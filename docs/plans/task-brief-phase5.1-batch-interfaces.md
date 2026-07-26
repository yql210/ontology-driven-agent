# Task: Phase 5.1 — 为 NebulaGraphStore 实现 4 个 batch 接口

## 背景

`NebulaGraphStore`（`src/ontoagent/store/nebula_store.py`）缺失 4 个方法，导致 `builder.py` 第一个调用就 `AttributeError` 崩溃。需要实现这 4 个方法。

`Neo4jGraphStore`（`src/ontoagent/store/neo4j_store.py`）已有这 4 个方法的实现，作为接口契约参考。

## 要实现的 4 个方法

### 1. `ensure_constraints(self) -> None`

**Neo4j 实现**（neo4j_store.py:542-561）：为每个 entity label 创建 `CREATE CONSTRAINT ... REQUIRE n.id IS UNIQUE`，然后调 `register_schema_version(self)`。

**NebulaGraph 实现**：
- NebulaGraph 的 VID 天然全局唯一（不需要显式约束）
- 所以方法体逻辑：调用 `NebulaSchemaInitializer.initialize()` 确保 schema 存在（如果还没初始化的话）
- 不需要 `register_schema_version`（schema_version.py 的 MERGE 还没改造，Phase 7b 才处理；暂时空操作或 try-except 忽略）
- 日志记录

### 2. `merge_nodes_batch(self, label: str, properties_list: list[dict], batch_size: int = 200) -> int`

**Neo4j 实现**（neo4j_store.py:184-220）：UNWIND + MERGE，按 batch_size 分批。

**NebulaGraph 实现**：
- 输入校验：label 合法（`re.match(r"^[A-Za-z_]\w*$", label)`），每个 dict 含 `id`
- key 转 camelCase（复用已有的 `_keys_to_camel_case`）
- 按 batch_size 分批
- **每批生成批量 INSERT VERTEX 语句**：
  ```ngql
  INSERT VERTEX `CodeEntity`(`name`, `entityType`, `filePath`) VALUES
    "uuid-1":("authenticate", "function", "/src/auth.py"),
    "uuid-2":("validate_token", "function", "/src/auth.py");
  ```
  **关键：INSERT VERTEX 的属性列表不带类型**（实测验证：`INSERT VERTEX tag(name) VALUES ...` 正确，`INSERT VERTEX tag(name string)` 会 SyntaxError）
- 属性值用已有的 `_format_value` 函数序列化
- 排除 `id` 字段（id 已作为 VID）
- 用已有的 `_session_scope` 获取 session 执行
- 失败时用 `safe_error_msg` 取错误信息并抛 RuntimeError
- 返回写入的总节点数

**注意 UPSERT vs INSERT**：
- 用 `INSERT VERTEX` 即可（NebulaGraph INSERT 对同一 VID 是覆盖语义，等效于 MERGE 的幂等性）
- 如果需要只更新部分字段不覆盖其他，才用 UPSERT VERTEX ON。builder 的场景是全量写入，INSERT 的覆盖语义正好。

### 3. `merge_relations_batch(self, relations: list[dict], batch_size: int = 200) -> int`

**Neo4j 实现**（neo4j_store.py:238-309）：UNWIND + MERGE，按 (source_label, target_label, rel_type) 分组。

**NebulaGraph 实现**：
- 输入校验：rel_type 映射 `RELATION_TYPE_TO_NEO4J.get(rel["rel_type"], rel["rel_type"].upper())`
- 按 rel_type 分组（NebulaGraph 的 INSERT EDGE 按 edge type 批量）
- 按 batch_size 分批
- **每批生成批量 INSERT EDGE 语句**：
  ```ngql
  INSERT EDGE `CALLS`() VALUES "src-id-1"->"dst-id-1":(), "src-id-2"->"dst-id-2":();
  ```
  **注意**：当前 Edge type 无属性（Phase 6.1 才加属性），所以 `()` 是空的。properties 参数暂时忽略（返回时记录但不写入）。
- 用已有的 `_session_scope` 获取 session 执行
- 返回写入的总关系数

### 4. `clear_all(self) -> int`

**Neo4j 实现**（neo4j_store.py:581-601）：循环 MATCH + DETACH DELETE 分批删除。

**NebulaGraph 实现**：
- 用 `CLEAR SPACE \`{self._space}\`;`（实测验证：CLEAR SPACE 保留 schema 只删数据）
- CLEAR SPACE 不返回删除数量，返回 0 或预估数
- 日志记录

## 代码约束

1. **复用已有工具函数**：`_keys_to_camel_case`、`_format_value`、`safe_error_msg`、`_session_scope`、`_escape_prop_name`、`RELATION_TYPE_TO_NEO4J`
2. **Tag 名和 Edge 名加反引号**（已有约定，防保留字冲突）
3. **TDD**：先写测试再写实现。测试用 mock session（参照已有的 `tests/unit/store/test_nebula_store.py` 的 mock 模式）
4. **docstring 完整**，中文注释
5. **不要改 `GraphStore` ABC**——这 4 个方法不在 ABC 里（是 Neo4j 的扩展），NebulaGraph 也作为扩展方法实现即可。不要把 batch 方法加到 ABC。
6. **import**：需要的 import 从 neo4j_store.py 参考（`re`、`logging` 已有）

## 测试要求

在 `tests/unit/store/test_nebula_store.py` 里新增测试（复用已有的 mock fixture），覆盖：
- `merge_nodes_batch`：正常批量写入、空列表、缺 id 报错、非法 label 报错、分批正确
- `merge_relations_batch`：正常批量写入、rel_type 映射、分批正确
- `ensure_constraints`：正常调用不报错
- `clear_all`：正常调用、返回值

## 验收

```bash
# 1. 单元测试全通过
uv run pytest tests/unit/store/test_nebula_store.py -v

# 2. grep 确认实现存在
grep -n "def merge_nodes_batch\|def merge_relations_batch\|def ensure_constraints\|def clear_all" src/ontoagent/store/nebula_store.py

# 3. 代码检查
uv run ruff check src/ontoagent/store/nebula_store.py
uv run pyright src/ontoagent/store/nebula_store.py
```

## 实测验证的关键语法（已在 NebulaGraph 3.7.0 验证通过）

```ngql
# INSERT VERTEX 不带类型
INSERT VERTEX `CodeEntity`(`name`, `entityType`) VALUES "uuid-1":("authenticate", "function");

# 批量 INSERT VERTEX
INSERT VERTEX `CodeEntity`(`name`) VALUES "v1":("a"), "v2":("b"), "v3":("c");

# 批量 INSERT EDGE
INSERT EDGE `CALLS`() VALUES "src-1"->"dst-1":(), "src-2"->"dst-2":();

# CLEAR SPACE 保留 schema
CLEAR SPACE `ontoagent`;
```
