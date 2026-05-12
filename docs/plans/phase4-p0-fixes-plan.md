# Phase 4 P0 修复实施计划

基于方案 v2（审核通过预估 9.5/10），拆分为 bite-sized tasks。

## Task 列表

### Task 1：P0-1 TraceCollector SQLite 持久化（trace.py）
**文件**：`src/layerkg/agent/trace.py`
**预估**：5 分钟

修改 `TraceCollector`：
1. `__init__` 新增 `persist_path` 参数（放在最后），初始化 SQLite
2. 新增 `_get_db()` 方法（每次新建连接）
3. 新增 `_init_db()` 方法（建表）
4. 新增 `_load_from_db()` 方法（启动加载）
5. 修改 `end_trace` — 添加 `await self._save_trace(log)`
6. 修改 `delete_trace` — 添加 `await self._delete_trace_persisted(thread_id)`
7. 修改 `_clean_old_traces_unlocked` — 清理后调用 `await self._rewrite_all_traces()`
8. 新增 `_trace_to_dict` / `_dict_to_trace` 序列化方法
9. 新增 `_save_trace` / `_delete_trace_persisted` / `_rewrite_all_traces` 异步方法

**注意**：`_clean_old_traces_unlocked` 当前是同步方法，调用 `_rewrite_all_traces` 需要改为异步或用不同模式。建议在 `start_trace` 中 `_clean_old_traces_unlocked` 改为 `await self._clean_old_traces()`，在锁内调用 `_rewrite_all_traces`。

### Task 2：P0-1 TraceCollector 持久化测试
**文件**：`tests/unit/agent/test_trace.py`（追加）
**预估**：5 分钟

1. `test_trace_persist_on_end` — end_trace 后 SQLite 有记录
2. `test_trace_load_from_db` — 从 SQLite 加载历史 trace
3. `test_trace_db_corrupted` — 损坏数据优雅忽略
4. `test_trace_clean_rewrites_db` — 清理后 SQLite 同步
5. `test_trace_delete_removes_from_db` — delete 删 SQLite
6. `test_trace_start_not_persisted` — start 不写 SQLite

### Task 3：P0-2 SSE done 事件时序修复
**文件**：`src/layerkg/web/router/chat.py`
**预估**：3 分钟

修改 `event_generator()`：
1. 将 `yield ServerSentEvent(done)` 从 finally 之后移到 try 块末尾
2. except 块只 yield error，不发 done
3. finally 块只做 trace 清理

### Task 4：P0-2 SSE 时序测试
**文件**：`tests/unit/test_web.py`（追加）
**预估**：3 分钟

1. `test_stream_done_after_normal` — 正常路径最后收到 done
2. `test_stream_error_no_done` — 异常路径无 done
3. `test_stream_timeout_no_done` — 超时路径无 done

### Task 5：P0-3 LLM 单例缓存
**文件**：`src/layerkg/agent/graph.py`
**预估**：2 分钟

1. 新增 `_llm: ChatOpenAI | None = None` 模块变量
2. 新增 `_get_llm()` 函数（懒初始化单例）
3. 新增 `_reset_llm()` 函数（测试用）
4. 修改 `_agent_node`：`llm = _create_llm()` → `llm = _get_llm()`

### Task 6：P0-3 LLM 缓存测试 + conftest
**文件**：`tests/unit/agent/test_graph.py`（追加）+ `tests/conftest.py`（修改）
**预估**：2 分钟

1. conftest.py 添加 `_reset_singletons` autouse fixture
2. `test_llm_singleton` — 多次调用返回同一实例
3. `test_llm_reset` — reset 后创建新实例

### Task 7：验证 + ruff + 全量测试
**预估**：3 分钟

1. `uv run ruff check src/ tests/ --fix`
2. `uv run pytest tests/ -v`
3. 确认 810+ 测试全通过
