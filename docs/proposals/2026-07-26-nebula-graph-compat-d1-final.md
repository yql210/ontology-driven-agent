# OntoAgent 图数据库多后端适配方案 D1-Final

> **状态：最终方案，待用户确认后进入实施**
> **日期：2026-07-26**
> **审查历史：经 3 轮 Claude Code 深度技术审查（共 ~500s 审查时间），全部关键判断已实查验证**
> **前置决策：生产环境强制 NebulaGraph；LLM 必须保留动态图查询能力**

---

## 一、方案演进脉络

| 版本 | 核心策略 | 审查结论 |
|---|---|---|
| V2 | nGQL 全适配 + 编译器 | 工作量大（13-21 天），ShapeEvaluator 性能退化 |
| C | CQRS 内存图 | query() 复杂度被严重低估，不可直接落地 |
| D1 原版 | 多 Prompt 模板 | 方向正确但兼容率虚高（声称 90% 实际 0%） |
| **D1-Final** | **多 Prompt + 后端适配（修订版）** | **整合 3 轮审查的 10 项修订，工作量 12-15 天** |

---

## 二、事实基线（3 轮审查实查验证）

### 2.1 OntoAgent 真实规模（非 CLAUDE.md 的过时数据）

| 维度 | 正确值 | 证据 |
|---|---|---|
| 实体标签数 | **13** | `schema.py:506-522` VALID_ENTITY_LABELS |
| 关系类型数 | **26** | `schema.py:582-644` VALID_RELATION_TYPES |
| 含 Cypher 的文件 | 22 个 | grep 实测 |
| Cypher 语句总数 | ~119 条 | grep 实测 |
| 直接 import Neo4jGraphStore | 15 处 | grep 实测 |

### 2.2 Cypher → NebulaGraph 兼容性（实查验证，修正后）

**关键修正：直接兼容率 = 0%。** 所有查询都必须改写。

原因：NebulaGraph 3.0+ 强制属性访问格式 `v.TagName.fieldName`（官方文档验证），而 OntoAgent 所有 Cypher 用 `n.field` 裸访问。

| 查询类别 | 数量 | 兼容性 | 改写方式 |
|---|---|---|---|
| 简单节点查找（MATCH + WHERE + RETURN） | ~15 条 | ⚠️ 需改写 | 补 tag 前缀 + `=` 改 `==` |
| 变长路径遍历（`*1..N`） | 7 处 | ⚠️ 需改写 | 语法兼容，但属性访问 + path 变量需改 |
| 聚合/统计（`labels()`、`count()`） | ~8 条 | ⚠️ 需改写 | `labels()` → `tags()`，格式调整 |
| 边查询（`startNode(r)` / `endNode(r)`） | 3 处 | ❌ 不支持 | **必须重写**：改用 `MATCH (a)-[r]->(b) RETURN id(a), id(b)` |
| Inline property matching `{name: $v}` | ~5 处 | ⚠️ 需改写 | 改用 WHERE 子句 |
| `properties(n)` 返回 | 2 处 | ❌ 格式不同 | 需后处理或改写 |
| 写操作（MERGE/UNWIND/SET/DELETE） | ~23 条 | ❌ 不支持 | **必须重写**：MERGE→UPSERT，UNWIND→循环 |

### 2.3 NebulaGraph vs Neo4j 关键差异（全部实查文档验证）

| 维度 | Neo4j | NebulaGraph 3.x | 影响 |
|---|---|---|---|
| 属性访问 | `n.field` | **`n.Tag.field`**（强制） | 所有查询都要改 |
| 相等比较 | `=` | `==` | WHERE 子句要改 |
| 标签函数 | `labels(n)` | `tags(n)`（返回格式不同） | 聚合查询要改 |
| 边端点 | `startNode(r)` | **不存在**，用 `id(a)` in pattern | graph.py 3 处重写 |
| MERGE | 原生 | **无**，用 UPSERT VERTEX | 写入路径重写 |
| UNWIND 批量 | 原生 | **不支持** | 批量写入改循环 |
| Schema | 无模式 | **强 Schema**（必须 CREATE TAG/EDGE） | 需 Schema 初始化器 |
| Space | 无 | **必须 USE SPACE** | Store 需管理 Space |
| 事务 | ACID | **无事务**（自动提交） | 批量写入无原子性 |
| Session | driver+session（池内置） | **ConnectionPool + Session（手动 release）** | 连接管理重写 |
| VID | 自动/属性 | **必须指定**（FIXED_STRING 或 INT64） | UUID 可作 VID |

---

## 三、方案架构（D1-Final）

### 3.1 核心策略：三层分离

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: Prompt 层（查询语言差异隔离）                │
│                                                      │
│  开发环境: prompt_cypher.yaml → LLM 生成 Cypher       │
│  生产环境: prompt_ngql.yaml   → LLM 生成 nGQL         │
│  (Schema-aware + Few-shot + 语法规则三重保障)          │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  Layer 2: GraphStore 抽象层（统一接口）                │
│                                                      │
│  query(query_string, params) → list[dict]            │
│  merge_node / merge_relation / get_relations / ...   │
│                                                      │
│  ┌─────────────┐    ┌──────────────────┐             │
│  │ Neo4jStore  │    │ NebulaGraphStore │             │
│  │ (Cypher)    │    │ (nGQL)           │             │
│  │ driver+pool │    │ ConnPool+Session │             │
│  └─────────────┘    └──────────────────┘             │
└─────────────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  Layer 3: 内部模块适配（固定查询改写）                  │
│                                                      │
│  ShapeEvaluator: PathCompiler 传递 Tag 上下文          │
│  graph.py: 逐条重写（startNode→pattern 重写）          │
│  批量写入: UNWIND→循环 UPSERT                          │
└─────────────────────────────────────────────────────┘
```

### 3.2 数据流

**LLM 动态查询路径（graph_query 工具）：**
```
配置 GRAPH_BACKEND=nebula
→ 加载 prompt_ngql.yaml（含 Schema + Few-shot + 语法规则）
→ LLM 生成 nGQL 查询字符串
→ graph_query(query) → tool_gateway 校验（NebulaGraph 关键字）
→ NebulaGraphStore.query() → 执行 → 返回 list[dict]
```

**内部模块查询路径（ShapeEvaluator 等）：**
```
ShapeEvaluator 构建 ShapeQuery（含 Tag 上下文）
→ NebulaGraphStore.execute_shape_query(ShapeQuery)
→ 内部编译为 nGQL（补全 tag 前缀）
→ 执行 → 返回 list[dict]
```

**写入路径：**
```
merge_node(label, properties)
→ NebulaGraphStore.merge_node()
→ UPSERT VERTEX ON {tag} {vid} SET ...
（批量：循环 UPSERT，非 UNWIND）
```

---

## 四、10 项关键修订（CC 审查产出，全部纳入）

### 修订 1：修正兼容率描述

方案中所有"MATCH 90% 兼容"修正为"0% 直接兼容，55% 需改写，20% 需重写"。

### 修订 2：Space 管理

`NebulaGraphStore.__init__` 中执行 `USE <space_name>`。每次 query 确保 session 在正确 space 中。

```python
class NebulaGraphStore(GraphStore):
    def __init__(self, host, port, user, password, space_name):
        self._pool = ConnectionPool()
        self._pool.init([(host, port)], config)
        self._space = space_name

    def _get_session(self):
        session = self._pool.get_session(self._user, self._password)
        session.execute(f'USE {self._space}')
        return session
```

### 修订 3：Session 池设计

nebula-python 的 Session 非线程安全、必须手动 release。设计 contextmanager：

```python
@contextmanager
def _session_scope(self):
    session = self._get_session()
    try:
        yield session
    finally:
        session.release()
```

每次 query 获取/释放 session，不复用为单例。

### 修订 4：重写批量写入路径

`merge_nodes_batch` / `merge_relations_batch` 从 UNWIND 改为循环 UPSERT：

```python
def merge_nodes_batch(self, label, properties_list, batch_size=200):
    for props in properties_list:
        vid = props["id"]
        self._upsert_vertex(label, vid, props)

def _upsert_vertex(self, tag, vid, props):
    set_clauses = ", ".join(f"{k} = ${k}" for k in props if k != "id")
    ngql = f'UPSERT VERTEX ON {tag} "{vid}" SET {set_clauses}'
    with self._session_scope() as session:
        session.execute(ngql, props)
```

**性能注意：** 批量写入性能下降 5-10 倍。POC 阶段需 benchmark，如不可接受则考虑用 `INSERT VERTEX ... VALUES` 批量语法（但不更新已有数据）。

### 修订 5：ShapeEvaluator / PathCompiler 接口修改

**问题：** PathCompiler 生成的 Cypher 中 `collected.fieldName` 和 `n.id` 需要 Tag 前缀，但起始节点 Tag 上下文没传递。

**修改：** `ShapeEvaluator.evaluate()` 已知 entity 的 labels，传入 `_build_query()`：

```python
# shape_evaluator.py 修改
def _build_query(self, shape, source_label: str) -> str:
    match_clause, _ = self._compiler.compile(shape.path)
    # source_label 用于补全 n.Tag.id
    return f"{match_clause} WHERE n.{source_label}.id == $entity_id RETURN collected.{shape.path.target_label}.{field_name} AS val"
```

### 修订 6：graph.py 逐条重写

graph.py 的所有查询逐条改写（单独分配 2-3 天）：

| 原查询 | 重写为 |
|---|---|
| `startNode(r).id AS source` | `MATCH (a)-[r]->(b) ... RETURN id(a) AS source` |
| `endNode(r).id AS target` | `RETURN id(b) AS target` |
| `properties(n) AS props` | `RETURN properties(n) AS props`（后处理格式） |
| `n.entity_type` | `n.{Tag}.entityType`（需按 label 补全） |
| `center {name: $name}` | `WHERE center.{Tag}.name == $name` |

### 修订 7：强化 LLM Prompt

从"列 4 条差异"升级为**三重保障**：

1. **Schema-aware**：注入完整 Tag/Edge schema
   ```yaml
   schema:
     tags:
       CodeEntity: [name, filePath, entityType, lines, ...]
       ConceptEntity: [name, description, ...]
       # ... 13 个 Tag 的完整属性列表
     edges:
       CALLS: []
       CONTAINS: []
       # ... 26 种 Edge
   ```

2. **Few-shot 示例（10+ 条）**：覆盖主要查询模式
   ```yaml
   examples:
     - query: |
         MATCH (a)-[:CALLS]->(b)
         WHERE a.CodeEntity.name CONTAINS "xxx"
         RETURN a.CodeEntity.name, b.CodeEntity.name
     - query: |
         MATCH (n:CodeEntity)-[:CALLS*1..3]->(callee)
         WHERE n.CodeEntity.id == "uuid-here"
         RETURN callee.CodeEntity.name
   ```

3. **语法预检**：`graph_query` 工具内正则检测
   ```python
   def validate_ngql(query: str) -> tuple[bool, str]:
       # 检测 v.field 裸属性访问（应含 tag 前缀）
       if re.search(r'\b\w+\.\w+\s*==', query) and not re.search(r'\w+\.\w+\.\w+', query):
           return False, "属性访问需要 Tag 前缀: v.TagName.fieldName"
       return True, "ok"
   ```

### 修订 8：更新 tool_gateway 写操作关键字

`tool_gateway.py` 的关键字列表增加 NebulaGraph 写操作：

```python
_NEBULA_WRITE_KEYWORDS = [
    "INSERT VERTEX", "INSERT EDGE", "UPSERT VERTEX", "UPSERT EDGE",
    "DELETE VERTEX", "DELETE EDGE", "UPDATE VERTEX", "UPDATE EDGE",
    "CREATE TAG", "CREATE EDGE", "ALTER TAG", "ALTER EDGE",
    "DROP TAG", "DROP EDGE", "CREATE SPACE", "DROP SPACE",
]
```

### 修订 9：事务补偿设计

批量写入失败时的清理策略：

```python
def merge_nodes_batch(self, label, properties_list):
    written_vids = []
    try:
        for props in properties_list:
            self._upsert_vertex(label, props["id"], props)
            written_vids.append(props["id"])
    except Exception as e:
        # 补偿：删除已写入的节点
        for vid in written_vids:
            self._delete_vertex_safe(vid)
        raise
```

### 修订 10：工作量修正

| Phase | V1 估算 | Final 估算 | 说明 |
|---|---|---|---|
| Phase 0: POC | 未规划 | **1-2 天** | 新增：搭 NebulaGraph 实例 + 验证关键查询 + benchmark |
| Phase 1: NebulaGraphStore | 2 天 | **4 天** | Space + Session 池 + UPSERT + 批量重写 |
| Phase 2: Prompt 模板 | 1 天 | **2 天** | Schema-aware + Few-shot + 语法预检 |
| Phase 3: 内部适配 | 2 天 | **4-5 天** | ShapeEvaluator 接口改 + graph.py 逐条重写 |
| Phase 4: 集成测试 | 1 天 | **2-3 天** | L3 运行时评估 + 性能 benchmark |
| **总计** | **6 天** | **12-15 天** | |

---

## 五、NebulaSchemaInitializer 设计（新增）

从 `schema.py` 自动生成 NebulaGraph DDL：

```python
class NebulaSchemaInitializer:
    def initialize(self, space_name: str):
        # 1. CREATE SPACE IF NOT EXISTS
        self._execute(f'CREATE SPACE IF NOT EXISTS {space_name} '
                      f'(vid_type=FIXED_STRING(36))')

        # 2. 为每个实体创建 Tag
        for label in VALID_ENTITY_LABELS:
            fields = entity_field_names(label)  # 反射 dataclass
            cols = ", ".join(f"{f} string" for f in fields)
            self._execute(f'CREATE TAG IF NOT EXISTS {label} ({cols})')

        # 3. 为每个关系创建 Edge type
        for rel_type in RELATION_TYPE_TO_NEO4J.values():
            self._execute(f'CREATE EDGE IF NOT EXISTS {rel_type}()')

        # 4. 创建索引
        self._execute(f'CREATE TAG INDEX IF NOT EXISTS idx_CodeEntity_id '
                      f'ON CodeEntity(id(36))')
```

**关键设计点：**
- VID 类型 `FIXED_STRING(36)` 匹配 OntoAgent 的 UUID（36 字符）
- 所有属性暂用 `string` 类型（简化），后续可优化为强类型
- 自定义实体/关系用通用 `CustomEntity` Tag + `CUSTOM_REL` Edge

---

## 六、分阶段实施计划

### Phase 0: POC 验证（1-2 天）— Go/No-Go 决策点

**目标：** 用最小成本验证关键技术可行性。

1. Docker 搭建 NebulaGraph 测试实例
2. 手写 5 条关键 nGQL 查询验证能否等效表达：
   - ShapeEvaluator 的 MATCH + 变长路径
   - graph.py 的 startNode/endNode 重写
   - 批量 UPSERT
3. 性能 benchmark：
   - 变长路径 `*1..3` 延迟（P50/P99）
   - 批量写入 1000 节点吞吐量
4. 智谱 GLM 生成 nGQL 质量测试（20 条查询）

**Go/No-Go 标准：**
- 性能：ShapeEvaluator 单次 evaluate < 100ms → Go
- LLM 质量：nGQL 生成正确率 > 80% → Go
- 如果任一不达标 → 重新评估方案

### Phase 1: NebulaGraphStore + Schema（4 天）

1. `store/nebula_store.py`：ConnectionPool + Session 管理 + Space
2. `store/nebula_schema.py`：SchemaInitializer 自动 DDL
3. 实现全部抽象方法：merge_node（UPSERT）、merge_relation、get_node、get_relations、delete_node、delete_relation、cleanup_orphan_nodes
4. 批量写入：循环 UPSERT + 事务补偿
5. `store/factory.py`：按配置选择后端
6. 单元测试

### Phase 2: 多 Prompt 模板（2 天）

1. `agent/prompts/graph_query_cypher.yaml`（现有，整理）
2. `agent/prompts/graph_query_ngql.yaml`（新增，三重保障）
3. `agent/prompt.py` 按后端配置加载模板
4. `agent/tool_gateway.py` 增加 NebulaGraph 写操作关键字

### Phase 3: 内部模块适配（4-5 天）

**优先级排序：**
1. ShapeEvaluator + PathCompiler 接口修改（1-2 天）— 核心卖点
2. graph.py 逐条重写（2-3 天）— startNode/endNode/properties/path 变量
3. 其他高频查询（check_compliance / trace_business_impact / builtin）（1 天）

### Phase 4: 集成测试 + benchmark（2-3 天）

1. 真实 NebulaGraph 实例端到端构建
2. ShapeEvaluator L3 运行时评估在 NebulaGraph 上执行
3. 性能基准测试（与 Neo4j 对比）
4. 双后端对比测试（相同数据集，相同查询结果）
5. 配置切换验证：`GRAPH_BACKEND=neo4j` vs `nebula`

---

## 七、新增/修改文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `store/nebula_store.py` | 新增 | NebulaGraphStore（ConnPool+Session+Space） |
| `store/nebula_schema.py` | 新增 | SchemaInitializer（DDL 自动生成） |
| `store/factory.py` | 新增 | 按配置选择后端 |
| `agent/prompts/graph_query_ngql.yaml` | 新增 | nGQL prompt（Schema+Few-shot+规则） |
| `agent/prompts/graph_query_cypher.yaml` | 新增 | Cypher prompt（整理现有） |
| `agent/prompt.py` | 修改 | 按后端加载 prompt |
| `agent/tool_gateway.py` | 修改 | 增加 NebulaGraph 写操作关键字 |
| `execution/shape_evaluator.py` | 修改 | _build_query 传入 source_label |
| `execution/path_compiler.py` | 修改 | compile 输出含 tag 前缀 |
| `api/web/router/graph.py` | 修改 | 逐条重写 Cypher → nGQL |
| `store/neo4j_store.py` | 修改 | 兼容新接口（保持 Cypher） |
| `store/graph_store.py` | 修改 | 可能新增 execute_shape_query |
| `config.py` | 修改 | 新增 graph_backend 配置 |
| `pyproject.toml` | 修改 | 新增 nebula-python 依赖 |

---

## 八、风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| 性能不达标（ShapeEvaluator > 100ms） | 中 | 高 | Phase 0 先 benchmark；可选走内存缓存 |
| 智谱 GLM 生成 nGQL 错误率高 | 中 | 高 | 三重保障 + 语法预检；Phase 0 实测 |
| 批量写入性能下降 5-10 倍 | 高 | 中 | 循环 UPSERT + benchmark；必要时用 INSERT 批量 |
| graph.py 重写工作量大 | 中 | 中 | Phase 3 单独分配 2-3 天 |
| NebulaGraph 强 Schema 阻碍动态本体 | 低 | 中 | 通用 CustomEntity Tag 兜底 |
| Session 泄漏 | 低 | 高 | contextmanager + try/finally |

---

## 九、与历史方案对比

| 维度 | V2（nGQL 编译器） | C-IM（内存图） | **D1-Final** |
|---|---|---|---|
| 工作量 | 13-21 天 | 8-11 天 | **12-15 天** |
| 多后端支持 | 仅 NebulaGraph | NebulaGraph+内存图 | **Neo4j + NebulaGraph** |
| 加新数据库 | 重写编译器 | 重写内存图 | **加一套 prompt** |
| ShapeEvaluator 性能 | 50-200ms | μs 级 | **与 V2 相同**（可选缓存） |
| LLM 动态查询 | 需 nGQL 编译器 | 无法支持 | **多 Prompt 支持** |
| 架构复杂度 | 高（编译器） | 高（双写） | **中（Store 适配）** |
| 可靠性 | 高 | 中（一致性） | **高** |

---

## 十、决策记录

- **2026-07-26：** 3 轮 CC 审查完成，D1-Final 方案确定。下一步：用户确认 → Phase 0 POC。
