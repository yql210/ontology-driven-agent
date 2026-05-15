"""Scheduler for event handler dispatch."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from layerkg.butler.event_bus import ButlerEvent


@dataclass
class HandlerResult:
    """Result from handler execution."""

    handler_id: str
    success: bool
    result_data: dict
    error: str | None
    attempts: int = 1


@dataclass
class HandlerSpec:
    """Specification for an event handler."""

    handler_id: str
    event_types: list[str]
    handler_fn: callable
    retry_count: int = 0
    retry_delay: float = 0.1
    timeout: float | None = None
    max_concurrency: int = 10


@dataclass
class HandlerStatus:
    """Runtime status of a handler."""

    handler_id: str
    total_invocations: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_attempt_at: str | None = None


class Scheduler:
    """Event handler scheduler with retry and concurrency control."""

    def __init__(self) -> None:
        self._handlers: dict[str, HandlerSpec] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._status: dict[str, HandlerStatus] = {}

    def register(self, spec: HandlerSpec) -> None:
        """Register a handler."""
        self._handlers[spec.handler_id] = spec
        self._semaphores[spec.handler_id] = asyncio.Semaphore(spec.max_concurrency)
        self._status[spec.handler_id] = HandlerStatus(handler_id=spec.handler_id)

    async def dispatch(self, event: ButlerEvent) -> list[HandlerResult]:
        """Dispatch event to matching handlers."""
        results = []
        for _handler_id, spec in self._handlers.items():
            if event.event_type in spec.event_types:
                result = await self._execute_handler(spec, event)
                results.append(result)
        return results

    async def _execute_handler(self, spec: HandlerSpec, event: ButlerEvent) -> HandlerResult:
        """Execute a handler with retry logic."""
        max_attempts = max(1, spec.retry_count) + 1 if spec.retry_count > 0 else 1
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                if spec.timeout:
                    result = await asyncio.wait_for(spec.handler_fn(event), timeout=spec.timeout)
                else:
                    result = await spec.handler_fn(event)

                # BaseHandler.HandlerResult 用 data 字段；Scheduler.HandlerResult 用 result_data
                result_data = getattr(result, "data", None) or getattr(result, "result_data", None) or {}
                handler_success = getattr(result, "success", True)
                handler_error = getattr(result, "error", None) if not handler_success else None

                status = self._status[spec.handler_id]
                status.total_invocations += 1

                if handler_success:
                    status.success_count += 1
                else:
                    status.failure_count += 1

                return HandlerResult(
                    handler_id=spec.handler_id,
                    success=handler_success,
                    result_data=result_data,
                    error=handler_error,
                    attempts=attempt,
                )
            except TimeoutError:
                last_error = "Handler timeout"
                if attempt < max_attempts:
                    await asyncio.sleep(spec.retry_delay)
            except Exception as e:
                last_error = str(e)
                if attempt < max_attempts:
                    await asyncio.sleep(spec.retry_delay)

        status = self._status[spec.handler_id]
        status.total_invocations += 1
        status.failure_count += 1

        return HandlerResult(
            handler_id=spec.handler_id,
            success=False,
            result_data={},
            error=last_error,
            attempts=max_attempts,
        )

    def get_status(self) -> dict[str, HandlerStatus]:
        """Get status of all handlers."""
        return self._status.copy()
