# 修复方案：Stage 4 聚类 _load_graph NebulaGraph 性能优化

## 问题

`module_clustering.py:_load_graph()` 用 `MATCH (c:CodeEntity) RETURN ...` 和 `MATCH (c1)-[r]->(c2) WHERE type(r) IN [...]` 全扫描读图。这两条 Cypher 在 NebulaGraph 上是无索引起点的全图扫描，44828 节点 + 6 万关系 = 服务器端执行极慢（>10 分钟无响应）。

## 设计方案

**核心思路**：在 GraphStore ABC 新增两个批量读方法，NebulaGraph 用原生 nGQL 高效实现，Neo4j 用 Cypher MATCH 实现。`_load_graph` 不再直接写 Cypher，改为调抽象方法。

### 新增 ABC 方法

```python
# graph_store.py

def get_nodes_by_label(self, label: str, properties: list[str] | None = None) -> list[dict]:
    """读取指定 label 的全部节点。

    Args:
        label: 节点标签。
        properties: 需要读取的属性名列表（camelCase）。None 表示读全部属性。

    Returns:
        节点属性字典列表，每项至少含 'id'。
    """
    # 默认实现：Cypher MATCH
    prop_list = ", ".join(f"n.{p} AS {p}" for p in (properties or ["id"]))
    return self.query(f"MATCH (n:{label}) RETURN id(n) AS id, {prop_list}")

def get_edges_by_type(self, rel_types: list[str], node_label: str = "") -> list[dict]:
    """读取指定类型的全部关系（含 source/target ID）。

    Args:
        rel_types: 关系类型列表。
        node_label: 可选，限制端点节点的 label（用于性能优化）。

    Returns:
        关系字典列表，每项含 'source_id' 和 'target_id'。
    """
    # 默认实现：Cypher MATCH
    type_filter = "|".join(rel_types)
    label_filter = f":{node_label}" if node_label else ""
    cypher = (
        f"MATCH (a{label_filter})-[r:{type_filter}]->(b{label_filter}) "
        f"RETURN a.id AS source_id, b.id AS target_id"
    )
    return self.query(cypher)
```

### NebulaGraphStore 覆写

用 `LOOKUP ON` + `YIELD`（比 MATCH 高效，直接走 tag/edge 索引）：

```python
# nebula_store.py

def get_nodes_by_label(self, label: str, properties: list[str] | None = None) -> list[dict]:
    props = properties or ["id", "name", "filePath"]
    # LOOKUP ON <tag> 走 tag 索引，比 MATCH 全扫描高效
    prop_yield = ", ".join(f"vertex.{label}.{p} AS {p}" for p in props)
    # 注意: id(vertex) 是 NebulaGraph 取 VID 的函数
    ngql = f"LOOKUP ON `{label}` YIELD id(vertex) AS id, {prop_yield};"
    # 适配器不处理 LOOKUP，直接走 session.execute

def get_edges_by_type(self, rel_types: list[str], node_label: str = "") -> list[dict]:
    # NebulaGraph: 对每个 edge type 做 LOOKUP ON <edge_type> YIELD src(edge), dst(edge)
    all_edges = []
    for rt in rel_types:
        ngql = f"LOOKUP ON `{rt}` YIELD src(edge) AS source_id, dst(edge) AS target_id;"
        results = self._execute_raw(ngql)
        all_edges.extend(results)
    return all_edges
```

**关键技术点**：
1. `LOOKUP ON CodeEntity` 走 tag 索引扫描（之前 `nebula_schema.py` 建的 `idx_CodeEntity_name` 等），比 `MATCH` 全图扫描快几个数量级
2. `LOOKUP ON CALLS` 走 edge 索引，直接拿全部边
3. 注意 nGQL 中属性访问用 `vertex.Tag.prop` 格式
4. LOOKUP 不经过 CypherToNgqlAdapter，需要直接走 `session.execute()`

### _load_graph 改造

```python
# module_clustering.py

def _load_graph(self) -> tuple[dict[str, set[str]], dict[str, dict]]:
    self._logger.info("Loading graph from store...")

    # 1. 批量读实体
    entity_results = self._neo4j_store.get_nodes_by_label(
        "CodeEntity", ["id", "name", "filePath"]
    )
    self._logger.info("Loaded %d CodeEntity nodes", len(entity_results))

    entity_data = {r["id"]: {"name": r.get("name"), "file_path": r.get("filePath")} for r in entity_results}

    # 2. 批量读关系
    relation_results = self._neo4j_store.get_edges_by_type(
        ["CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"], "CodeEntity"
    )
    self._logger.info("Loaded %d edges", len(relation_results))

    # 3. 建邻接表（不变）
    ...
```

### 进度日志

在 `_load_graph` 各阶段加 info 日志（读实体、读关系、建邻接表、加虚拟边），消除大图下的"无日志卡死"错觉。

## 改动范围

| 文件 | 改动 |
|------|------|
| `store/graph_store.py` | 新增 `get_nodes_by_label` + `get_edges_by_type`（默认 Cypher 实现） |
| `store/nebula_store.py` | 覆写上述两个方法（用 LOOKUP ON） |
| `store/neo4j_store.py` | 无需改（用 ABC 默认实现）或按需覆写 |
| `pipeline/module_clustering.py` | `_load_graph` 改调抽象方法 + 加进度日志 |
| 测试 | unit test mock 改为 mock 新接口 |

## 风险

1. **LOOKUP ON 需要 tag/edge 索引**：已由 nebula_schema.py 初始化时创建（`CREATE TAG INDEX IF NOT EXISTS idx_CodeEntity_name ON CodeEntity(name(64))`）。但索引可能因 DDL 时序问题没建上（问题 #4），此时 LOOKUP 可能退化。需要降级处理：LOOKUP 失败 → 警告 → 降级回 MATCH。
2. **LOOKUP 属性名**：NebulaGraph 中 `LOOKUP ON CodeEntity YIELD vertex.CodeEntity.filePath` 需要 tag 上有 `filePath` 属性。需确认 schema 中属性名一致。
3. **LOOKUP 不走 CypherToNgqlAdapter**：需要直接走 `session.execute()`，绕过适配器。
