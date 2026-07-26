# Task: Phase 5.3 + 5.4

## Task 1: Phase 5.3 — Schema 自动初始化 + DDL 探针重试

### 背景
当前 NebulaSchemaInitializer（src/ontoagent/store/nebula_schema.py）从未被自动调用。

### 需要做的事

1. 在 NebulaGraphStore.__init__ 末尾加 _ensure_schema_ready() 调用：
   - 用原始 session（不走 _session_scope，因为 space 可能还没建）
   - 调用 NebulaSchemaInitializer.initialize() 创建 Space + Tag + Edge + Index
   - DDL 等待用探针重试而不是 time.sleep
   - 探针：SHOW TAGS（succeeded 且非 empty 说明 schema 生效了）
   - 用 tenacity 重试：stop_after_delay=120s, wait=wait_fixed(2s)
   - 如果 120s 后仍失败，打 warning 但不崩溃
   - 用 self._schema_ready bool 缓存，避免每次实例化都重新初始化

2. NebulaSchemaInitializer.create_tags() 补建 SchemaVersion Tag：
   - 当前只遍历 VALID_ENTITY_LABELS，不含 SchemaVersion
   - 加 SchemaVersion Tag（version string, description string, applied_at string）

3. import tenacity（如果项目没有就 uv add tenacity）

### 关键约束
- 不要在 __init__ 里 time.sleep
- _ensure_schema_ready 失败不抛异常，只打 warning

## Task 2: Phase 5.4 — 美元符号 param 兜底

### 背景
上层代码有 20+ 处用 dollar-sign name/entity_id/fp/limit 参数化查询，但 NebulaGraphStore.query() 只做 {key} 替换不处理 dollar-sign key。

### 需要做的事

修改 NebulaGraphStore.query() 方法：

1. 在现有的 {key} 替换逻辑之后，增加 dollar-sign key 替换
2. 如果 value 是 list/dict/tuple/set，抛 TypeError（强制上层改语义 API）
3. 替换时打 warning 日志（标记需要后续迁移）
4. _format_value 返回带引号字符串，需要 strip 引号用于 param 替换

### 验收
- uv run pytest tests/unit/test_nebula_store.py -v
- uv run pytest tests/unit/ -x -q
- uv run ruff check src/ontoagent/store/nebula_store.py src/ontoagent/store/nebula_schema.py
