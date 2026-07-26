# Phase 1 实施计划：NebulaGraphStore + Schema + Factory

> **给 Claude Code 执行。TDD 方式。**
> **项目根目录：/opt/data/workspace/ontology-driven-agent**

## 背景

OntoAgent 当前只支持 Neo4j。生产环境需要支持 NebulaGraph。本 Phase 实现 `NebulaGraphStore`（GraphStore 的 NebulaGraph 实现）+ `NebulaSchemaInitializer`（自动 DDL）+ `GraphStoreFactory`（按配置选择后端）。

**POC 已验证可行**，关键事实：
- NebulaGraph 3.7.0，连接 124.221.243.142:9669，root/nebula
- VID 类型 FIXED_STRING(36) 匹配 OntoAgent UUID
- 属性访问格式 `v.TagName.fieldName`（不是 `v.fieldName`）
- UPSERT VERTEX 替代 MERGE（支持 WHEN 条件）
- 无 UNWIND，批量写入需循环
- Space 创建后需等待 ~20s
- `timestamp`/`path` 是保留字

## 文件清单

### 新建文件
1. `src/ontoagent/store/nebula_store.py` — NebulaGraphStore 实现
2. `src/ontoagent/store/nebula_schema.py` — NebulaSchemaInitializer
3. `src/ontoagent/store/factory.py` — GraphStoreFactory
4. `tests/unit/test_nebula_store.py` — 单元测试（mock nebula session）
5. `tests/unit/test_nebula_schema.py` — Schema 初始化测试

### 修改文件
1. `src/ontoagent/store/graph_store.py` — 可能新增抽象方法（如 execute_shape_query）
2. `src/ontoagent/config.py` — 新增 graph_backend 配置
3. `pyproject.toml` — nebula3-python 已在 venv（手动添加到 dependencies）

---

## Task 1: NebulaSchemaInitializer（`store/nebula_schema.py`）

### 职责
从 `domain/schema.py` 的 `VALID_ENTITY_LABELS`（13 个）和 `RELATION_TYPE_TO_NEO4J`（26 个）自动生成 NebulaGraph DDL。

### 接口设计

```python
class NebulaSchemaInitializer:
    """从 OntoAgent schema 自动创建 NebulaGraph Space + Tag + Edge + 索引。"""

    def __init__(self, session: Session, space_name: str = "ontoagent"):
        """session 是 nebula3 的 Session 对象。"""

    def initialize(self, vid_type: str = "FIXED_STRING(36)") -> bool:
        """创建 Space + Tag + Edge + 索引。幂等（IF NOT EXISTS）。
        返回是否成功。DDL 是异步的，调用方需等待生效。
        """

    def ensure_space(self, vid_type: str = "FIXED_STRING(36)") -> bool:
        """仅创建/确认 Space 存在。"""

    def create_tags(self) -> list[str]:
        """为 13 个实体创建 Tag。返回 DDL 语句列表。
        属性从 _LABEL_TO_DATACLASS 反射获取（用 entity_field_names）。
        所有属性暂用 string 类型（POC 验证策略）。
        保留字（timestamp 等）用反引号包裹。
        """

    def create_edges(self) -> list[str]:
        """为 26 个关系创建 Edge type（无属性）。"""

    def create_indexes(self) -> list[str]:
        """为每个 Tag 的 name 属性创建索引。"""
```

### 关键实现点
- `entity_field_names(label)` 从 `domain/schema.py` 反射获取字段名
- 保留字检测：`timestamp`、`path`、`source`、`rank` 等用反引号
- 所有 Tag 属性用 `string` 类型（简化，POC 验证策略）
- Space 创建后 `SUBMIT JOB` + `sleep`

### 测试（`tests/unit/test_nebula_schema.py`）
- test_creates_space_with_correct_vid_type
- test_creates_all_13_tags
- test_creates_all_26_edges
- test_timestamp_field_uses_backticks
- test_idempotent（IF NOT EXISTS）
- 所有测试用 mock session，不连真实 NebulaGraph

---

## Task 2: NebulaGraphStore（`store/nebula_store.py`）

### 职责
实现 `GraphStore` ABC 的全部抽象方法，使用 nebula3-python 客户端。

### 接口设计

```python
class NebulaGraphStore(GraphStore):
    """NebulaGraph 实现。使用 ConnectionPool + Session 管理。"""

    def __init__(
        self,
        host: str,
        port: int = 9669,
        user: str = "root",
        password: str = "nebula",
        space: str = "ontoagent",
        max_connection_pool_size: int = 10,
    ):
        """初始化连接池。不在这里 USE SPACE——每次 _get_session 时执行。"""

    def _get_session(self) -> Session:
        """获取 session 并执行 USE SPACE。用 contextmanager 管理（_session_scope）。"""

    @contextmanager
    def _session_scope(self):
        """session 生命周期管理：获取 → USE SPACE → yield → release。"""

    def close(self) -> None:
        """关闭连接池。"""

    # ---- GraphStore ABC 实现 ----

    def merge_node(self, label: str, properties: dict) -> dict:
        """UPSERT VERTEX ON {label} "{vid}" SET k1=v1, k2=v2
        properties 必须含 'id'，作为 VID。
        属性 key 转 camelCase（复用 _keys_to_camel_case 逻辑）。
        """

    def get_node(self, node_id: str) -> dict | None:
        """FETCH PROP ON * "{node_id}" YIELD properties(vertex)
        返回 dict 或 None。
        需把 NebulaGraph 返回格式转为 OntoAgent 格式（camelCase keys）。
        """

    def delete_node(self, node_id: str) -> bool:
        """DELETE VERTEX "{node_id}" WITH EDGE"""

    def merge_relation(
        self, source_id: str, target_id: str, rel_type: str,
        properties: dict | None = None,
        *, source_label: str = "", target_label: str = "",
    ) -> dict:
        """DELETE EDGE + INSERT EDGE（幂等 upsert）。
        rel_type 转大写（RELATION_TYPE_TO_NEO4J 映射）。
        固定 rank=0 保证唯一。
        """

    def delete_relation(self, source_id: str, target_id: str, rel_type: str) -> bool:
        """DELETE EDGE {rel_type} "{source_id}"->"{target_id}"@0"""

    def get_relations(
        self, source_id: str | None = None,
        target_id: str | None = None, rel_type: str | None = None,
    ) -> list[dict]:
        """MATCH (a)-[r]->(b) WHERE ... RETURN ...
        根据 source_id/target_id/rel_type 组合 WHERE 条件。
        rel_type 转大写。
        """

    def query(self, ngql: str, params: dict | None = None) -> list[dict]:
        """执行原生 nGQL 查询，返回 list[dict]。
        params：如果提供，做简单字符串替换（NebulaGraph 不支持参数化查询）。
        """

    def cleanup_orphan_nodes(self) -> int:
        """MATCH (n) WHERE NOT (n)--() DELETE n
        NebulaGraph 中每个点必有 Tag，所以查找无边的点。
        """
```

### 关键实现点
- `_session_scope` contextmanager：try/finally 确保 session.release()
- `merge_node`：VID 用 properties["id"]，UPSERT VERTEX SET
- `get_node`：FETCH PROP 返回的是 NebulaGraph 格式，需转 dict
- `query` 返回 `list[dict]`（与 Neo4jStore.query 保持一致接口）
- 属性 key camelCase 转换：复用 neo4j_store.py 的 `_keys_to_camel_case`（提取到共享工具或复制）
- 关系类型映射：用 `RELATION_TYPE_TO_NEO4J` 做小写→大写转换
- `merge_relations_batch`：循环调用 merge_relation（无 UNWIND）

### 测试（`tests/unit/test_nebula_store.py`）
全部用 mock session：
- test_merge_node_uses_upsert_with_correct_vid
- test_get_node_returns_none_when_not_found
- test_merge_relation_uses_delete_then_insert
- test_delete_node_with_edge
- test_get_relations_filters_by_source_id
- test_query_returns_list_of_dict
- test_session_scope_releases_on_exception
- test_label_validation（防注入）
- test_merge_node_missing_id_raises

---

## Task 3: GraphStoreFactory（`store/factory.py`）

### 接口

```python
def create_graph_store(config: OntoAgentConfig) -> GraphStore:
    """根据 config.graph_backend 选择后端。
    "neo4j" → Neo4jGraphStore
    "nebula" → NebulaGraphStore
    """

def create_graph_store_from_env() -> GraphStore:
    """从环境变量创建。读取 ONTOAGENT_GRAPH_BACKEND。"""
```

### 测试
- test_factory_returns_neo4j_store
- test_factory_returns_nebula_store
- test_factory_invalid_backend_raises

---

## Task 4: Config 修改（`config.py`）

在 OntoAgentConfig 中新增：
- `graph_backend: str = "neo4j"`  # "neo4j" | "nebula"
- `nebula_host: str = "127.0.0.1"`
- `nebula_port: int = 9669`
- `nebula_user: str = "root"`
- `nebula_password: str = "nebula"`
- `nebula_space: str = "ontoagent"`

从环境变量读取：ONTOAGENT_GRAPH_BACKEND, ONTOAGENT_NEBULA_HOST 等。

---

## 实施顺序（TDD）

1. **先写测试** → 跑测试（全红）
2. **写实现** → 跑测试（全绿）
3. **ruff check + ruff format**
4. **pyright 检查**

### 每个 Task 的 TDD 循环
Task 1 (Schema) → Task 2 (Store) → Task 3 (Factory) → Task 4 (Config)

---

## 编码规范（来自 CLAUDE.md）

- `from __future__ import annotations` 头部
- 类型注解必须（`X | None`、`list[X]`）
- f-string，行宽 120
- `@dataclass` + `__post_init__` 校验
- `logging` 不用 `print`
- 提交前：ruff check + ruff format + pyright 全通过
- 测试 markers: unit（无外部依赖）

---

## NebulaGraph nGQL 语法速查（给 CC 参考）

```ngql
# 创建
CREATE SPACE IF NOT EXISTS ontoagent (vid_type=FIXED_STRING(36));
CREATE TAG IF NOT EXISTS CodeEntity(name string, filePath string);
CREATE EDGE IF NOT EXISTS CALLS();
CREATE TAG INDEX IF NOT EXISTS idx ON CodeEntity(name(64));

# 写入
INSERT VERTEX CodeEntity(name, filePath) VALUES "uuid-vid":("name", "/path");
INSERT EDGE CALLS() VALUES "src-vid"->"dst-vid":();
UPSERT VERTEX ON CodeEntity "uuid-vid" SET name = "new_name" WHEN name == "old";

# 查询（属性访问必须带 Tag 前缀！）
MATCH (n:CodeEntity) WHERE n.CodeEntity.name == "xxx" RETURN n.CodeEntity.filePath;
MATCH (a)-[r:CALLS]->(b) RETURN a.CodeEntity.name, b.CodeEntity.name;
MATCH (n)-[:CALLS*1..3]->(callee) WHERE id(n) == "vid" RETURN callee.CodeEntity.name;

# 删除
DELETE VERTEX "vid" WITH EDGE;
DELETE EDGE CALLS "src-vid"->"dst-vid"@0;

# 获取节点
FETCH PROP ON CodeEntity "vid" YIELD properties(vertex);
FETCH PROP ON * "vid" YIELD id(vertex), properties(vertex);
```

---

## 重要约束

1. **不要修改 Neo4jStore** — 保持不变，NebulaGraphStore 是独立实现
2. **不要修改业务代码** — 只改 store 层 + config
3. **所有测试用 mock** — 单元测试不连真实 NebulaGraph
4. **camelCase 属性** — 与 Neo4jStore 保持一致（_keys_to_camel_case）
5. **保留字反引号** — timestamp, path, source, rank 等
6. **session 必须 release** — contextmanager + try/finally
