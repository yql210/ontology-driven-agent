"""Boundary and edge case tests for Butler Engine system."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from ontoagent.butler.event_bus import ButlerEvent
from ontoagent.butler.handlers.base import HandlerResult
from ontoagent.butler.scheduler import HandlerResult as SchedulerHandlerResult
from ontoagent.butler.scheduler import HandlerSpec
from ontoagent.config import OntoAgentConfig


@pytest.fixture
def isolated_config(tmp_path):
    """Config with isolated data directory to prevent test cross-contamination."""
    config = OntoAgentConfig()
    config.data_dir = str(tmp_path / ".ontoagent")
    return config


# Test 1: test_skill_store_confidence_out_of_bounds
@pytest.mark.asyncio
async def test_skill_store_confidence_out_of_bounds(isolated_config):
    """SkillStore rejects confidence values outside [0.0, 1.0] range."""
    from ontoagent.butler.skills.store import SkillEntity, SkillLayer

    # Test confidence > 1.0
    with pytest.raises(ValueError, match=r"confidence must be between 0\.0 and 1\.0"):
        SkillEntity(
            skill_id="skill-1",
            name="Test Skill",
            layer=SkillLayer.RULE,
            pattern={"test": "pattern"},
            action={"test": "action"},
            confidence=1.5,
            source="test",
        )

    # Test confidence < 0.0
    with pytest.raises(ValueError, match=r"confidence must be between 0\.0 and 1\.0"):
        SkillEntity(
            skill_id="skill-2",
            name="Test Skill",
            layer=SkillLayer.RULE,
            pattern={"test": "pattern"},
            action={"test": "action"},
            confidence=-0.1,
            source="test",
        )

    # Test boundary values (should NOT raise)
    SkillEntity(
        skill_id="skill-3",
        name="Test Skill",
        layer=SkillLayer.RULE,
        pattern={"test": "pattern"},
        action={"test": "action"},
        confidence=0.0,
        source="test",
    )

    SkillEntity(
        skill_id="skill-4",
        name="Test Skill",
        layer=SkillLayer.RULE,
        pattern={"test": "pattern"},
        action={"test": "action"},
        confidence=1.0,
        source="test",
    )


# Test 2: test_eventbus_publish_no_subscriber
@pytest.mark.asyncio
async def test_eventbus_publish_no_subscriber():
    """EventBus publish with no subscribers should not raise any error."""
    from ontoagent.butler.event_bus import EventBus

    bus = EventBus()

    event = ButlerEvent(event_type="test.event", payload={"data": "test"}, source="test")

    # Should not raise any exception
    await bus.publish(event)

    # Sync publish should also work
    bus.publish_sync(event)


# Test 3: test_three_same_patterns_promote_skill_to_active
@pytest.mark.asyncio
async def test_three_same_patterns_promote_skill_to_active(isolated_config):
    """Three same patterns promote skill to active with confidence >= 0.8."""
    from ontoagent.butler.engine import ButlerEngine
    from ontoagent.butler.handlers.reflection import ReflectionHandler

    engine = ButlerEngine(isolated_config)
    handler = ReflectionHandler()
    engine.register_handler(handler)

    await engine.start()

    skill_store = engine._skill_store
    assert skill_store is not None

    # Submit 4 handler.completed events with the same pattern
    # First event creates skill (hit_count=0, confidence=0.5)
    # Each subsequent event increments hit_count and confidence
    # Need 4 events total to get hit_count=3 and confidence=0.8
    for _ in range(4):
        event = ButlerEvent(
            event_type="handler.completed",
            payload={
                "original_event_type": "code.changed",
                "handler_id": "knowledge.update",
                "success": True,
                "file_extension": ".py",
                "duration_ms": 100,
            },
            source="test",
        )
        await engine.submit_event(event)

    # Verify skill was promoted to active with confidence >= 0.8
    skills = await skill_store.search_by_pattern("signature", "code.changed:.py")
    assert len(skills) > 0

    skill = skills[0]
    # After 4 events: hit_count=3, confidence=0.5 + 3 * 0.1 = 0.8
    assert skill.confidence >= 0.8
    assert skill.status == "active"

    await engine.stop()


# Test 4: test_handler_exception_scheduler_retries_and_audits
@pytest.mark.asyncio
async def test_handler_exception_scheduler_retries_and_audits(isolated_config, tmp_path):
    """Handler exception triggers scheduler retry and logs to ConsistencyGuard."""
    from ontoagent.butler.consistency.guard import ConsistencyGuard
    from ontoagent.butler.scheduler import Scheduler

    scheduler = Scheduler()
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))

    call_count = 0

    async def flaky_handler(event):
        nonlocal call_count
        call_count += 1
        await guard.log_operation(
            op="handler_attempt",
            target_type="event",
            target_id=event.event_id,
            before=None,
            after={"attempt": call_count},
            operator="flaky_handler",
        )
        if call_count < 3:
            raise RuntimeError("transient error")
        return SchedulerHandlerResult(
            handler_id="flaky",
            success=True,
            result_data={},
            error=None,
        )

    scheduler.register(
        HandlerSpec(
            handler_id="flaky",
            event_types=["test"],
            handler_fn=flaky_handler,
            retry_count=2,
            retry_delay=0.01,
        )
    )

    event = ButlerEvent(event_type="test", payload={}, source="test")
    results = await scheduler.dispatch(event)

    # Should have retried and succeeded
    assert call_count == 3
    assert len(results) == 1
    assert results[0].success is True
    assert results[0].attempts == 3

    # Verify audit logs were created for each attempt
    entries = await guard.query(target_type="event", target_id=event.event_id)
    assert len(entries) == 3


# Test 5: test_engine_not_started_submit_returns_empty
@pytest.mark.asyncio
async def test_engine_not_started_submit_returns_empty(isolated_config):
    """ButlerEngine without start() returns empty list from submit_event()."""
    from ontoagent.butler.engine import ButlerEngine

    engine = ButlerEngine(isolated_config)

    event = ButlerEvent(event_type="test.event", payload={}, source="test")
    results = await engine.submit_event(event)

    # Should return empty list without error
    assert results == []
    assert engine._running is False


# Test 6: test_engine_context_manager
@pytest.mark.asyncio
async def test_engine_context_manager(isolated_config):
    """ButlerEngine async context manager starts engine on enter and stops on exit."""
    from ontoagent.butler.engine import ButlerEngine

    # Engine should not be running before context
    engine = ButlerEngine(isolated_config)
    assert engine._running is False

    async with engine:
        # Engine should be running inside context
        assert engine._running is True
        assert engine._guard is not None
        assert engine._skill_store is not None

        # Should be able to submit events
        event = ButlerEvent(event_type="test.event", payload={}, source="test")
        results = await engine.submit_event(event)
        # Results may be empty if no handlers, but shouldn't crash
        assert isinstance(results, list)

    # Engine should be stopped after exit
    assert engine._running is False
    assert engine._guard is None
    assert engine._skill_store is None


# Test 7: test_git_watcher_nonexistent_path
@pytest.mark.asyncio
async def test_git_watcher_nonexistent_path():
    """GitWatcher with nonexistent path handles trigger() gracefully."""
    from ontoagent.butler.event_bus import EventBus
    from ontoagent.butler.watchers.git_watcher import GitWatcher

    bus = EventBus()
    nonexistent_path = MagicMock()
    nonexistent_path.exists.return_value = False
    nonexistent_path.__str__ = lambda _: "/fake/nonexistent/path"

    watcher = GitWatcher(repo_path=nonexistent_path, bus=bus)

    # trigger() should not crash even with nonexistent path
    # It publishes an event without calling _get_head_ref
    await watcher.trigger(since="HEAD~1")

    # The event should have been published
    # We can verify by checking the bus's internal state indirectly
    # or by subscribing to the event type

    received_events = []

    async def track_events(event):
        received_events.append(event)

    bus.subscribe("code.changed", track_events)

    await watcher.trigger(since="HEAD~2")
    await asyncio.sleep(0.01)  # Small delay for queue processing

    # Should have received an event
    assert len(received_events) >= 1
    assert received_events[-1].event_type == "code.changed"


# Test 8: test_concurrent_events_processing
@pytest.mark.asyncio
async def test_concurrent_events_processing(isolated_config):
    """Concurrent event submission processes all events without race conditions."""
    from ontoagent.butler.engine import ButlerEngine
    from ontoagent.butler.handlers.base import BaseHandler, HandlerContext

    class CountingHandler(BaseHandler):
        """Test handler that counts invocations."""

        handler_id = "counter"
        event_types = ["test.concurrent"]

        def __init__(self):
            self.count = 0
            self._lock = asyncio.Lock()

        async def handle(self, event: ButlerEvent, ctx: HandlerContext) -> HandlerResult:
            async with self._lock:
                self.count += 1
                # Small delay to increase chance of race conditions
                await asyncio.sleep(0.01)
            return HandlerResult(success=True, data={"count": self.count})

    engine = ButlerEngine(isolated_config)
    handler = CountingHandler()
    engine.register_handler(handler)

    await engine.start()

    # Submit multiple events concurrently
    num_events = 10
    tasks = []
    for i in range(num_events):
        event = ButlerEvent(
            event_type="test.concurrent",
            payload={"index": i},
            source="test",
        )
        tasks.append(engine.submit_event(event))

    # Wait for all submissions
    results = await asyncio.gather(*tasks)

    # All events should have been processed
    assert len(results) == num_events

    # Handler should have been called exactly num_events times
    assert handler.count == num_events

    # Verify all results indicate success
    for result_list in results:
        assert len(result_list) == 1
        assert result_list[0].success is True

    await engine.stop()
