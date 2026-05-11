# Phase 4 Day 4: 可观测性实施计划

> 基于 v4 方案（审核 8.5/10 通过）
> 执行方式：Claude Code 逐 Task 实施，Hermes 审核每个 Task

## Task 1: 后端 — Trace 数据模型 + 收集器 + 测试

### 文件操作
- **新建** `src/layerkg/agent/trace.py`
  - `TraceStep` dataclass（step_id, type, content, tool_name, tool_args: str, tool_result: str, duration_ms）
  - `TraceLog` dataclass（thread_id, query, steps, total_duration_ms, created_at, status）
  - `TraceCollector` class（asyncio.Lock, max_traces=500, max_age_seconds=3600）
    - `start_trace(thread_id, query)` → TraceLog（90%阈值时清理）
    - `add_step(thread_id, type, content, ...)` → TraceStep（内部 json.dumps(tool_args)[:1000]，内部生成 step_id）
    - `end_trace(thread_id, status)` → TraceLog
    - `get_trace(thread_id)` → TraceLog | None
    - `delete_trace(thread_id)` → bool
    - `list_traces()` → list[TraceLog]
    - `_clean_old_traces_unlocked(max_delete=100)` 清理超龄+超量
- **修改** `tests/conftest.py` — 添加 trace_collector fixture
  ```python
  @pytest.fixture
  def trace_collector():
      from layerkg.agent.trace import TraceCollector
      return TraceCollector(max_traces=10, max_age_seconds=60)
  ```
- **新建** `tests/unit/agent/test_trace.py`
  - test_start_and_get_trace — 基本 CRUD
  - test_add_step_generates_step_id — step_id 自动递增
  - test_concurrent_add_steps — 10 个并发 add_step，step_id 不混淆
  - test_clean_old_traces — 超龄 traces 自动清理
  - test_clean_excess_traces — 超量清理最旧
  - test_max_delete_limit — 单次清理不超过 max_delete
  - test_delete_trace — 删除方法正确
  - test_tool_args_serialization — dict → JSON str 转换 + 截断
  - test_end_trace_status — running/completed/failed 状态

### 验证
```bash
uv run pytest tests/unit/agent/test_trace.py -v
```

---

## Task 2: 后端 — 集成 graph.py + chat.py + 测试

### 文件操作
- **修改** `src/layerkg/agent/graph.py`
  - 删除 `_get_langfuse_handler()`
  - `_make_config()` 简化（移除 callbacks 逻辑）
  - `run_query_stream()` 增加 `trace_collector: TraceCollector | None = None` 参数
    - start_trace 在开头
    - on_chat_model_end → add_step("thinking")
    - on_tool_start → add_step("tool_call")
    - on_tool_end → add_step("tool_result")
    - 异常 → end_trace(status="failed")
    - 正常 → end_trace(status="completed")
  - tool_end yield 增加 result 字段
- **修改** `src/layerkg/web/router/chat.py`
  - 导入并传递 TraceCollector 给 run_query_stream
- **新建** `tests/unit/agent/test_graph_trace.py`
  - test_trace_events_recorded — thinking/tool_call/tool_result 正确记录
  - test_step_id_increments — step_id 递增
  - test_tool_duration_calculated — 工具耗时计算
  - test_failed_trace_status — 异常时 status="failed"
  - test_no_trace_without_collector — collector=None 时不报错

### 验证
```bash
uv run pytest tests/unit/agent/test_graph_trace.py -v
```

---

## Task 3: 后端 — Trace API + 清理 Langfuse

### 文件操作
- **新建** `src/layerkg/web/router/trace.py`
  - `GET /api/trace/list` — 列出所有 traces
  - `GET /api/trace/thread/{thread_id}` — 获取详情
  - `GET /api/trace/graph/mermaid` — Agent 图 mermaid
  - `DELETE /api/trace/thread/{thread_id}` — 删除 trace
- **修改** `src/layerkg/web/app.py`
  - 创建 TraceCollector 单例
  - 注入到 trace_router.collector
  - include_router
- **修改** `src/layerkg/config.py` — 删除 langfuse_public_key, langfuse_secret_key, langfuse_host
- **修改** `pyproject.toml` — 移除 langfuse>=4.6.1
- **删除** `tests/unit/agent/test_langfuse.py`
- **运行** `grep -r "langfuse" src/ tests/` 确保无遗漏
- **新建** `tests/unit/web/test_trace_api.py`
  - test_list_traces
  - test_get_trace_404
  - test_get_trace_detail
  - test_delete_trace
  - test_get_mermaid

### 验证
```bash
uv run pytest tests/unit/web/test_trace_api.py -v
grep -r "langfuse" src/ tests/  # 应无输出
```

---

## Task 4: 前端 — Traces 页面 + Mermaid

### 文件操作
- `cd frontend && npm install mermaid`
- **修改** `frontend/src/types/index.ts`（或 types.ts）
  - 新增 TraceStep, TraceInfo, TraceListItem 类型
- **新建** `frontend/src/api/trace.ts` — 封装 trace API
- **新建** `frontend/src/views/TracesView.vue` — trace 列表页
- **新建** `frontend/src/views/TraceDetailView.vue` — 详情 + mermaid + 时间线
  - status === "running" 时 2s 轮询
- **修改** `frontend/src/router/index.ts` — /traces, /traces/:threadId
- **修改** `frontend/src/App.vue` — 导航栏新增 Traces tab
- **修改** `frontend/src/views/ChatView.vue` — 对话结束后"查看 Trace"链接

### 验证
```bash
cd frontend && npm run build
```

---

## Task 5: 端到端验证

```bash
uv run pytest tests/ -v
cd frontend && npm run build
```

全部通过后 → git commit + push + 思源笔记更新。
