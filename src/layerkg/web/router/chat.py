import asyncio
import json
import time
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, field_validator
from sse_starlette import EventSourceResponse, ServerSentEvent

from layerkg.agent.graph import run_query
from layerkg.agent.trace import TraceCollector

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


@router.post("/chat", response_model=ChatResponse)
async def chat_sync(req: ChatRequest) -> ChatResponse:
    start = time.time()
    thread_id = req.thread_id or str(uuid4())
    answer = await run_query(req.message, thread_id=thread_id)
    duration_ms = int((time.time() - start) * 1000)
    return ChatResponse(answer=answer, thread_id=thread_id, duration_ms=duration_ms)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    from layerkg.agent.graph import run_query_stream

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
