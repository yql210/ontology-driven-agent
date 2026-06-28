import asyncio
import json
import time
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, field_validator
from sse_starlette import EventSourceResponse, ServerSentEvent

from ontoagent.agent.graph import run_query
from ontoagent.agent.trace import TraceCollector

router = APIRouter()

# TraceCollector 单例，由 app.py 注入
collector: TraceCollector | None = None


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            msg = "message cannot be empty"
            raise ValueError(msg)
        return v[:2000]


class ChatResponse(BaseModel):
    answer: str
    thread_id: str
    duration_ms: int


class ApprovalRequest(BaseModel):
    approval_id: str
    approved: bool
    thread_id: str | None = None

    @field_validator("approval_id")
    @classmethod
    def approval_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            msg = "approval_id cannot be empty"
            raise ValueError(msg)
        return v


class ApprovalResponse(BaseModel):
    success: bool
    status: str  # "completed" | "rejected" | "error"
    message: str
    result: dict | None = None


@router.post("/chat", response_model=ChatResponse)
async def chat_sync(req: ChatRequest) -> ChatResponse:
    start = time.time()
    thread_id = req.thread_id or str(uuid4())
    answer = await run_query(req.message, thread_id=thread_id)
    duration_ms = int((time.time() - start) * 1000)
    return ChatResponse(answer=answer, thread_id=thread_id, duration_ms=duration_ms)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    from ontoagent.agent.graph import run_query_stream

    thread_id = req.thread_id or str(uuid4())

    async def event_generator():
        trace_ended = False
        try:
            async with asyncio.timeout(120):
                async for event in run_query_stream(
                    req.message,
                    thread_id=thread_id,
                    trace_collector=collector,
                ):
                    yield ServerSentEvent(
                        data=json.dumps(event, ensure_ascii=False),
                        event=event["type"],
                    )
            # 正常结束 → yield done
            yield ServerSentEvent(
                data=json.dumps({"thread_id": thread_id}),
                event="done",
            )
        except TimeoutError:
            if collector:
                await collector.end_trace(thread_id, status="failed")
                trace_ended = True
            yield ServerSentEvent(
                data=json.dumps({"type": "error", "message": "Agent timeout"}),
                event="error",
            )
        except Exception as e:
            if collector:
                await collector.end_trace(thread_id, status="failed")
                trace_ended = True
            yield ServerSentEvent(
                data=json.dumps({"type": "error", "message": str(e)}),
                event="error",
            )
        finally:
            # 只做 trace 清理，不 yield 事件
            if collector and not trace_ended:
                try:
                    trace = await collector.get_trace(thread_id)
                    if trace and trace.status == "running":
                        await collector.end_trace(thread_id, status="completed")
                except Exception:
                    pass

    return EventSourceResponse(event_generator())


@router.post("/chat/approval", response_model=ApprovalResponse)
async def chat_approval(req: ApprovalRequest) -> ApprovalResponse:
    """处理审批决策。前端点击批准/拒绝按钮后调用。

    直接调用 express_intent 工具，传入 approval_id + approved 参数，
    跳过完整的 Agent 循环。返回执行结果。
    """
    try:
        from ontoagent.agent.tools import _get_approval_gate

        # 验证审批令牌
        gate = _get_approval_gate()
        if gate is None:
            return ApprovalResponse(
                success=False,
                status="error",
                message="审批系统未启用",
            )

        ctx = gate.resolve(req.approval_id, req.approved)
        if ctx is None:
            if not req.approved:
                return ApprovalResponse(
                    success=True,
                    status="rejected",
                    message="操作已被拒绝",
                )
            return ApprovalResponse(
                success=False,
                status="error",
                message="审批令牌无效、已过期或已被使用",
            )

        # 继续执行
        import os

        from ontoagent.agent.tools import _get_action_executor
        from ontoagent.store.neo4j_store import Neo4jGraphStore

        uri = os.environ.get("ONTOAGENT_NEO4J_URI", "bolt://localhost:7687")
        user = os.environ.get("ONTOAGENT_NEO4J_USER", "neo4j")
        password = os.environ.get("ONTOAGENT_NEO4J_PASSWORD", "")

        graph_store = Neo4jGraphStore(uri=uri, user=user, password=password)
        executor = _get_action_executor(graph_store)

        # 记录 trace: 审批开始
        from ontoagent.api.web.router.trace import collector as trace_collector

        if trace_collector and req.thread_id:
            await trace_collector.add_step(
                req.thread_id,
                type="approval_resolved",
                content=f"审批{'通过' if req.approved else '拒绝'}: {ctx.intent_type} → {ctx.target}",
                tool_name="approval",
            )

        result = executor.execute(
            ctx.intent_type,
            {**ctx.params, "target": ctx.target},
            bypass_function_approval=True,
        )

        # 记录 trace: 审批执行结果
        if trace_collector and req.thread_id:
            await trace_collector.add_step(
                req.thread_id,
                type="tool_result",
                content=f"审批执行结果: {'成功' if result.success else '失败'}",
                tool_name="approval",
                tool_result=f"success={result.success}, summary={result.summary}",
            )

        return ApprovalResponse(
            success=result.success,
            status="completed",
            message=result.summary or f"操作 '{ctx.intent_type}' 执行完成",
            result=result.to_dict(),
        )

    except Exception as e:
        import logging

        logging.getLogger(__name__).exception("chat_approval failed")
        return ApprovalResponse(
            success=False,
            status="error",
            message=str(e),
        )
