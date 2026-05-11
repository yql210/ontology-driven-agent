# Phase 4 Day 4: 可观测性/可解释性增强

> 日期: 2026-05-12
> 方案: 纯本地 SDK，零外部依赖

## 1. 目标

让用户在 Web UI 中能看到：
1. **Agent 图结构** — LangGraph ReAct 循环的可视化流程图
2. **推理链路** — 每次对话中 agent 做了什么决策、调了什么工具、花了多久
3. **工具调用详情** — 输入参数 + 输出结果 + 耗时

## 2. 方案选型（已确定）

| 方案 | 结论 |
|---|---|
| Langfuse 自部署 | ❌ 需要 Docker + PostgreSQL，运维成本高 |
| LangSmith 云端 | ❌ 需要外部 API key + 网络依赖 |
| LangGraph Studio | ❌ 需要 LangGraph Cloud/Server |
| **本地 SDK（选定）** | ✅ LangGraph 内置 mermaid + 自建 trace |

## 3. 技术设计

### 3.1 后端: Trace 数据模型

```python
# src/layerkg/agent/trace.py（新文件）
from dataclasses import dataclass, field

@dataclass
class TraceStep:
    step_id: int
    type: str              # "thinking" | "tool_call" | "tool_result" | "final"
    content: str           # LLM 思考内容 / 工具名 / 最终回复
    tool_name: str | None  # 工具调用时填写
    tool_args: dict | None # 工具输入
    tool_result: str | None # 工具输出
    start_time: float      # 时间戳
    duration_ms: float | None  # 耗时（毫秒）

@dataclass
class TraceLog:
    thread_id: str
    query: str
    steps: list[TraceStep] = field(default_factory=list)
    total_duration_ms: float | None = None
    created_at: float = 0.0
```

### 3.2 后端: SSE 事件扩展

现有 SSE 事件: `token`, `tool_start`, `tool_end`, `error`, `done`

新增事件:
- `trace_step` — 每个 agent 步骤完成时推送（含 step_id, type, content, duration_ms）
- `graph_mermaid` — 推送 agent 图的 mermaid 字符串（仅在首次对话时）

```python
# SSE event schema
{"type": "trace_step", "step": {...TraceStep dict...}}
{"type": "graph_mermaid", "mermaid": "graph TD; ..."}
```

### 3.3 后端: Trace 收集器

```python
# src/layerkg/agent/trace.py
class TraceCollector:
    """收集 LangGraph agent 运行时的 trace 数据"""

    def __init__(self):
        self._traces: dict[str, TraceLog] = {}
        self._current_step = 0

    def start_trace(self, thread_id: str, query: str) -> TraceLog: ...
    def add_step(self, thread_id: str, step: TraceStep) -> None: ...
    def end_trace(self, thread_id: str) -> TraceLog: ...
    def get_trace(self, thread_id: str) -> TraceLog | None: ...
    def get_mermaid(self) -> str: ...  # 调用 agent.get_graph().draw_mermaid()
```

### 3.4 后端: 集成到 chat router

修改 `src/layerkg/web/router/chat.py`:
- `run_query_stream()` 中注入 TraceCollector callback
- LangGraph 的 `astream_events` 已提供 `on_chain_start`, `on_chain_end`, `on_tool_start`, `on_tool_end` 等事件
- 捕获这些事件 → 构造 TraceStep → 通过 SSE 推送 `trace_step`

### 3.5 后端: 新 API 端点

```
GET /api/trace/{thread_id}       → 获取历史 trace
GET /api/trace/graph/mermaid     → 获取 agent 图 mermaid
```

### 3.6 前端: Trace 面板

新增组件:
- `TracePanel.vue` — 可折叠的侧边/底部面板
  - **Mermaid 图**: 用 mermaid.js 渲染 agent 图结构（静态，仅展示一次）
  - **步骤时间线**: 竖向列表展示每个 trace_step
    - 🧠 思考 → 显示 LLM 输出文本
    - 🔧 工具调用 → 显示工具名 + 参数（可展开）
    - 📋 工具结果 → 显示返回内容（可折叠）
    - ✅ 最终回复 → 显示耗时

### 3.7 前端: 依赖

```bash
npm install mermaid
```

仅需这一个新前端依赖。后端零新依赖。

### 3.8 清理: 移除 Langfuse 死代码

- `config.py`: 删除 `langfuse_public_key`, `langfuse_secret_key`, `langfuse_host`
- `graph.py`: 删除 `_get_langfuse_handler()`，`_make_config()` 中移除 callbacks
- `tests/unit/agent/test_langfuse.py`: 删除
- `pyproject.toml`: 移除 `langfuse>=4.6.1` 依赖

## 4. 文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/layerkg/agent/trace.py` | 新建 | TraceCollector + 数据模型 |
| `src/layerkg/agent/graph.py` | 修改 | 移除 Langfuse，集成 TraceCollector |
| `src/layerkg/config.py` | 修改 | 删除 Langfuse 配置项 |
| `src/layerkg/web/router/chat.py` | 修改 | SSE 推送 trace_step 事件 |
| `src/layerkg/web/app.py` | 修改 | 挂载 trace router |
| `src/layerkg/web/router/trace.py` | 新建 | trace API 端点 |
| `tests/unit/agent/test_trace.py` | 新建 | TraceCollector 单元测试 |
| `tests/unit/agent/test_langfuse.py` | 删除 | 移除 Langfuse 测试 |
| `pyproject.toml` | 修改 | 移除 langfuse 依赖 |
| `frontend/src/components/TracePanel.vue` | 新建 | Trace 面板组件 |
| `frontend/src/components/MermaidGraph.vue` | 新建 | Mermaid 渲染组件 |
| `frontend/src/views/ChatView.vue` | 修改 | 集成 TracePanel |
| `frontend/src/api/types.ts` | 修改 | 新增 TraceStep, SSE trace_step 事件 |
| `frontend/src/stores/chat.ts` | 修改 | 处理 trace_step / graph_mermaid 事件 |

## 5. 实施顺序（Task 列表）

### Task 1: 后端 - Trace 数据模型 + 收集器
- 新建 `src/layerkg/agent/trace.py`
- 实现 TraceStep, TraceLog, TraceCollector
- 新建 `tests/unit/agent/test_trace.py` — TDD

### Task 2: 后端 - 集成到 graph.py + chat.py
- 修改 `graph.py`: 移除 `_get_langfuse_handler()`，在 `run_agent` 中注入 TraceCollector
- 修改 `chat.py` 的 `run_query_stream()`: 用 `astream_events` 捕获事件 → 构造 TraceStep → SSE 推送
- 新增 `trace_step`, `graph_mermaid` SSE 事件

### Task 3: 后端 - Trace API + 清理 Langfuse
- 新建 `src/layerkg/web/router/trace.py`
- 修改 `app.py` 挂载 trace router
- 修改 `config.py` 删除 Langfuse 字段
- 修改 `pyproject.toml` 移除 langfuse
- 删除 `test_langfuse.py`

### Task 4: 前端 - TracePanel + Mermaid
- `npm install mermaid`
- 新建 `MermaidGraph.vue` — 渲染 mermaid 字符串
- 新建 `TracePanel.vue` — 步骤时间线
- 修改 `types.ts` + `chat.ts` 处理新 SSE 事件
- 修改 `ChatView.vue` 集成面板

### Task 5: 端到端验证
- `uv run pytest tests/ -v` — 全量测试
- `cd frontend && npm run build` — 前端构建
- 手动启动 `uv run layerkg web` → 对话测试 trace 面板

## 6. 验收标准

- [ ] Agent 图 mermaid 可在前端渲染
- [ ] 每次对话的推理步骤可在 TracePanel 中查看
- [ ] 工具调用的参数和结果可展开/折叠
- [ ] 每步显示耗时
- [ ] Langfuse 相关代码完全移除
- [ ] 782+ tests 全绿
- [ ] npm build 通过
