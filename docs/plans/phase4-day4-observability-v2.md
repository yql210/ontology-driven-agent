# Phase 4 Day 4: 可观测性/可解释性增强（v2 — 审核修订版）

> 日期: 2026-05-12
> 方案: 纯本地 SDK，零外部依赖
> v1 审核: 6.0/10 → 修复 3 个 blocking + 3 个 important issues

## 1. 目标

让用户在 Web UI 中能看到：
1. **Agent 图结构** — LangGraph ReAct 循环的可视化流程图（Mermaid）
2. **推理链路** — 每次对话中 agent 思考→工具调用→结果的完整时间线
3. **工具调用详情** — 输入参数 + 输出结果 + 耗时

## 2. 技术设计

### 2.1 后端: Trace 数据模型

```python
# src/layerkg/agent/trace.py（新文件）
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class TraceStep:
    """单个 agent 步骤"""
    step_id: int              # 由 TraceCollector 内部生成
    type: str                 # "thinking" | "tool_call" | "tool_result" | "final"
    content: str              # LLM 思考 / 工具名 / 最终回复摘要
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    start_time: float = 0.0
    duration_ms: float | None = None


@dataclass
class TraceLog:
    """一次完整对话的 trace"""
    thread_id: str
    query: str
    steps: list[TraceStep] = field(default_factory=list)
    total_duration_ms: float | None = None
    created_at: float = field(default_factory=time.time)


class TraceCollector:
    """线程安全的 trace 收集器（单例）

    修复 v1 blocking issues:
    - B1: asyncio.Lock + per-thread step counter
    - B2: step_id 由 add_step() 内部生成
    - B3: 自动清理超时 traces（默认 1 小时）
    """

    def __init__(self, max_traces: int = 500, max_age_seconds: int = 3600):
        self._traces: dict[str, TraceLog] = {}
        self._lock = asyncio.Lock()
        self._step_counters: dict[str, int] = {}
        self._max_traces = max_traces
        self._max_age_seconds = max_age_seconds

    async def start_trace(self, thread_id: str, query: str) -> TraceLog:
        async with self._lock:
            log = TraceLog(thread_id=thread_id, query=query)
            self._traces[thread_id] = log
            self._step_counters[thread_id] = 0
            # 清理超龄 traces
            self._clean_old_traces_unlocked()
            return log

    async def add_step(
        self,
        thread_id: str,
        type: str,
        content: str,
        tool_name: str | None = None,
        tool_args: dict | None = None,
        tool_result: str | None = None,
        duration_ms: float | None = None,
    ) -> TraceStep:
        """内部生成 step_id，返回创建的 TraceStep"""
        async with self._lock:
            step_id = self._step_counters.get(thread_id, 0)
            self._step_counters[thread_id] = step_id + 1

            step = TraceStep(
                step_id=step_id,
                type=type,
                content=content,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                start_time=time.time(),
                duration_ms=duration_ms,
            )
            log = self._traces.get(thread_id)
            if log:
                log.steps.append(step)
            return step

    async def end_trace(self, thread_id: str) -> TraceLog | None:
        async with self._lock:
            log = self._traces.get(thread_id)
            if log:
                log.total_duration_ms = (time.time() - log.created_at) * 1000
            return log

    async def get_trace(self, thread_id: str) -> TraceLog | None:
        async with self._lock:
            return self._traces.get(thread_id)

    def _clean_old_traces_unlocked(self) -> None:
        """清理超龄 + 超量 traces（必须在 lock 内调用）"""
        now = time.time()
        # 1. 清理超龄
        to_delete = [
            tid for tid, log in self._traces.items()
            if now - log.created_at > self._max_age_seconds
        ]
        for tid in to_delete:
            del self._traces[tid]
            self._step_counters.pop(tid, None)
        # 2. 超量时清理最旧的
        if len(self._traces) > self._max_traces:
            sorted_tids = sorted(self._traces, key=lambda t: self._traces[t].created_at)
            for tid in sorted_tids[: len(self._traces) - self._max_traces]:
                del self._traces[tid]
                self._step_counters.pop(tid, None)
```

### 2.2 后端: 集成到 graph.py

修改 `run_query_stream()`，在现有 astream_events 循环中注入 trace 收集：

```python
# graph.py — run_query_stream 修改
async def run_query_stream(
    question: str,
    thread_id: str | None = None,
    trace_collector: TraceCollector | None = None,  # 新参数
) -> AsyncGenerator[dict]:
    thread_id = thread_id or "default"
    agent = create_agent()
    config = _make_config(thread_id)

    # 开始 trace
    if trace_collector:
        await trace_collector.start_trace(thread_id, question)
        yield {"type": "graph_mermaid", "mermaid": agent.get_graph().draw_mermaid()}

    try:
        tool_start_time: dict[str, float] = {}  # tool_name -> start_time

        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=question)]},
            config=config,
            version="v2",
        ):
            kind = event["event"]

            if kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if hasattr(chunk, "content") and chunk.content:
                    yield {"type": "token", "content": chunk.content}

            elif kind == "on_chat_model_end":
                # Agent 思考完成 → 记录 trace
                output = event["data"].get("output", {})
                content = ""
                if hasattr(output, "content"):
                    content = str(output.content)[:200]
                if trace_collector and content:
                    await trace_collector.add_step(
                        thread_id, type="thinking", content=content
                    )

            elif kind == "on_tool_start":
                tool_name = event["name"]
                args = event["data"].get("input", {})
                tool_start_time[tool_name] = time.time()
                yield {"type": "tool_start", "tool": tool_name, "args": args}
                if trace_collector:
                    await trace_collector.add_step(
                        thread_id,
                        type="tool_call",
                        content=f"调用 {tool_name}",
                        tool_name=tool_name,
                        tool_args=args,
                    )

            elif kind == "on_tool_end":
                tool_name = event["name"]
                output = event["data"].get("output", "")
                duration = None
                if tool_name in tool_start_time:
                    duration = (time.time() - tool_start_time[tool_name]) * 1000
                    del tool_start_time[tool_name]
                yield {
                    "type": "tool_end",
                    "tool": tool_name,
                    "result": str(output)[:500],  # I3 修复: 传 result
                }
                if trace_collector:
                    await trace_collector.add_step(
                        thread_id,
                        type="tool_result",
                        content=f"{tool_name} 返回结果",
                        tool_name=tool_name,
                        tool_result=str(output)[:500],
                        duration_ms=duration,
                    )

    except Exception as e:
        yield {"type": "error", "message": str(e)}
    finally:
        if trace_collector:
            await trace_collector.end_trace(thread_id)
```

### 2.3 后端: Trace API

```python
# src/layerkg/web/router/trace.py（新文件）
from fastapi import APIRouter
from pydantic import BaseModel

from layerkg.agent.trace import TraceCollector

router = APIRouter(prefix="/trace", tags=["trace"])
collector: TraceCollector | None = None  # 由 app.py 注入


class TraceStepResponse(BaseModel):
    step_id: int
    type: str
    content: str
    tool_name: str | None
    tool_args: dict | None
    tool_result: str | None
    duration_ms: float | None

class TraceResponse(BaseModel):
    thread_id: str
    query: str
    steps: list[TraceStepResponse]
    total_duration_ms: float | None

class MermaidResponse(BaseModel):
    mermaid: str


@router.get("/{thread_id}", response_model=TraceResponse | None)
async def get_trace(thread_id: str):
    log = await collector.get_trace(thread_id) if collector else None
    if not log:
        return None
    return TraceResponse(
        thread_id=log.thread_id,
        query=log.query,
        steps=[
            TraceStepResponse(
                step_id=s.step_id, type=s.type, content=s.content,
                tool_name=s.tool_name, tool_args=s.tool_args,
                tool_result=s.tool_result, duration_ms=s.duration_ms,
            ) for s in log.steps
        ],
        total_duration_ms=log.total_duration_ms,
    )


@router.get("/graph/mermaid", response_model=MermaidResponse)
async def get_graph_mermaid():
    from layerkg.agent.graph import create_agent
    agent = create_agent()
    return MermaidResponse(mermaid=agent.get_graph().draw_mermaid())
```

### 2.4 后端: app.py 集成

```python
# app.py 修改
from layerkg.agent.trace import TraceCollector

_trace_collector = TraceCollector()

def create_app():
    app = FastAPI(...)
    # 挂载 trace router
    from layerkg.web.router import trace as trace_router
    trace_router.collector = _trace_collector
    app.include_router(trace_router.router, prefix="/api")

    # 传递 collector 到 chat router
    # chat.py 需要传递 collector 给 run_query_stream
```

### 2.5 后端: 清理 Langfuse

- `config.py`: 删除 `langfuse_public_key`, `langfuse_secret_key`, `langfuse_host`
- `graph.py`: 删除 `_get_langfuse_handler()`，`_make_config()` 移除 callbacks 逻辑
- `pyproject.toml`: 移除 `langfuse>=4.6.1`
- 删除 `tests/unit/agent/test_langfuse.py`

### 2.6 前端: 类型扩展

```typescript
// types.ts 新增
export interface TraceStep {
  step_id: number
  type: 'thinking' | 'tool_call' | 'tool_result' | 'final'
  content: string
  tool_name?: string
  tool_args?: Record<string, unknown>
  tool_result?: string
  duration_ms?: number
}

// SSEEvent 联合类型扩展
// 新增: { type: 'trace_step', step: TraceStep }
//       { type: 'graph_mermaid', mermaid: string }
```

### 2.7 前端: chat.ts 处理新事件

```typescript
// chat.ts — 新增 case
case 'trace_step':
  // 存储 trace steps 到 message 或独立 store
  break
case 'graph_mermaid':
  // 存储 mermaid 字符串
  break
```

### 2.8 前端: TracePanel 组件

新建两个组件：
- `MermaidGraph.vue` — 用 mermaid.js 渲染 agent 图结构
- `TracePanel.vue` — 可折叠面板，展示推理步骤时间线

ChatView 集成：右侧或底部可展开的 TracePanel。

## 3. 实施顺序（Task 列表）

### Task 1: 后端 — Trace 数据模型 + 收集器（TDD）
- 新建 `src/layerkg/agent/trace.py`（TraceStep + TraceLog + TraceCollector）
- 新建 `tests/unit/agent/test_trace.py`
  - 单线程基本功能测试
  - **并发测试**（10 个同时 add_step，step_id 不混淆）
  - **清理测试**（超龄 traces 自动清理）
  - **超量测试**（超过 max_traces 时清理最旧）

### Task 2: 后端 — 集成 graph.py + chat.py SSE
- 修改 `graph.py`:
  - 删除 `_get_langfuse_handler()`
  - `_make_config()` 简化（移除 callbacks）
  - `run_query_stream()` 增加 trace_collector 参数，注入 trace 逻辑
  - `tool_end` 增加 result 字段
- 修改 `chat.py`:
  - 注入 TraceCollector 实例
  - 将 collector 传给 `run_query_stream()`

### Task 3: 后端 — Trace API + 清理 Langfuse
- 新建 `src/layerkg/web/router/trace.py`（GET /api/trace/{thread_id}, GET /api/trace/graph/mermaid）
- 修改 `app.py`（创建 collector 单例，挂载 trace router）
- 修改 `config.py`（删除 Langfuse 字段）
- 修改 `pyproject.toml`（移除 langfuse）
- 删除 `test_langfuse.py`

### Task 4: 前端 — TracePanel + Mermaid
- `npm install mermaid`
- 修改 `types.ts`（新增 TraceStep 类型、SSE 事件类型）
- 修改 `chat.ts`（处理 trace_step / graph_mermaid 事件）
- 新建 `MermaidGraph.vue`
- 新建 `TracePanel.vue`
- 修改 `ChatView.vue`（集成面板）

### Task 5: 端到端验证
- `uv run pytest tests/ -v` — 全量测试
- `cd frontend && npm run build` — 前端构建

## 4. 验收标准

- [ ] Agent 图 mermaid 可在前端渲染
- [ ] 每次对话的推理步骤可在 TracePanel 中查看
- [ ] 工具调用的参数和结果可展开/折叠
- [ ] 每步显示耗时
- [ ] 并发安全：10 个同时请求 trace 数据不混淆
- [ ] 内存安全：超龄 traces 自动清理
- [ ] Langfuse 相关代码完全移除
- [ ] 782+ tests 全绿
- [ ] npm build 通过
