# 执行任务：Stage 4 聚类 NebulaGraph 性能优化

## 背景
_load_graph 的 MATCH 全扫描在 44828 实体的 NebulaGraph 上卡死。需要改用 NebulaGraph 原生高效查询。

## 关键技术约束
1. NebulaGraph 的 MATCH (c:CodeEntity) 不走索引，是全 tag 扫描 → 大图极慢
2. 现有索引：每个 tag 只有 name(64) 上的索引（nebula_schema.py:200-207）
3. NebulaGraph 的 LOOKUP ON tag WHERE tag.name != "" YIELD ... 会走 name 索引扫描全量
4. Edge 自带索引，LOOKUP ON edge_type YIELD src(edge), dst(edge) 可直接扫描全部边
5. SDK API: result.column_values("col_name")[i].as_string() / .as_int()

## 任务清单

### 任务 1: GraphStore ABC 新增批量读方法

**文件**: src/ontoagent/store/graph_store.py

在已有方法之后，新增两个非抽象方法（带默认 Cypher 实现）:

```python
def get_nodes_by_label(self, label: str, properties: list[str] | None = None) -> list[dict]:
    """批量读取指定 label 的全部节点。

    默认实现用 Cypher MATCH（Neo4j 高效）。NebulaGraph 子类应覆写为 LOOKUP ON。

    Args:
        label: 节点标签名。
        properties: 需要读取的属性名列表（camelCase）。None 表示只读 id 和 name。

    Returns:
        节点属性字典列表，每项至少含 'id'。
    """
    props = properties or ["id", "name"]
    prop_clause = ", ".join(f"n.{p} AS {p}" for p in props)
    return self.query(f"MATCH (n:{label}) RETURN id(n) AS id, {prop_clause}")

def get_edges_by_types(self, rel_types: list[str], node_label: str = "") -> list[dict]:
    """批量读取指定类型的全部关系。

    默认实现用 Cypher MATCH（Neo4j 高效）。NebulaGraph 子类应覆写为 LOOKUP ON edge_type。

    Args:
        rel_types: 关系类型列表（如 ['CALLS', 'IMPORTS']）。
        node_label: 可选，限制端点节点 label。

    Returns:
        关系字典列表，每项含 'source_id' 和 'target_id'。
    """
    type_filter = "|".join(rel_types)
    label_part = f":{node_label}" if node_label else ""
    cypher = (
        f"MATCH (a{label_part})-[r:{type_filter}]->(b{label_part}) "
        f"RETURN id(a) AS source_id, id(b) AS target_id"
    )
    return self.query(cypher)
```

### 任务 2: NebulaGraphStore 覆写批量读方法

**文件**: src/ontoagent/store/nebula_store.py

覆写 get_nodes_by_label 和 get_edges_by_types。用 _session_scope + session.execute 直接执行 nGQL（不走 CypherToNgqlAdapter）。

```python
def get_nodes_by_label(self, label: str, properties: list[str] | None = None) -> list[dict]:
    """NebulaGraph: 用 LOOKUP ON 走 tag 索引扫描全量节点。

    利用已有的 idx_{label}_name 索引，通过 WHERE name != "" 触发索引扫描。
    比 MATCH (c:label) 全 tag 扫描快几个数量级。
    """
    props = properties or ["id", "name"]
    # YIELD 中用 vertex.Tag.prop 格式访问属性
    prop_yield = ", ".join(f"vertex.`{label}`.`{p}` AS `{p}`" for p in props if p != "id")
    ngql = (
        f"LOOKUP ON `{label}` WHERE `{label}`.name != \"\" "
        f"YIELD id(vertex) AS id, {prop_yield};"
    )
    with self._session_scope() as session:
        result = session.execute(ngql)
        if not result.is_succeeded():
            # 降级到 MATCH（走 query/CypherToNgqlAdapter）
            logger.warning("[NebulaStore] LOOKUP ON %s failed, falling back to MATCH: %s", label, _safe_error_msg(result))
            props_clause = ", ".join(f"n.{p} AS {p}" for p in props)
            return self.query(f"MATCH (n:{label}) RETURN id(n) AS id, {props_clause}")
        return _resultset_to_dicts(result)

def get_edges_by_types(self, rel_types: list[str], node_label: str = "") -> list[dict]:
    """NebulaGraph: 对每个 edge type 做 LOOKUP ON 扫描全部边。

    NebulaGraph edge 自带索引，LOOKUP ON edge_type 可直接遍历全部边。
    """
    all_edges: list[dict] = []
    with self._session_scope() as session:
        for rt in rel_types:
            ngql = f"LOOKUP ON `{rt}` YIELD src(edge) AS source_id, dst(edge) AS target_id;"
            result = session.execute(ngql)
            if not result.is_succeeded():
                logger.warning("[NebulaStore] LOOKUP ON edge %s failed: %s", rt, _safe_error_msg(result))
                continue
            if not result.is_empty():
                all_edges.extend(_resultset_to_dicts(result))
    return all_edges
```

注意:
- LOOKUP 的 WHERE 条件用 name != "" 触发 name 索引扫描全量
- src(edge)/dst(edge) 返回的是 VID 字符串
- _resultset_to_dicts 已正确处理 column_values
- 如果 LOOKUP 失败（索引没建上），降级到 MATCH

### 任务 3: _load_graph 改调抽象方法 + 加进度日志

**文件**: src/ontoagent/pipeline/module_clustering.py

把 _load_graph 的两条 Cypher 查询替换为调用 get_nodes_by_label 和 get_edges_by_types。各阶段加 info 日志。

当前代码 (L80-102):
```python
entity_cypher = """
    MATCH (c:CodeEntity)
    RETURN c.id AS id, c.name AS name, c.file_path AS file_path
"""
entity_results = self._neo4j_store.query(entity_cypher)
...
relation_cypher = """
    MATCH (c1:CodeEntity)-[r]->(c2:CodeEntity)
    WHERE type(r) IN ['CALLS', 'IMPORTS', 'EXTENDS', 'IMPLEMENTS']
    RETURN c1.id AS source, c2.id AS target
"""
relation_results = self._neo4j_store.query(relation_cypher)
```

替换为:
```python
self._logger.info("Loading CodeEntity nodes from store...")
entity_results = self._neo4j_store.get_nodes_by_label(
    "CodeEntity", ["id", "name", "filePath"]
)
self._logger.info("Loaded %d CodeEntity nodes", len(entity_results))
...
self._logger.info("Loading structural edges (CALLS/IMPORTS/EXTENDS/IMPLEMENTS)...")
relation_results = self._neo4j_store.get_edges_by_types(
    ["CALLS", "IMPORTS", "EXTENDS", "IMPLEMENTS"], "CodeEntity"
)
self._logger.info("Loaded %d edges", len(relation_results))
```

注意: entity_results 中的 key 可能是 "filePath"（camelCase），需要在构建 entity_data 时映射到 "file_path"。

### 任务 4: 测试更新

**文件**: tests/unit/pipeline/test_module_clustering.py

现有测试 mock self._neo4j_store.query()。改为 mock get_nodes_by_label 和 get_edges_by_types。

测试用例:
- mock get_nodes_by_label 返回 [{"id": "e1", "name": "foo", "filePath": "/a/b.py"}, ...]
- mock get_edges_by_types 返回 [{"source_id": "e1", "target_id": "e2"}, ...]
- 验证 _load_graph 正确构建邻接表和 entity_data

确保已有的 TestLoadGraph 全部测试改用新 mock 接口。

## 执行约束
- 不要碰 conftest.py
- 不要修改 module_clustering.py 中 save_modules / detect_modules / _label_propagation 等方法
- 只改 _load_graph 方法
- 每个任务后运行相关测试
