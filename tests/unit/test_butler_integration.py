"""Integration tests for butler modules."""

from __future__ import annotations

import asyncio

import pytest

from layerkg.butler import ButlerEvent
from layerkg.butler.consistency.guard import ConsistencyGuard
from layerkg.butler.event_bus import EventBus
from layerkg.butler.scheduler import HandlerResult, HandlerSpec, Scheduler


@pytest.fixture
def tmp_path(tmp_path):
    """Temporary path fixture."""
    return tmp_path


async def test_eventbus_scheduler_integration(tmp_path):
    """EventBus publish → Scheduler dispatch → Handler 执行 → Guard 审计。"""
    bus = EventBus()
    scheduler = Scheduler()
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))

    handler_results = []

    async def tracked_handler(event):
        await guard.log_operation("handle", "event", event.event_id, None, {"status": "done"}, "test_handler")
        handler_results.append(event.event_id)
        return HandlerResult(handler_id="h1", success=True, result_data={}, error=None)

    scheduler.register(
        HandlerSpec(
            handler_id="h1",
            event_types=["git_change"],
            handler_fn=tracked_handler,
        )
    )
    bus.subscribe("*", lambda e: asyncio.create_task(scheduler.dispatch(e)))

    event = ButlerEvent(event_type="git_change", payload={"files": ["a.py"]}, source="git")
    await bus.publish(event)
    await asyncio.sleep(0.1)

    assert len(handler_results) == 1
    entries = await guard.query(target_type="event", target_id=event.event_id)
    assert len(entries) == 1
    assert entries[0].operation == "handle"


async def test_eventbus_scheduler_multiple_handlers(tmp_path):
    """多个 handler 订阅不同事件类型，正确分发。"""
    bus = EventBus()
    scheduler = Scheduler()
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))

    git_results = []
    agent_results = []

    async def git_handler(event):
        await guard.log_operation("handle_git", "event", event.event_id, None, {"type": "git"}, "git_handler")
        git_results.append(event.event_id)
        return HandlerResult(handler_id="git_h", success=True, result_data={}, error=None)

    async def agent_handler(event):
        await guard.log_operation("handle_agent", "event", event.event_id, None, {"type": "agent"}, "agent_handler")
        agent_results.append(event.event_id)
        return HandlerResult(handler_id="agent_h", success=True, result_data={}, error=None)

    scheduler.register(
        HandlerSpec(
            handler_id="git_h",
            event_types=["git_change"],
            handler_fn=git_handler,
        )
    )
    scheduler.register(
        HandlerSpec(
            handler_id="agent_h",
            event_types=["agent_trace"],
            handler_fn=agent_handler,
        )
    )
    bus.subscribe("*", lambda e: asyncio.create_task(scheduler.dispatch(e)))

    git_event = ButlerEvent(event_type="git_change", payload={"files": ["a.py"]}, source="git")
    agent_event = ButlerEvent(event_type="agent_trace", payload={"action": "query"}, source="agent")

    await bus.publish(git_event)
    await bus.publish(agent_event)
    await asyncio.sleep(0.1)

    assert len(git_results) == 1
    assert len(agent_results) == 1

    all_entries = await guard.query(target_type="event")
    assert len(all_entries) == 2


async def test_eventbus_scheduler_retry_with_guard(tmp_path):
    """Handler 失败重试时，每次尝试都记录到 Guard。"""
    bus = EventBus()
    scheduler = Scheduler()
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))

    call_count = 0

    async def flaky_handler(event):
        nonlocal call_count
        call_count += 1
        await guard.log_operation("attempt", "event", event.event_id, None, {"attempt": call_count}, "flaky")
        if call_count < 3:
            raise RuntimeError("transient error")
        return HandlerResult(handler_id="flaky", success=True, result_data={}, error=None)

    scheduler.register(
        HandlerSpec(
            handler_id="flaky",
            event_types=["test"],
            handler_fn=flaky_handler,
            retry_count=2,
            retry_delay=0.01,
        )
    )
    bus.subscribe("*", lambda e: asyncio.create_task(scheduler.dispatch(e)))

    event = ButlerEvent(event_type="test", payload={}, source="test")
    await bus.publish(event)
    await asyncio.sleep(0.2)

    assert call_count == 3

    entries = await guard.query(target_type="event", target_id=event.event_id)
    assert len(entries) == 3
    assert entries[0].after == '{"attempt": 1}'
    assert entries[1].after == '{"attempt": 2}'
    assert entries[2].after == '{"attempt": 3}'


async def test_eventbus_scheduler_timeout_records_to_guard(tmp_path):
    """Handler 超时失败，Guard 记录审计。"""
    bus = EventBus()
    scheduler = Scheduler()
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))

    async def slow_handler(event):
        await guard.log_operation("start", "event", event.event_id, None, {}, "slow")
        await asyncio.sleep(10)
        return HandlerResult(handler_id="slow", success=True, result_data={}, error=None)

    scheduler.register(
        HandlerSpec(
            handler_id="slow",
            event_types=["test"],
            handler_fn=slow_handler,
            timeout=0.05,
            retry_count=0,
        )
    )
    bus.subscribe("*", lambda e: asyncio.create_task(scheduler.dispatch(e)))

    event = ButlerEvent(event_type="test", payload={}, source="test")
    await bus.publish(event)
    await asyncio.sleep(0.2)

    status = scheduler.get_status()
    assert status["slow"].failure_count == 1

    entries = await guard.query(target_type="event", target_id=event.event_id)
    assert len(entries) == 1
    assert entries[0].operation == "start"


async def test_eventbus_scheduler_get_status_integration():
    """Scheduler 状态正确反映 handler 调用情况。"""
    bus = EventBus()
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
    bus.subscribe("*", lambda e: asyncio.create_task(scheduler.dispatch(e)))

    status = scheduler.get_status()
    assert "h1" in status
    assert status["h1"].total_invocations == 0

    event = ButlerEvent(event_type="test", payload={}, source="test")
    await bus.publish(event)
    await asyncio.sleep(0.1)

    status = scheduler.get_status()
    assert status["h1"].total_invocations == 1
    assert status["h1"].success_count == 1
