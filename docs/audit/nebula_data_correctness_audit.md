# NebulaGraph 适配层数据正确性深度审计报告

**审计范围**：`src/ontoagent/store/nebula_store.py`、`cypher_adapter.py`、`nebula_schema.py`，对照 `neo4j_store.py`，并追踪 `builder.py` / `impact_propagator.py` / `incremental_updater.py` / `module_clustering.py` 的数据流。

**审计时间**：2026-07-26

---

## 总览

共发现 **11 个正确性问题**，其中 **5 个严重（数据丢失/功能失效）**，4 个高危，2 个中危。即使单元测试通过，这些问题会导致**写入的数据与业务期望不一致**、**读出的数据与写入的数据不一致**、**部分业务功能在 NebulaGraph 后端完全失效**。

| # | 问题 | 严重性 | 影响范围 |
|---|------|--------|----------|
| P1 | `merge_nodes_batch` / `merge_relations_batch` 未实现 → builder 写入全部崩溃 | 🔴 严重 | 全量构建、增量更新 |
| P2 | 关系属性（provenance/weight/confidence）完全丢失 | 🔴 严重 | 影响传播、可信度、溯源 |
| P3 | `merge_node` schema 不匹配时静默丢弃字段 | 🔴 严重 | 任何含动态字段的实体 |
| P4 | bool/int/float 全转字符串 → 读出类型错误 | 🔴 严重 | enabled/resolved/confidence 等 |
| P5 | `get_node` 丢失 Tag 信息 → `node_label` 永远是 "Unknown" | 🔴 严重 | impact_propagator、概念重提取、文档重生成 |
| P6 | VID 用 entity.id，跨标签同名实体 ID 冲突时数据被覆盖 | 🟠 高危 | 多标签场景 |
| P7 | 无事务，并发 UPSERT 竞态 + merge_relation DELETE+INSERT 非原子 | 🟠 高危 | 并发构建 |
| P8 | Cypher→nGQL 转换 best-effort，复杂查询静默失败 | 🟠 高危 | 所有 `query()` 调用 |
| P9 | `ensure_constraints`/`clear_all` 未实现 → 约束缺失 + 清库失败 | 🟠 高危 | 初始化、清库 |
| P10 | `file_path` 查询命名不一致（snake_case vs camelCase） | 🟡 中危 | module_clustering、incremental_updater |
| P11 | 全字符串拼接，注入风险 | 🟡 中危 | 安全 |

---

## P1 🔴 严重：`merge_nodes_batch` / `merge_relations_batch` 完全未实现

### 数据流路径
`builder.py::_stage_write_structural` (line 300, 305, 325, 347, 372) → `graph_store.merge_nodes_batch(...)` / `merge_store.merge_relations_batch(...)`

`builder.py::_write_service_topic_entities` (line 736, 762, 779, 804)
`builder.py::_write_business_ontology` (line 842, 849, 872)
`builder.py::_extract_capabilities` (line 1091, 1098)

### 正确性问题
- `GraphStore` ABC（`graph_store.py`）**只声明了 8 个抽象方法**，不含 `merge_nodes_batch` / `merge_relations_batch` / `ensure_constraints` / `clear_all`。
- `Neo4jGraphStore` 实现了这些方法（`neo4j_store.py` line 184, 238, 542, 581）。
- `NebulaGraphStore` **完全没有实现** `merge_nodes_batch` / `merge_relations_batch` / `ensure_constraints` / `clear_all`（全文搜索确认）。
- `OntoAgentBuilder._stage_write_structural` 的**第一行**就是 `graph_store.ensure_constraints()`（line 296），会直接 `AttributeError`。
- 即使绕过 `ensure_constraints`，line 300 `graph_store.merge_nodes_batch("CodeEntity", code_dicts, ...)` 同样 `AttributeError`。

### 影响范围
- **全量构建（`build`）在 NebulaGraph 后端 100% 崩溃**——Stage 2 关键路径第一个调用就失败。
- 增量更新（`IncrementalUpdater`）虽然 lazy import 的是 `Neo4jGraphStore`（line 153-159，**硬编码 Neo4j**），但 `builder.py` 走 `factory.create_graph_store`，NebulaGraph 路径全断。
- 单元测试通过的原因：测试 mock 了 `GraphStore`，未覆盖真实 `NebulaGraphStore` 的批量接口缺失。

### 证据
```python
# neo4j_store.py line 184 — Neo4j 有
def merge_nodes_batch(self, label, properties_list, batch_size=200): ...

# nebula_store.py — 全文搜索 merge_nodes_batch → 0 结果
# graph_store.py — ABC 也未声明（所以类型检查也漏了）
```

### 严重性：🔴 严重（P0 级阻断）
**判定**：这是最严重的接口契约违反。NebulaGraph 后端**根本无法构建图谱**。

---

## P2 🔴 严重：关系属性（weight / provenance / confidence）完全丢失

### 数据流路径
`builder.py::_stage_write_structural` (line 316-321) →
```python
"properties": add_provenance({"weight": rel.weight}, source="ast_parser", confidence=1.0, ...)
```
→ `graph_store.merge_relations_batch(...)` / `merge_relation(...)` →
`nebula_store.py::merge_relation` (line 288-302):
```python
# NebulaGraph Edge type 当前定义为空，properties 暂无法写入（DDL 简化策略）
insert_stmt = f'INSERT EDGE `{neo4j_rel_type}`() VALUES "{source_id}"->"{target_id}"@0:();'
...
return properties or {}  # ← 返回了 properties 但没写入！
```

### 正确性问题
1. **`nebula_schema.py::create_edges` (line 117-125) 所有 Edge type 都是无属性的**：`CREATE EDGE IF NOT EXISTS \`{edge_type}\` ();`
2. `merge_relation` 收到 `properties` 参数但**完全忽略**——INSERT 语句的 `()` 是空的。
3. `merge_relation` 返回 `properties or {}`，**伪装写入成功**，调用方无法察觉数据丢失。

### 影响范围（业务功能失效）
- **关系 `weight` 丢失**：`Relation.weight`（schema.py line 673，默认 1.0）是影响传播的核心权重。builder 写入时调用 5 处 `add_provenance({"weight": rel.weight}, ...)`（line 317, 444, 606, 754, 796），全部丢失。
- **关系 `provenance_source` / `confidence` / `extracted_at` 丢失**：`add_provenance` 给关系加的溯源字段全部丢失。
- **语义关系的置信度丢失**：`builder.py::_stage_semantic` (line 443-448) 给 LLM 提取的语义关系写入 `confidence=clamp_confidence(rel.weight)`，这是区分 AST 关系（1.0）和 LLM 关系（0.7-0.95）的唯一标记，全部丢失。
- **incremental_updater 的关系 provenance 丢失**：line 271, 373 同样调用 `add_provenance({}, ...)`，空 dict 虽然 weight 没传，但 provenance 三字段也丢。

### 与 Neo4j 的不一致
`neo4j_store.py::merge_relation` (line 410-416) 正确写入属性：
```python
if properties:
    props = _keys_to_camel_case(properties)
    set_clauses.append(f"r.{key} = ${key}")
    params[key] = value
```
同一实体在 Neo4j 里关系带 weight/confidence/provenance，在 NebulaGraph 里是裸边——**数据形态不等价**。

### 严重性：🔴 严重
**判定**：影响传播器（impact_propagator）依赖 weight 区分关系重要性，数据丢失后所有关系权重退化为相同，影响分析失真。

---

## P3 🔴 严重：`merge_node` schema 不匹配时静默丢弃字段

### 数据流路径
`builder_utils.py::entity_to_dict` → 产出 dict（可能含 schema 未定义字段）→
`nebula_store.py::merge_node` (line 167-202)：
```python
props = _keys_to_camel_case(properties)
set_parts = [f"{k} = {_format_value(v)}" for k, v in props.items() if k != "id"]
# UPSERT ...
if "Tag prop not found" in err or "wrong value type" in err:
    logger.warning("... fallback to INSERT placeholder for %s:%s", label, vid)
    stmt = f'INSERT VERTEX `{label}`() VALUES "{vid}":();'  # ← 空属性占位！
```

### 正确性问题
1. 当 `entity_to_dict` 产出的字段名不在 `nebula_schema.create_tags()` 生成的 schema 中时，UPSERT 报 `Tag prop not found`。
2. fallback 逻辑执行 `INSERT VERTEX \`{label}\`() VALUES ...`——**所有属性全部丢弃**，只保留 VID 和 Tag。
3. 这是一个**静默降级**：只打 WARNING 日志，不抛异常，调用方以为写入成功。

### 具体场景（已确认字段名不匹配）
- **`builder_utils.py::entity_to_dict` (line 54)**：`d["code_parameters"] = entity.parameters`
- **`nebula_schema.py::create_tags` (line 100-108)**：注释承认 `codeParameters` 是 `entity_to_dict` 产出的 key，与 schema 的 `parameters` 不同，需单独声明。虽然 schema 里补了 `common_fields = {"codeParameters", ...}`，但：
  - `entity_to_dict` 产出 `codeParameters`（camelCase 后），schema 也声明了 `codeParameters`——**这对上了**。
  - 但 `_EXTRA_FIELDS`（schema.py line 550）声明 CodeEntity 有动态字段 `lines` 和 `entryCategory`，`entity_to_dict` **不产出 `lines`**（搜索确认），而 `action_executor.py` (line 220) 查询 `n.lines`——**读端期望的字段写端从未写入**。
- **`capability_entity_to_dict` (line 270)**：产出 `enabled`（bool），schema 通过 `entity_field_names("CapabilityEntity")` 反射出 `enabled`——字段名对上，但类型是 bool，见 P4。

### 影响范围
任何 `entity_to_dict` 产出但 schema 未声明的字段都会被丢弃。由于 schema 是从 `entity_field_names` 反射生成的，理论上字段名应一致，但：
- 动态字段（`lines`、`entryCategory`）依赖 `_EXTRA_FIELDS` 手动维护，易遗漏。
- 第三方扩展实体（未来新增 dataclass 字段）若忘记更新 schema，会静默丢数据。

### 严重性：🔴 严重
**判定**：静默数据丢失是最危险的正确性问题——"测试通过"但数据是空的。

---

## P4 🔴 严重：bool/int/float 全转字符串 → 读出类型错误

### 数据流路径
写入：`nebula_schema.py::create_tags` (line 112) 所有字段 `string` 类型 →
`nebula_store.py::_format_value` (line 62-77)：
```python
def _format_value(value):
    if value is None: return "null"
    s = str(value)  # ← bool/int/float 全部 str()！
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'
```
读出：`nebula_store.py::_unwrap_value` (line 477-500)：
```python
for caster in ("as_string", "as_int", "as_double", "as_bool"):
    ...  # as_string 永远先成功，所以读出来的永远是字符串
```

### 正确性问题
1. **`_format_value` 把 `True` → `"True"`、`1.0` → `"1.0"`、`42` → `"42"`**，全部当字符串写入。
2. **`_unwrap_value` 的 caster 顺序是 `as_string` 优先**（line 480），NebulaGraph 对所有 string 类型 `as_string()` 都成功，所以读出来**永远是字符串**。
3. **bool 字段**：`capability_entity_to_dict` 写 `enabled=True` → 存成 `"True"` → 读出 `"True"`（字符串），用 `if node["enabled"]` 判断永远为真（非空字符串都 truthy）。
4. **confidence 字段**：`add_provenance` 写 `confidence=1.0` → 存成 `"1.0"` → 读出 `"1.0"`（字符串），做 `0.0 <= confidence <= 1.0` 数值比较时**Python 3 不允许 str 与 float 比较，会 TypeError**。
5. **`start_line` / `end_line`**：`entity_to_dict` 写 int → 存成字符串 → 读出字符串 → `action_executor.py` 做 `int(node.get("lines", 0))` 还能救，但 `shapes.yaml` (line 69) 的 `entity.lines > 100` 约束做字符串与 int 比较 → **TypeError 或错误语义**。

### 影响范围
- **CapabilityEntity.enabled**：读出 `"True"`/`"False"` 字符串，shape 过滤 `if s.enabled`（shape_registry.py line 140）永远为真。
- **所有 confidence 字段**：读出字符串，数值运算崩溃。
- **所有数值字段**（start_line/end_line/size/impacted_count/nodes_added 等）：读出字符串。

### 与 Neo4j 的不一致
Neo4j 是弱 schema，`session.run(cypher, **props)` 直接传 Python 原生类型，Neo4j 保留 int/float/bool。同一实体在 Neo4j 里 `enabled` 是 bool，在 NebulaGraph 里是 `"True"` 字符串——**类型语义不等价**。

### 严重性：🔴 严重
**判定**：类型系统性的偏差，影响所有数值和布尔字段的正确性。

---

## P5 🔴 严重：`get_node` 丢失 Tag 信息 → node_label 永远 "Unknown"

### 数据流路径
`nebula_store.py::get_node` (line 207-241)：
```python
stmt = f'FETCH PROP ON * "{node_id}" YIELD id(vertex) AS id, properties(vertex) AS props;'
...
if "props" in row and isinstance(row["props"], dict):
    props = {...}  # 只展开属性
    props.setdefault("id", node_id)
    return props  # ← 没有 label / tag 信息！
```

### 正确性问题
1. `get_node` 返回的 dict **不包含节点标签（Tag）信息**。Neo4j 的 `get_node` (line 320-330) 返回 `dict(node)`，虽然也不含 label，但 NebulaGraph 的 `FETCH PROP ON *` 本可以获取 tags 却没取。
2. **下游 `impact_propagator.py::_bidirectional_bfs` (line 336-341)**：
```python
node = self._graph_store.get_node(neighbor_id)
impacts.append(ImpactedNode(
    node_label=node.get("label", "Unknown"),  # ← 永远是 "Unknown"！
    ...
))
```
3. **`incremental_updater.py::update` (line 618, 622)**：
```python
concept_ids = [n.node_id for n in impact_report.impacted_nodes if n.node_label == "ConceptEntity"]
doc_ids = [n.node_id for n in impact_report.impacted_nodes if n.node_label == "DocEntity"]
```
由于 `node_label` 永远是 `"Unknown"`，**concept_ids 和 doc_ids 永远为空列表**。

### 影响范围
- **概念重提取（`_flag_concept_reextraction`）永远不触发**：`concepts_flagged` 永远为 0。
- **文档重生成（`_flag_doc_regeneration`）永远不触发**：`docs_flagged` 永远为 0。
- 影响报告中所有节点的 `node_label` 都是 "Unknown"，前端展示和过滤失效。

### 严重性：🔴 严重
**判定**：增量更新的两个关键后续动作（概念重提取、文档重生成）在 NebulaGraph 后端完全失效。

---

## P6 🟠 高危：VID 用 entity.id，跨标签同名 ID 冲突

### 数据流路径
`nebula_store.py::merge_node` (line 168)：`vid = props["id"]`
`nebula_schema.py::ensure_space` (line 73)：`vid_type=FIXED_STRING(36)`

### 正确性问题
1. NebulaGraph 的 VID 是**全局唯一**的（Space 级别），不像 Neo4j 的 `id` 是 Label 级别唯一的（通过 `MERGE (n:Label {id: $id})` 实现标签内唯一）。
2. OntoAgent 的 `entity.id` 是 UUID v4（`str(uuid.uuid4())`），理论上全局唯一，但：
   - **外部导入节点**（`builder.py` line 336-345）也生成 UUID，但如果两个不同仓库的 `__external__` 模块同名（如都叫 `os`、`sys`），**每次构建生成不同 UUID，不会冲突**——这部分安全。
   - **但如果业务层传入非 UUID 的 id**（如 `changeset_id = f"cs-{uuid.uuid4().hex[:12]}"`，incremental_updater.py line 515），`FIXED_STRING(36)` 会**截断**超过 36 字符的 VID（"cs-" + 12 hex = 15 字符，安全；但其他自定义 id 可能超长）。
3. **`delete_node` (line 246) `DELETE VERTEX "{node_id}" WITH EDGE`** 会删除该 VID 的**所有 Tag**——如果一个 VID 被多个 Tag 标记（NebulaGraph 支持多 Tag），删一个会全删。

### 影响范围
当前 UUID 策略下冲突概率极低，但 FIXED_STRING(36) 截断和非 UUID id 是隐患。

### 严重性：🟠 高危

---

## P7 🟠 高危：无事务，并发 UPSERT 竞态 + merge_relation 非原子

### 数据流路径
`nebula_store.py::merge_relation` (line 290-299)：
```python
with self._session_scope() as session:
    delete_stmt = f'DELETE EDGE `{neo4j_rel_type}` "{source_id}"->"{target_id}"@0;'
    insert_stmt = f'INSERT EDGE ...'
    session.execute(delete_stmt)   # ← 第一条
    result = session.execute(insert_stmt)  # ← 第二条
```

### 正确性问题
1. **NebulaGraph 无多语句事务**（不像 Neo4j 的 `session.run` 在事务内执行）。两条 `execute` 之间**不是原子的**。
2. 如果 DELETE 成功但 INSERT 失败（网络抖动、schema 未生效），**关系被删除且不会恢复**——数据丢失。
3. **并发 `merge_relation` 同一(src,tgt)对**：线程 A DELETE → 线程 B DELETE（已不存在，无影响）→ 线程 A INSERT → 线程 B INSERT（NebulaGraph INSERT EDGE 是追加语义，rank=0 时覆盖，但两个 session 的时序无保证）。
4. **并发 `merge_node` 同一 VID**：两个 UPSERT 同时写不同属性集，NebulaGraph 的 UPSERT 是 last-write-wins，可能丢失先写的属性。
5. `builder.py` 的批量写入虽然是串行的，但 `_session_scope` 每次获取新 session，**不同 session 之间无隔离保证**。

### 与 Neo4j 的不一致
Neo4j 的 `session.run` 在**隐式事务**内执行，且有乐观锁重试（`max_transaction_retry_time=60`）。NebulaGraph 无任何事务保证。

### 影响范围
单线程构建时风险较低（串行执行），但 Butler 事件驱动引擎（`butler/`）并发触发 build/update 时，关系可能丢失。

### 严重性：🟠 高危

---

## P8 🟠 高危：Cypher→nGQL 转换 best-effort，复杂查询静默失败

### 数据流路径
`nebula_store.py::query` (line 346-387) → `CypherToNgqlAdapter.adapt(ngql)` → 下发

### 正确性问题
1. `cypher_adapter.py` 注释明确（line 8-11）：**best-effort，无法识别的查询原样下发，仅 warning**。
2. **不处理的模式**（line 39）：`MATCH path = ...` + `length(path)` / `nodes(p)`、`SET` 写操作、`MERGE`。
3. `impact_propagator.py::map_files_to_nodes` (line 246-248) 的查询：
```python
"MATCH (n {file_path: $fp}) RETURN n.id AS id, n.name AS name, labels(n) AS labels"
```
   - `file_path` 是 inline property → adapter 转 `n.Tag.file_path`，但 `(n {file_path: $fp})` 的 `n` **没有 Tag 前缀**（匿名节点），`_fix_property_access` 的 `var_tag_map` 为空 → **`n.file_path` 不会被转换，nGQL 里 n.file_path 无效**（NebulaGraph 需 `n.Tag.field`）。
   - `n.id` → `id(n)` ✓（全局规则）
   - `labels(n)` → `tags(n)` ✓
   - 但 inline `{file_path: $fp}` 的转换依赖 `_fix_inline_matching`，而该正则 `\(([A-Za-z_]\w*):...` **要求变量名 + 冒号**，`(n {file_path...}` 里 `n` 后**没有冒号**（无 Tag），正则不匹配 → **inline property 被丢弃，WHERE 条件丢失** → 查询返回所有节点。
4. **`module_clustering.py::_load_graph` (line 80)**：`c.file_path` → 需要 `c.CodeEntity.file_path`，adapter 的 `var_tag_map` 从 `MATCH (c:CodeEntity)` 提取 `{c: CodeEntity}`，能转 ✓。但 **line 80 `RETURN c.id AS id, c.name AS name, c.file_path AS file_path`** 中 `c.id` → `id(c)`、`c.name` → `c.CodeEntity.name`、`c.file_path` → `c.CodeEntity.file_path`，**但 RETURN 的别名 `file_path` 在结果 dict 里的 key 是 `file_path`**（snake_case），而存储的属性名是 `filePath`（camelCase，因为 merge_node 经 `_keys_to_camel_case`）——**key 不匹配，读出 None**。见 P10。

### 影响范围
- `impact_propagator.map_files_to_nodes` 可能返回错误节点集（WHERE 条件丢失）。
- 任何含匿名节点 inline property 的查询都受影响。
- `MATCH path = ...` 查询完全不支持。

### 严重性：🟠 高危

---

## P9 🟠 高危：`ensure_constraints`/`clear_all` 未实现

### 数据流路径
`builder.py::_stage_write_structural` (line 296)：`graph_store.ensure_constraints()`
`builder.py::build` (line 541)：`graph_store.clear_all()`

### 正确性问题
- `NebulaGraphStore` 没有实现 `ensure_constraints` 和 `clear_all`（ABC 也未声明，所以是隐式缺失）。
- `ensure_constraints` 在 Neo4j 里创建唯一约束（`neo4j_store.py` line 542-561），NebulaGraph 没有等效逻辑——虽然 VID 天然唯一，但 schema 初始化（`NebulaSchemaInitializer`）是否被调用**不明确**（builder 没调用它）。
- `clear_all` 未实现 → `build(clear=True)` 会 `AttributeError`。

### 影响范围
`build(clear=True)` 在 NebulaGraph 后端崩溃。重复构建会导致旧数据残留。

### 严重性：🟠 高危

---

## P10 🟡 中危：`file_path` 查询命名不一致（snake_case vs camelCase）

### 数据流路径
- **写入**：`merge_node` 经 `_keys_to_camel_case` → 存储为 `filePath`。
- **查询（module_clustering）**：`RETURN c.file_path AS file_path` → adapter 转 `c.CodeEntity.file_path` → 查询 NebulaGraph 的 `file_path` 属性——**但 schema 字段名是 `filePath`**（`entity_field_names` 反射出 camelCase）。
- **查询（incremental_updater）**：`MATCH (n {filePath: $fp})` (line 302, 347) → 用 camelCase ✓。

### 正确性问题
1. `module_clustering.py::_load_graph` (line 80) 用 `c.file_path`（snake_case），adapter 转成 `c.CodeEntity.file_path`，但 NebulaGraph schema 的字段是 `filePath`（camelCase）——**属性名不匹配，读出空值**。
2. 结果：`entity_data[entity_id]["file_path"]` 全部为 None → line 114-116 的 `file_to_entities` 分组失效 → **同文件虚拟边全部丢失** → 聚类质量严重下降。
3. `incremental_updater.py::_apply_deleted` (line 302) 和 `_apply_modified` (line 347) 用 `{filePath: $fp}`（camelCase）——**这两个对了**，但 adapter 的 inline matching 对 `(n {filePath: $fp})`（无 Tag）不生效（见 P8），WHERE 条件可能丢失。

### 影响范围
module_clustering 的文件分组失效，聚类退化为纯结构关系聚类。

### 严重性：🟡 中危

---

## P11 🟡 中危：全字符串拼接，注入风险

### 数据流路径
`nebula_store.py` 所有方法：`merge_node`、`merge_relation`、`get_node`、`delete_node`、`query`、`update_node_property`——全部 f-string 拼接。

### 正确性问题
1. VID 值（`"{vid}"`、`"{node_id}"`、`"{source_id}"`、`"{target_id}"`）**未转义双引号**。如果 VID 含 `"`，会破坏 nGQL 语法或注入。
2. `_format_value` (line 76) 对属性值做了 `replace('"', '\\"')`，但 VID 拼接（line 176, 188, 198, 210, 246, 293, 294, 308, 436, 463）**没有转义**。
3. `query` 方法的 `params` 替换 (line 372) 是 `str(value)` 直接替换，无转义。
4. docstring（line 86-87）承认："调用方需保证标签、关系类型、ID 通过白名单校验"，但 VID 是业务 UUID，**不保证不含特殊字符**。

### 影响范围
正常 UUID 安全，但如果 VID 来自外部输入（如用户指定节点 ID），存在注入风险。

### 严重性：🟡 中危

---

## 附录：重点数据流追踪结论

### builder.py 写入 CodeEntity + CALLS 关系的完整流程
1. `entity_to_dict(e)` → dict（snake_case key）
2. `add_provenance(...)` → 加 `provenance_source`/`confidence`/`extracted_at`（snake_case）
3. `merge_nodes_batch("CodeEntity", code_dicts)` → **NebulaGraphStore 未实现，崩溃**（P1）
4. 若假设实现了：`_keys_to_camel_case` → camelCase → UPSERT SET → schema 字段全 string（P4）
5. CALLS 关系：`merge_relations_batch(rel_data)` → **未实现，崩溃**（P1）；即使实现，weight/provenance 丢失（P2）

### impact_propagator 读取 weight/affect_score 的流程
1. **weight 从未写入 NebulaGraph**（P2）——Edge type 无属性。
2. `impact_propagator._compute_score` (line 216-221) 用的是**内置的 `DEFAULT_WEIGHT_MATRIX`**（line 93-104），**不从图谱读 weight**。
3. 所以 weight 丢失**不影响 impact_propagator 的计算**（它用代码内置矩阵），但影响其他依赖图谱 weight 的功能（如果有）。
4. **但 `node_label` 丢失（P5）导致 concept/doc 重提取失效**——这是 impact_propagator 下游的真正问题。

### incremental_updater 的 update_node_property 流程
1. `_flag_concept_reextraction` (line 444)：`update_node_property(node_id, "needsReextraction", True)`
2. `nebula_store.py::update_node_property` (line 409-474)：
   - FETCH tags → 取第一个 Tag → `UPDATE VERTEX ON {tag} SET {tag}.needsReextraction = "True"`
   - **但 schema 没有 `needsReextraction` 字段**（`entity_field_names` 不含它）→ `Tag prop not found` → **返回 False，更新失败**。
3. **结果**：`concepts_flagged` 和 `docs_flagged` 在 NebulaGraph 后端**永远为 0**（双重失效：node_label 丢失导致找不到目标 + schema 无字段导致更新失败）。

### add_provenance 给实体/关系加 provenance 的流程
1. **实体 provenance**：`add_provenance(entity_to_dict(e))` → `provenance_source`/`confidence`/`extracted_at` → merge_node → schema 有这三个字段（`common_fields`，nebula_schema.py line 105-108）→ **写入成功**，但 confidence 是 float 转 string（P4）。
2. **关系 provenance**：`add_provenance({}, source=...)` → merge_relation → **完全丢失**（P2，Edge type 无属性）。

---

## 修复优先级建议

1. **P1**：实现 `merge_nodes_batch`/`merge_relations_batch`/`ensure_constraints`/`clear_all`，或在 ABC 中声明并强制实现。
2. **P2**：给 Edge type 加属性（至少 weight/confidence/provenance_source/extracted_at），`merge_relation` 写入 properties。
3. **P5**：`get_node` 返回值加 `label`/`tag` 字段（FETCH tags(vertex)）。
4. **P4**：schema 用正确类型（int64/double/bool），或读出时按 schema 类型转换。
5. **P3**：移除静默 fallback，schema 不匹配应抛异常而非丢弃数据。
6. **P10**：统一查询属性命名（camelCase）。
7. **P8/P7/P11**：加固 adapter 和并发安全。

---

*报告生成方式：纯静态代码审计，未运行任何测试。所有结论基于源码证据。*
