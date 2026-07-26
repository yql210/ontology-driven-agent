# Phase 3 实施计划：内部模块 Cypher 适配

> **给 Claude Code 执行。TDD 方式。**
> **项目根目录：/opt/data/workspace/ontology-driven-agent**
> **策略：在 NebulaGraphStore 内部做 Cypher→nGQL 自动转换，上层代码最小改动**

## 核心策略

49 条内部 Cypher 要改成 nGQL，逐条手工改太痛苦且易错。更好的方式是：
**在 NebulaGraphStore.query() 内部加一层 CypherToNgqlAdapter**，自动处理最高频的差异。

### 自动转换规则（覆盖 80%+ 的 Cypher）

| Cypher 模式 | nGQL 转换 | 覆盖率 |
|---|---|---|
| `n.field` → `n.Tag.field` | 从 MATCH 子句的 `:Tag` 提取变量→Tag 映射，补全前缀 | 14 条 |
| `labels(n)` → `tags(n)` | 简单字符串替换 | 8 条 |
| `labels(n)[0]` → `tags(n)[0]` | 同上 | 含在上面 |
| `size(labels(n))` → `size(tags(n))` | 同上 | |
| `n.id = $param` → `id(n) == $param` | 特殊处理 id 属性 | 3 条 |
| `= $param` → `== $param` | WHERE 中的单等号改双等号 | 若干 |
| `{name: $name}` inline → `WHERE x.Tag.name == $name` | inline property matching 改 WHERE | 5 条 |
| `startNode(r).id` → 用 pattern 变量替代 | 重写边查询 | 3 条 |
| `MERGE` → `UPSERT VERTEX` | 写操作转换 | 3 条 |

**不自动转换的（需上层改代码）：**
- `MATCH path = ...` + `length(path)` / `nodes(p)`（path 变量的返回格式不同）— 2 条
- `SET n.x = true`（写操作通过 Cypher 而非 store API）— 3 条，改为调 store API
- `collect(DISTINCT ...)` — 1 条，简单改写

## 文件清单

### 新建文件
1. `src/ontoagent/store/cypher_adapter.py` — CypherToNgqlAdapter 自动转换器
2. `tests/unit/test_cypher_adapter.py` — 适配器测试（纯单元测试，无外部依赖）

### 修改文件
1. `src/ontoagent/store/nebula_store.py` — query() 方法调用 CypherToNgqlAdapter
2. `src/ontoagent/store/graph_store.py` — 新增 `update_node_property()` 语义化方法（替代 SET 写操作）
3. `src/ontoagent/execution/functions/builtin.py` — call_tree Cypher 补 Tag 前缀
4. `src/ontoagent/execution/functions/trace_business_impact.py` — path 变量重写
5. `src/ontoagent/execution/functions/check_compliance.py` — 关系类型大写
6. `src/ontoagent/pipeline/incremental_updater.py` — SET 写操作改为语义化 API
7. `src/ontoagent/agent/_helpers.py` — get_neo4j() 改为 get_graph_store()（走 factory）
8. `src/ontoagent/pipeline/builder.py` — _get_graph_store() 改走 factory

---

## Task 1: CypherToNgqlAdapter（核心）

### `src/ontoagent/store/cypher_adapter.py`

```python
class CypherToNgqlAdapter:
    """将 OntoAgent 内部使用的 Cypher 子集自动转换为 nGQL。

    处理最高频的差异：
    1. 属性访问补全 Tag 前缀（从 MATCH :Tag 提取变量→Tag 映射）
    2. labels(n) → tags(n)
    3. inline property matching {name: $val} → WHERE 子句
    4. = → ==（WHERE 中的比较）
    5. startNode(r)/endNode(r) → pattern 变量

    不处理的（需上层代码改）：
    - MATCH path = ... + length(path)/nodes(p)
    - SET 写操作（应走 store API）
    - MERGE（走 merge_node API）
    """

    def adapt(self, cypher: str, params: dict | None = None) -> str:
        """转换 Cypher → nGQL。"""
        result = cypher
        result = self._fix_labels(result)
        result = self._fix_property_access(result)
        result = self._fix_inline_matching(result)
        result = self._fix_equality(result)
        result = self._fix_start_end_node(result)
        result = self._fix_reserved_words(result)
        return result

    def _build_var_tag_map(self, cypher: str) -> dict[str, str]:
        """从 MATCH (var:Tag) 提取 变量名→Tag名 映射。"""
        # 匹配 (var:Tag) 或 (var:Tag1:Tag2)
        # 也匹配无变量的 ()-[]->(:Tag)
        ...

    def _fix_labels(self, cypher: str) -> str:
        """labels(n) → tags(n)"""
        return cypher.replace("labels(", "tags(")

    def _fix_property_access(self, cypher: str) -> str:
        """n.field → n.Tag.field（用 var_tag_map 补全）"""
        # 对每个 var:tag 对，把 var.field（但不是 var.tag.field）改为 var.tag.field
        # 特殊处理 var.id → id(var)
        ...

    def _fix_inline_matching(self, cypher: str) -> str:
        """(n {name: $val}) → (n) WHERE n.Tag.name == $val"""
        ...

    def _fix_equality(self, cypher: str) -> str:
        """WHERE 中的 = → ==（但 SET = 不改）"""
        ...

    def _fix_start_end_node(self, cypher: str) -> str:
        """startNode(r).id → 需要 MATCH (a)-[r]->(b) 然后 id(a)"""
        # 如果有 startNode/endNode，需确保 pattern 中有绑定变量
        ...

    def _fix_reserved_words(self, cypher: str) -> str:
        """属性名是保留字时加反引号"""
        ...
```

### 测试（`tests/unit/test_cypher_adapter.py`）

每个转换规则都要测：
- test_labels_to_tags
- test_property_access_gets_tag_prefix
- test_property_access_id_becomes_id_function
- test_inline_matching_converted_to_where
- test_equality_single_to_double_equals
- test_start_end_node_replaced
- test_multiple_tags_in_match
- test_no_match_clause_passes_through
- test_set_clause_not_affected_by_equality_fix
- test_reserved_words_get_backticks

---

## Task 2: NebulaGraphStore.query() 集成 adapter

修改 `nebula_store.py` 的 query() 方法：

```python
def query(self, ngql_or_cypher: str, params: dict | None = None) -> list[dict]:
    """执行查询。如果是 Cypher 语法，自动通过 CypherToNgqlAdapter 转换。"""
    adapter = CypherToNgqlAdapter()
    ngql = adapter.adapt(ngql_or_cypher, params)
    # 执行 nGQL...
```

---

## Task 3: 上层写操作改走语义化 API

### incremental_updater.py 的 SET 操作

当前：
```python
cypher = "MATCH (n:ConceptEntity) WHERE n.id IN $ids SET n.needs_reextraction = true RETURN count(n) AS count"
result = graph_store.query(cypher, {"ids": impacted_concept_ids})
```

改为：
```python
for node_id in impacted_concept_ids:
    graph_store.update_node_property(node_id, "needs_reextraction", True)
```

在 GraphStore ABC 新增：
```python
@abstractmethod
def update_node_property(self, node_id: str, key: str, value: Any) -> bool:
    """更新单个节点属性。"""
```

Neo4jStore 实现：用 MERGE + SET
NebulaGraphStore 实现：用 UPDATE VERTEX

---

## Task 4: 其他小改动

### builtin.py call_tree
```python
# 当前
f"MATCH (caller:CodeEntity)-[:CALLS*1..{depth}]->(callee:CodeEntity) WHERE caller.id = $entity_id ..."
# adapter 会自动处理，但如果要显式写：
f'MATCH (caller:CodeEntity)-[:CALLS*1..{depth}]->(callee:CodeEntity) WHERE id(caller) == $entity_id RETURN callee.CodeEntity.name AS name'
```

### check_compliance.py
```python
# 当前用了小写关系类型 :processes_data → 改为大写
"MATCH (c:CodeEntity {id: $target_id})-[:PROCESSES_DATA]->(d:DataAsset)-[:GOVERNED_BY]->(ci:ComplianceItem) ..."
```

### _helpers.py / builder.py 改走 factory
```python
# _helpers.py
def get_graph_store() -> GraphStore:
    """获取图存储后端（按配置选择 Neo4j 或 NebulaGraph）。"""
    ...
```

---

## 实施顺序

1. Task 1: CypherToNgqlAdapter + 测试（核心，~20 个测试用例）
2. Task 2: NebulaGraphStore.query() 集成 adapter
3. Task 3: update_node_property API + incremental_updater 改造
4. Task 4: 小改动（_helpers/builder/check_compliance/builtin）
5. ruff + 全量回归

## 重要约束

1. **不要改 Neo4jStore** — adapter 只在 NebulaGraphStore 中调用
2. **Neo4j 后端不受影响** — Cypher 原样传给 Neo4j
3. **adapter 是 best-effort** — 无法转换的查询记录 warning，让调用方知道
4. **不处理 graph.py 的复杂查询** — graph.py 的 path 变量 + nodes(p) 需要单独处理（可放 Phase 3b）
