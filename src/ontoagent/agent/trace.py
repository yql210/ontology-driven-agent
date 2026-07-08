"""Trace data model and collector for agent observability."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TraceStep:
    """Single agent step."""

    step_id: int
    type: str  # "thinking" | "tool_call" | "tool_result" | "final" | "approval_required" | "approval_resolved"
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
    # NEW — 审批关联
    approval_token: str = ""       # 当前待审批的令牌
    approval_status: str = ""      # "pending" | "approved" | "rejected" | ""
    parent_trace_thread_id: str = ""  # 如果是审批回执，关联父 trace


class TraceCollector:
    """Thread-safe trace collector (singleton)."""

    def __init__(
        self,
        max_traces: int = 500,
        max_age_seconds: int = 3600,
        persist_path: str = ".traces.db",
    ) -> None:
        self._traces: dict[str, TraceLog] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._step_counters: dict[str, int] = {}
        self._max_traces = max_traces
        self._max_age_seconds = max_age_seconds
        self._persist_path = Path(persist_path)

        # SQLite 持久化 — 初始化建表
        self._init_db()
        self._load_from_db()

    def _get_db(self) -> sqlite3.Connection:
        """每次获取新连接（线程安全，asyncio.to_thread 可能在不同线程）"""
        db = sqlite3.connect(str(self._persist_path))
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _init_db(self) -> None:
        """初始化建表"""
        db = self._get_db()
        try:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    thread_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
            """
            )
            db.commit()
        finally:
            db.close()

    def _load_from_db(self) -> None:
        """启动时从 SQLite 加载历史 traces"""
        try:
            db = self._get_db()
            try:
                rows = db.execute("SELECT thread_id, data FROM traces").fetchall()
            finally:
                db.close()
            for thread_id, data_json in rows:
                data = json.loads(data_json)
                log = self._dict_to_trace(data)
                if log:
                    self._traces[thread_id] = log
                    self._step_counters[thread_id] = len(log.steps)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to load trace history from SQLite: %s", e)

    @staticmethod
    def _trace_to_dict(log: TraceLog) -> dict:
        """序列化 TraceLog 为字典"""
        return {
            "thread_id": log.thread_id,
            "query": log.query,
            "status": log.status,
            "created_at": log.created_at,
            "total_duration_ms": log.total_duration_ms,
            "approval_token": log.approval_token,
            "approval_status": log.approval_status,
            "parent_trace_thread_id": log.parent_trace_thread_id,
            "steps": [
                {
                    "step_id": s.step_id,
                    "type": s.type,
                    "content": s.content,
                    "tool_name": s.tool_name,
                    "tool_args": s.tool_args,
                    "tool_result": s.tool_result,
                    "duration_ms": s.duration_ms,
                }
                for s in log.steps
            ],
        }

    @staticmethod
    def _dict_to_trace(data: dict) -> TraceLog | None:
        """从字典反序列化 TraceLog"""
        try:
            log = TraceLog(
                thread_id=data["thread_id"],
                query=data["query"],
                status=data.get("status", "running"),
                created_at=data.get("created_at", time.time()),
                total_duration_ms=data.get("total_duration_ms"),
                approval_token=data.get("approval_token", ""),
                approval_status=data.get("approval_status", ""),
                parent_trace_thread_id=data.get("parent_trace_thread_id", ""),
            )
            for s in data.get("steps", []):
                log.steps.append(
                    TraceStep(
                        step_id=s["step_id"],
                        type=s["type"],
                        content=s["content"],
                        tool_name=s.get("tool_name"),
                        tool_args=s.get("tool_args"),
                        tool_result=s.get("tool_result"),
                        duration_ms=s.get("duration_ms"),
                    )
                )
            return log
        except (KeyError, TypeError):
            return None

    async def _save_trace(self, log: TraceLog) -> None:
        """异步保存 trace 到 SQLite（通过 to_thread 避免阻塞）"""
        data = json.dumps(self._trace_to_dict(log), ensure_ascii=False)

        def _write() -> None:
            db = self._get_db()
            try:
                db.execute(
                    "INSERT OR REPLACE INTO traces (thread_id, data) VALUES (?, ?)",
                    (log.thread_id, data),
                )
                db.commit()
            finally:
                db.close()

        await asyncio.to_thread(_write)

    async def _delete_trace_persisted(self, thread_id: str) -> None:
        """异步删除 trace"""

        def _delete() -> None:
            db = self._get_db()
            try:
                db.execute("DELETE FROM traces WHERE thread_id = ?", (thread_id,))
                db.commit()
            finally:
                db.close()

        await asyncio.to_thread(_delete)

    async def _rewrite_all_traces(self) -> None:
        """全量重写（清理后）"""
        pairs = [(tid, json.dumps(self._trace_to_dict(log), ensure_ascii=False)) for tid, log in self._traces.items()]

        def _bulk_write() -> None:
            db = self._get_db()
            try:
                db.execute("DELETE FROM traces")
                db.executemany("INSERT INTO traces (thread_id, data) VALUES (?, ?)", pairs)
                db.commit()
            finally:
                db.close()

        await asyncio.to_thread(_bulk_write)

    async def start_trace(self, thread_id: str, query: str) -> TraceLog:
        """Start a new trace."""
        async with self._lock:
            log = TraceLog(thread_id=thread_id, query=query)
            self._traces[thread_id] = log
            self._step_counters[thread_id] = 0
            # Clean old traces when approaching limit
            if len(self._traces) > self._max_traces * 0.9:
                await self._clean_old_traces(max_delete=100)
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

            # Serialize tool_args to JSON string
            args_str = None
            if tool_args is not None:
                args_str = json.dumps(tool_args, ensure_ascii=False)

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
                # 持久化到 SQLite
                await self._save_trace(log)
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
                # 删除 SQLite 中的记录
                await self._delete_trace_persisted(thread_id)
                return True
            return False

    async def list_traces(self) -> list[TraceLog]:
        """List all traces."""
        async with self._lock:
            return list(self._traces.values())

    async def _clean_old_traces(self, max_delete: int = 100) -> None:
        """Clean old and excess traces (must be called within lock)."""
        now = time.time()
        # 1. Clean expired traces
        to_delete = [tid for tid, log in self._traces.items() if now - log.created_at > self._max_age_seconds]
        for tid in to_delete[:max_delete]:
            del self._traces[tid]
            self._step_counters.pop(tid, None)
        # 2. Clean oldest if exceeding max_traces
        if len(self._traces) > self._max_traces:
            sorted_tids = sorted(self._traces, key=lambda t: self._traces[t].created_at)
            excess = len(self._traces) - self._max_traces
            for tid in sorted_tids[: min(excess, max_delete)]:
                del self._traces[tid]
                self._step_counters.pop(tid, None)
        # 3. 重写 SQLite
        await self._rewrite_all_traces()
