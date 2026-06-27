"""Unit tests for butler scheduler module."""

from __future__ import annotations

import asyncio

from layerkg.butler import ButlerEvent
from layerkg.butler.scheduler import HandlerResult, HandlerSpec, Scheduler


async def test_scheduler_dispatch():
    """Scheduler 将事件分发到匹配的 handler。"""
    scheduler = Scheduler()
    results = []

    async def mock_handler(event):
        results.append(event.event_id)
        return HandlerResult(handler_id="h1", success=True, result_data={"ok": True}, error=None)

    scheduler.register(
        HandlerSpec(
            handler_id="h1",
            event_types=["git_change"],
            handler_fn=mock_handler,
        )
    )

    event = ButlerEvent(event_type="git_change", payload={}, source="test")
    dispatch_results = await scheduler.dispatch(event)
    assert len(dispatch_results) == 1
    assert dispatch_results[0].success is True
    assert dispatch_results[0].attempts == 1
    assert results[0] == event.event_id


async def test_scheduler_type_filter():
    """Scheduler 不匹配的 event_type 不触发 handler。"""
    scheduler = Scheduler()
    called = []

    async def mock_handler(event):
        called.append(True)
        return HandlerResult(handler_id="h1", success=True, result_data={}, error=None)

    scheduler.register(
        HandlerSpec(
            handler_id="h1",
            event_types=["git_change"],
            handler_fn=mock_handler,
        )
    )

    event = ButlerEvent(event_type="agent_trace", payload={}, source="test")
    results = await scheduler.dispatch(event)
    assert len(results) == 0
    assert len(called) == 0


async def test_scheduler_retry():
    """Handler 失败时自动重试，记录 attempts。"""
    scheduler = Scheduler()
    call_count = 0

    async def flaky_handler(event):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient error")
        return HandlerResult(handler_id="h1", success=True, result_data={}, error=None)

    scheduler.register(
        HandlerSpec(
            handler_id="h1",
            event_types=["test"],
            handler_fn=flaky_handler,
            retry_count=3,
            retry_delay=0.01,
        )
    )

    event = ButlerEvent(event_type="test", payload={}, source="test")
    results = await scheduler.dispatch(event)
    assert results[0].success is True
    assert results[0].attempts == 3
    assert call_count == 3


async def test_scheduler_timeout():
    """Handler 超时后标记失败。"""
    scheduler = Scheduler()

    async def slow_handler(event):
        await asyncio.sleep(10)

    scheduler.register(
        HandlerSpec(
            handler_id="h1",
            event_types=["test"],
            handler_fn=slow_handler,
            timeout=0.05,
            retry_count=0,
        )
    )

    event = ButlerEvent(event_type="test", payload={}, source="test")
    results = await scheduler.dispatch(event)
    assert results[0].success is False
    assert "timeout" in results[0].error.lower() or "Timeout" in results[0].error


async def test_scheduler_get_status():
    """get_status 返回各 handler 的运行统计。"""
    scheduler = Scheduler()

    async def handler(event):
        return HandlerResult(handler_id="h1", success=True, result_data={}, error=None)

    scheduler.register(
        HandlerSpec(
            handler_id="h1",
            event_types=["test"],
            handler_fn=handler,
        )
    )

    status = scheduler.get_status()
    assert "h1" in status
    assert status["h1"].total_invocations == 0

    event = ButlerEvent(event_type="test", payload={}, source="test")
    await scheduler.dispatch(event)
    status = scheduler.get_status()
    assert status["h1"].total_invocations == 1
