"""Trace API router for agent observability."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from layerkg.agent.trace import TraceCollector

router = APIRouter(prefix="/trace", tags=["trace"])

# TraceCollector 单例，由 app.py 注入
collector: TraceCollector | None = None


class TraceStepResponse(BaseModel):
    """Single trace step response."""

    step_id: int
    type: str
    content: str
    tool_name: str | None = None
    tool_args: str | None = None
    tool_result: str | None = None
    duration_ms: float | None = None


class TraceResponse(BaseModel):
    """Complete trace response."""

    thread_id: str
    query: str
    status: str
    steps: list[TraceStepResponse]
    total_duration_ms: float | None = None


class TraceListItem(BaseModel):
    """Trace list item response."""

    thread_id: str
    query: str
    status: str
    step_count: int
    total_duration_ms: float | None = None
    created_at: float


class MermaidResponse(BaseModel):
    """Mermaid graph response."""

    mermaid: str


@router.get("/list", response_model=list[TraceListItem])
async def list_traces() -> list[TraceListItem]:
    """列出所有 traces（前端列表页用）。"""
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
        )
        for t in traces
    ]


@router.get("/thread/{thread_id}", response_model=TraceResponse)
async def get_trace(thread_id: str) -> TraceResponse:
    """获取单个 trace 详情。"""
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
                step_id=s.step_id,
                type=s.type,
                content=s.content,
                tool_name=s.tool_name,
                tool_args=s.tool_args,
                tool_result=s.tool_result,
                duration_ms=s.duration_ms,
            )
            for s in log.steps
        ],
        total_duration_ms=log.total_duration_ms,
    )


@router.get("/graph/mermaid", response_model=MermaidResponse)
async def get_graph_mermaid() -> MermaidResponse:
    """获取 Agent 图结构的 Mermaid 表示。"""
    from layerkg.agent.graph import create_agent

    agent = create_agent()
    return MermaidResponse(mermaid=agent.get_graph().draw_mermaid())


@router.delete("/thread/{thread_id}")
async def delete_trace(thread_id: str) -> dict[str, bool]:
    """删除指定 trace。"""
    if not collector:
        raise HTTPException(status_code=404, detail="Trace collector not initialized")
    deleted = await collector.delete_trace(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Trace {thread_id} not found")
    return {"deleted": True}
