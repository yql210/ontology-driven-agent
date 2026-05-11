"""Trace data model and collector for agent observability."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field


@dataclass
class TraceStep:
    """Single agent step."""

    step_id: int
    type: str  # "thinking" | "tool_call" | "tool_result" | "final"
    content: str
    tool_name: str | None = None
    tool_args: str | None = None
    tool_result: str | None = None
    duration_ms: float | None = None


@dataclass
class TraceLog:
    """Complete trace for a single conversation."""

    thread_id: str
    query: str
    steps: list[TraceStep] = field(default_factory=list)
    total_duration_ms: float | None = None
    created_at: float = field(default_factory=time.time)
    status: str = "running"  # "running" | "completed" | "failed"


class TraceCollector:
    """Thread-safe trace collector (singleton)."""

    def __init__(self, max_traces: int = 500, max_age_seconds: int = 3600) -> None:
        self._traces: dict[str, TraceLog] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._step_counters: dict[str, int] = {}
        self._max_traces = max_traces
        self._max_age_seconds = max_age_seconds

    async def start_trace(self, thread_id: str, query: str) -> TraceLog:
        """Start a new trace."""
        async with self._lock:
            log = TraceLog(thread_id=thread_id, query=query)
            self._traces[thread_id] = log
            self._step_counters[thread_id] = 0
            # Clean old traces when approaching limit
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
        """Add a step to the trace. Returns the created TraceStep."""
        async with self._lock:
            step_id = self._step_counters.get(thread_id, 0)
            self._step_counters[thread_id] = step_id + 1

            # Serialize tool_args to JSON string, truncate to 1000 chars
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
        """End a trace with status."""
        async with self._lock:
            log = self._traces.get(thread_id)
            if log:
                log.total_duration_ms = (time.time() - log.created_at) * 1000
                log.status = status
            return log

    async def get_trace(self, thread_id: str) -> TraceLog | None:
        """Get a trace by thread_id."""
        async with self._lock:
            return self._traces.get(thread_id)

    async def delete_trace(self, thread_id: str) -> bool:
        """Delete a trace by thread_id."""
        async with self._lock:
            if thread_id in self._traces:
                del self._traces[thread_id]
                self._step_counters.pop(thread_id, None)
                return True
            return False

    async def list_traces(self) -> list[TraceLog]:
        """List all traces."""
        async with self._lock:
            return list(self._traces.values())

    def _clean_old_traces_unlocked(self, max_delete: int = 100) -> None:
        """Clean old and excess traces (must be called within lock)."""
        now = time.time()
        # 1. Clean expired traces
        to_delete = [
            tid for tid, log in self._traces.items()
            if now - log.created_at > self._max_age_seconds
        ]
        for tid in to_delete[:max_delete]:
            del self._traces[tid]
            self._step_counters.pop(tid, None)
        # 2. Clean oldest if exceeding max_traces
        if len(self._traces) > self._max_traces:
            sorted_tids = sorted(self._traces, key=lambda t: self._traces[t].created_at)
            excess = len(self._traces) - self._max_traces
            for tid in sorted_tids[:min(excess, max_delete)]:
                del self._traces[tid]
                self._step_counters.pop(tid, None)
