# Phase 4 Day 4: 可观测性/可解释性增强（v4 — 四审修订版）

> 日期: 2026-05-12
> 方案: 纯本地 SDK，零外部依赖
> v1 审核: 6.0/10 → v2 审核: 7.5/10 → v3 审核: 8.0/10 → 修复 3 blocking + 2 important issues

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
import json
import time
from dataclasses import dataclass, field


@dataclass
class TraceStep:
    """单个 agent 步骤"""
    step_id: int              # 由 TraceCollector 内部生成
    type: str                 # "thinking" | "tool_call" | "tool_result" | "final"
    content: str              # LLM 思考 / 工具名 / 最终回复摘要
    tool_name: str | None = None
    tool_args: str | None = None   # v4 B1: JSON序列化后的截断字符串
    tool_result: str | None = None  # v4 B1: 明确 str 类型
    duration_ms: float | None = None


@dataclass
class TraceLog:
    """一次完整对话的 trace"""
    thread_id: str
    query: str
    steps: list[TraceStep] = field(default_factory=list)
    total_duration_ms: float | None = None
    created_at: float = field(default_factory=time.time)
    status: str = "running"  # v3 N2: "running" | "completed" | "failed"


class TraceCollector:
    """线程安全的 trace 收集器（单例）

    v3 修复:
    - B1(v2): 清理操作限制单次数量，避免阻塞请求路径
    - B2(v2): 明确 SSE vs API 分离，trace 数据仅通过 API 获取
    - B3(v2): 路由设计使用明确前缀避免歧义
    - I1: 删除冗余 start_time 字段
    - I2: 新增 graph_trace 集成测试要求
    - I4: 明确前端 SSE 和 API 的职责分离
    """

    def __init__(self, max_traces: int = 500, max_age_seconds: int = 3600):
        self._traces: dict[str, TraceLog] = {}
        self._lock = asyncio.Lock()
        self._step_counters: dict[str, int] = {}
        self._max_traces = max_traces
        self._max_age_seconds = max_age_seconds
        # v4 I1: 删除 _created_at（mermaid 静态无需缓存时间戳）

    async def start_trace(self, thread_id: str, query: str) -> TraceLog:
        async with self._lock:
            log = TraceLog(thread_id=thread_id, query=query)
            self._traces[thread_id] = log
            self._step_counters[thread_id] = 0
            # v3 B1 修复: 只在超过 90% 阈值时清理，且限制单次清理数量
            if len(self._traces) > self._max_traces * 0.9:
                self._clean_old_traces_unlocked(max_delete=100)
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
        """内部生成 step_id，返回创建的 TraceStep
        
        v4 B1: tool_args 自动 JSON 序列化 + 截断（传入 dict，存储 str）
        """
        async with self._lock:
            step_id = self._step_counters.get(thread_id, 0)
            self._step_counters[thread_id] = step_id + 1

            # v4 B1: 序列化 tool_args 为 str，截断到 1000 字符
            args_str = None
            if tool_args is not None:
                args_str = json.dumps(tool_args, ensure_ascii=False)[:1000]

            step = TraceStep(
                step_id=step_id,
                type=type,
                content=content,
                tool_name=tool_name,
                tool_args=args_str,
                tool_result=tool_result,
                duration_ms=duration_ms,
            )
            log = self._traces.get(thread_id)
            if log:
                log.steps.append(step)
            return step

    async def end_trace(self, thread_id: str, status: str = "completed") -> TraceLog | None:
        async with self._lock:
            log = self._traces.get(thread_id)
            if log:
                log.total_duration_ms = (time.time() - log.created_at) * 1000
                log.status = status  # v3 N2: 支持失败状态
            return log

    async def get_trace(self, thread_id: str) -> TraceLog | None:
        async with self._lock:
            return self._traces.get(thread_id)

    async def delete_trace(self, thread_id: str) -> bool:
        """v4 B3: 删除指定 trace"""
        async with self._lock:
            if thread_id in self._traces:
                del self._traces[thread_id]
                self._step_counters.pop(thread_id, None)
                return True
            return False

    async def list_traces(self) -> list[TraceLog]:
        """v3: 列出所有 traces（前端列表页用）"""
        async with self._lock:
            return list(self._traces.values())

    def _clean_old_traces_unlocked(self, max_delete: int = 100) -> None:
        """清理超龄 + 超量 traces（必须在 lock 内调用）

        v3 B1 修复: max_delete 参数限制单次清理数量
        """
        now = time.time()
        # 1. 清理超龄
        to_delete = [
            tid for tid, log in self._traces.items()
            if now - log.created_at > self._max_age_seconds
        ]
        for tid in to_delete[:max_delete]:
            del self._traces[tid]
            self._step_counters.pop(tid, None)
        # 2. 超量时清理最旧的
        if len(self._traces) > self._max_traces:
            sorted_tids = sorted(self._traces, key=lambda t: self._traces[t].created_at)
            excess = len(self._traces) - self._max_traces
            for tid in sorted_tids[:min(excess, max_delete)]:
                del self._traces[tid]
                self._step_counters.pop(tid, None)
```

### 2.2 后端: 集成到 graph.py

**设计决策（v3 B2 修复）**: SSE 事件保持原有 `token` / `tool_start` / `tool_end` / `error` 不变。Trace 数据**仅通过 API 端点**获取，不在 SSE 中传输 trace_step。这样：
- SSE 保持轻量（面向实时流式输出）
- API 提供完整的 trace 查询能力（面向可观测性面板）

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

    # 开始 trace（仅后端收集，不 yield trace 事件到 SSE）
    if trace_collector:
        await trace_collector.start_trace(thread_id, question)

    try:
        tool_start_time: dict[str, float] = {}

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
                    "result": str(output)[:500],
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
        if trace_collector:
            await trace_collector.end_trace(thread_id, status="failed")
        yield {"type": "error", "message": str(e)}
        return

    # 正常结束
    if trace_collector:
        await trace_collector.end_trace(thread_id, status="completed")
```

### 2.3 后端: Trace API

**v3 B3 修复**: 路由使用明确前缀，避免 `/{thread_id}` 与 `/graph/mermaid` 歧义。

```python
# src/layerkg/web/router/trace.py（新文件）
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from layerkg.agent.trace import TraceCollector

router = APIRouter(prefix="/trace", tags=["trace"])
collector: TraceCollector | None = None  # 由 app.py 注入


class TraceStepResponse(BaseModel):
    step_id: int
    type: str
    content: str
    tool_name: str | None
    tool_args: str | None   # v4 B1
    tool_result: str | None  # v4 B1
    duration_ms: float | None

class TraceResponse(BaseModel):
    thread_id: str
    query: str
    status: str  # v3 N2
    steps: list[TraceStepResponse]
    total_duration_ms: float | None

class TraceListItem(BaseModel):
    thread_id: str
    query: str
    status: str
    step_count: int
    total_duration_ms: float | None
    created_at: float

class MermaidResponse(BaseModel):
    mermaid: str


@router.get("/list", response_model=list[TraceListItem])
async def list_traces():
    """列出所有 traces（前端列表页用）"""
    if not collector:
        return []
    traces = await collector.list_traces()
    return [
        TraceListItem(
            thread_id=t.thread_id,
            query=t.query,
            status=t.status,
            step_count=len(t.steps),
            total_duration_ms=t.total_duration_ms,
            created_at=t.created_at,
        ) for t in traces
    ]


@router.get("/thread/{thread_id}", response_model=TraceResponse)
async def get_trace(thread_id: str):
    """获取单个 trace 详情（v3 B3: 明确前缀 /thread/）"""
    if not collector:
        raise HTTPException(status_code=404, detail="Trace collector not initialized")
    log = await collector.get_trace(thread_id)
    if not log:
        raise HTTPException(status_code=404, detail=f"Trace {thread_id} not found")
    return TraceResponse(
        thread_id=log.thread_id,
        query=log.query,
        status=log.status,
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
    """获取 Agent 图结构的 Mermaid 表示"""
    from layerkg.agent.graph import create_agent
    agent = create_agent()
    return MermaidResponse(mermaid=agent.get_graph().draw_mermaid())


@router.delete("/thread/{thread_id}")
async def delete_trace(thread_id: str):
    """v4 B3: 通过 collector 方法删除"""
    if not collector:
        raise HTTPException(status_code=404, detail="Trace collector not initialized")
    deleted = await collector.delete_trace(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Trace {thread_id} not found")
    return {"deleted": True}
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

完整清理清单（v3 I3 修复）：
- `config.py`: 删除 `langfuse_public_key`, `langfuse_secret_key`, `langfuse_host` 字段
- `graph.py`: 删除 `_get_langfuse_handler()`，`_make_config()` 移除 callbacks 逻辑
- `pyproject.toml`: 移除 `langfuse>=4.6.1` 依赖
- 删除 `tests/unit/agent/test_langfuse.py`
- 运行 `grep -r "langfuse" src/ tests/` 确保无遗漏引用

### 2.6 前端: 设计（v3 B2 + I4 修复）

**明确职责分离**:
- **SSE**: 保持原有 `token` / `tool_start` / `tool_end` / `error` 事件，**不变**
- **API**: 前端通过 `GET /api/trace/list` 获取 trace 列表，`GET /api/trace/thread/{id}` 获取详情
- **Mermaid**: `GET /api/trace/graph/mermaid` 获取 Agent 图，前端用 mermaid.js 渲染

前端页面设计：
1. **导航栏** — 新增 "Traces" tab，路由 `/traces`
2. **TracesView.vue** — trace 列表页
   - 表格显示: thread_id, query, status, steps 数, 耗时, 时间
   - 点击某行展开详情
3. **TraceDetailView.vue** — 单个 trace 详情页
   - Mermaid 图（Agent 结构）
   - 时间线面板（步骤列表，可展开工具参数/结果）
4. **ChatView.vue** — 对话页面底部添加"查看 Trace"链接（对话结束后出现）

**v4 I2: 前端 trace 状态同步策略**
- TraceDetailView 在 `status === "running"` 时，每 2s 轮询 `GET /api/trace/thread/{id}`
- 轮询直到 `status !== "running"` 或用户离开页面
- 对话 SSE 完成后，显示"查看 Trace"按钮

```typescript
// types.ts 新增
export interface TraceStep {
  step_id: number
  type: 'thinking' | 'tool_call' | 'tool_result' | 'final'
  content: string
  tool_name?: string
  tool_args?: string   // v4 B1: JSON序列化后截断字符串
  tool_result?: string
  duration_ms?: number
}

export interface TraceInfo {
  thread_id: string
  query: string
  status: 'running' | 'completed' | 'failed'
  steps: TraceStep[]
  total_duration_ms?: number
}

export interface TraceListItem {
  thread_id: string
  query: string
  status: string
  step_count: number
  total_duration_ms?: number
  created_at: number
}
```

## 3. 实施顺序（Task 列表）

### Task 1: 后端 — Trace 数据模型 + 收集器（TDD）
- 新建 `src/layerkg/agent/trace.py`（TraceStep + TraceLog + TraceCollector）
- 修改 `tests/conftest.py`（v4 B2: 添加 trace_collector fixture，每次测试用独立实例）
  ```python
  @pytest.fixture
  def trace_collector():
      from layerkg.agent.trace import TraceCollector
      return TraceCollector(max_traces=10, max_age_seconds=60)
  ```
- 新建 `tests/unit/agent/test_trace.py`
  - 单线程基本功能测试
  - **并发测试**（10 个同时 add_step，step_id 不混淆）
  - **清理测试**（超龄 traces 自动清理）
  - **超量测试**（超过 max_traces 时清理最旧）
  - **max_delete 限制测试**（v3: 验证单次清理不超过 max_delete）

### Task 2: 后端 — 集成 graph.py + chat.py
- 修改 `graph.py`:
  - 删除 `_get_langfuse_handler()`
  - `_make_config()` 简化（移除 callbacks）
  - `run_query_stream()` 增加 trace_collector 参数，注入 trace 收集
  - `tool_end` yield 增加 result 字段
  - 异常时 end_trace(status="failed")
  - 正常结束时 end_trace(status="completed")
- 修改 `chat.py`:
  - 注入 TraceCollector 实例
  - 将 collector 传给 `run_query_stream()`
- **v3 I2**: 新建 `tests/unit/agent/test_graph_trace.py`
  - 验证 trace 事件正确记录（thinking/tool_call/tool_result）
  - 验证 step_id 正确递增
  - 验证工具调用耗时计算
  - 验证异常时 status="failed"

### Task 3: 后端 — Trace API + 清理 Langfuse
- 新建 `src/layerkg/web/router/trace.py`（GET /api/trace/list, GET /api/trace/thread/{id}, GET /api/trace/graph/mermaid, DELETE /api/trace/thread/{id}）
- 修改 `app.py`（创建 collector 单例，挂载 trace router）
- 修改 `config.py`（删除 Langfuse 字段）
- 修改 `pyproject.toml`（移除 langfuse）
- 删除 `test_langfuse.py`
- 运行 `grep -r "langfuse" src/ tests/` 确保完全清理
- 新建 `tests/unit/web/test_trace_api.py`
  - API 路由测试（list / get / delete / mermaid）
  - 404 响应测试

### Task 4: 前端 — Traces 页面 + Mermaid
- `npm install mermaid`
- 修改 `types.ts`（新增 TraceStep / TraceInfo / TraceListItem 类型）
- 新建 `api/trace.ts`（封装 trace API 调用）
- 新建 `TracesView.vue`（trace 列表页）
- 新建 `TraceDetailView.vue`（trace 详情 + mermaid + 时间线）
- 修改 `router/index.ts`（新增 /traces, /traces/:threadId 路由）
- 修改 `App.vue`（导航栏新增 Traces tab）
- 修改 `ChatView.vue`（对话结束后添加"查看 Trace"链接）

### Task 5: 端到端验证
- `uv run pytest tests/ -v` — 全量测试
- `cd frontend && npm run build` — 前端构建

## 4. 验收标准

- [ ] Agent 图 mermaid 可在前端渲染
- [ ] Trace 列表页可查看所有历史 traces
- [ ] Trace 详情页展示完整推理步骤时间线
- [ ] 工具调用的参数和结果可展开/折叠
- [ ] 每步显示耗时
- [ ] Trace 有 status 字段（running/completed/failed）
- [ ] 并发安全：10 个同时请求 trace 数据不混淆
- [ ] 内存安全：超龄 traces 自动清理，单次清理有上限
- [ ] API 路由清晰无歧义（/thread/ 前缀）
- [ ] SSE 不传输 trace 数据，保持轻量
- [ ] Langfuse 相关代码完全移除
- [ ] 782+ tests 全绿
- [ ] npm build 通过
