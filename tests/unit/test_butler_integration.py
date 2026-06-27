"""Integration tests for butler modules."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from layerkg.butler import ButlerEvent
from layerkg.butler.consistency.guard import ConsistencyGuard
from layerkg.butler.engine import ButlerEngine
from layerkg.butler.event_bus import EventBus
from layerkg.butler.scheduler import HandlerResult, HandlerSpec, Scheduler
from layerkg.config import LayerKGConfig


@pytest.fixture
def isolated_config(tmp_path):
    """Config with isolated data directory to prevent test cross-contamination."""
    config = LayerKGConfig()
    config.data_dir = str(tmp_path / ".layerkg")
    return config


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


# New tests for Task 2-1: Event dispatch loop integration


@pytest.mark.asyncio
async def test_code_changed_triggers_knowledge_update_and_audit(isolated_config):
    """Publish code.changed → KnowledgeUpdateHandler triggered → audit logged."""
    from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler
    from layerkg.pipeline.incremental_updater import UpdateReport

    engine = ButlerEngine(isolated_config)

    handler = KnowledgeUpdateHandler()
    engine.register_handler(handler)

    mock_report = UpdateReport(
        changes_detected=3,
        nodes_added=5,
        nodes_updated=2,
        nodes_deleted=0,
        relations_rebuilt=7,
        vectors_updated=5,
        impacted_nodes_count=10,
        orphans_removed=0,
        changeset_id="cs-test-123",
        elapsed_ms=100.0,
    )

    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(return_value=mock_report)
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        await engine.start()

        # Publish code.changed event
        event = ButlerEvent(
            event_type="code.changed",
            payload={"since": "HEAD~1", "repo_path": "/test/repo", "file_path": "test.py"},
            source="test",
        )
        results = await engine.submit_event(event)

        # Should have exactly 1 result from KnowledgeUpdateHandler
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].handler_id == "knowledge.update"
        assert results[0].result_data["changeset_id"] == "cs-test-123"
        assert results[0].result_data["changes_detected"] == 3

        # Verify audit was logged
        guard = engine._guard
        assert guard is not None
        entries = await guard.query(target_type="repo", target_id="/test/repo")
        assert len(entries) == 1
        assert entries[0].operation == "knowledge_update"

        await engine.stop()


@pytest.mark.asyncio
async def test_handler_completed_triggers_reflection_and_skill_creation(isolated_config):
    """Publish handler.completed → ReflectionHandler triggered → skill created."""
    from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler
    from layerkg.butler.handlers.reflection import ReflectionHandler
    from layerkg.pipeline.incremental_updater import UpdateReport

    engine = ButlerEngine(isolated_config)

    knowledge_handler = KnowledgeUpdateHandler()
    reflection_handler = ReflectionHandler()

    engine.register_handler(knowledge_handler)
    engine.register_handler(reflection_handler)

    mock_report = UpdateReport(
        changes_detected=1,
        nodes_added=2,
        nodes_updated=0,
        nodes_deleted=0,
        relations_rebuilt=2,
        vectors_updated=1,
        impacted_nodes_count=3,
        orphans_removed=0,
        changeset_id="cs-123",
        elapsed_ms=50.0,
    )

    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(return_value=mock_report)
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        await engine.start()

        # Track completion events via direct async callback
        completion_events = []

        async def track_completion_wrapper(event):
            if event.event_type == "handler.completed":
                completion_events.append(event)

        sub_id = engine._bus.subscribe("handler.completed", track_completion_wrapper)

        # Publish code.changed event
        event = ButlerEvent(
            event_type="code.changed",
            payload={"since": "HEAD~1", "repo_path": "/test/repo", "file_path": "test.py"},
            source="test",
        )
        await engine.submit_event(event)
        await asyncio.sleep(0.3)  # Give time for EventBus consumer to process

        # Verify handler.completed was published
        assert len(completion_events) > 0

        # Verify skill was created (via ReflectionHandler cascade in submit_event)
        skill_store = engine._skill_store
        assert skill_store is not None

        skills = await skill_store.search_by_pattern("signature", "code.changed:.py")
        assert len(skills) > 0
        assert skills[0].status == "candidate"
        assert skills[0].confidence == 0.5

        engine._bus.unsubscribe(sub_id)
        await engine.stop()


@pytest.mark.asyncio
async def test_unknown_event_type_no_handler_responds(isolated_config):
    """Publish unknown event type → no handler responds, no error."""
    from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler
    from layerkg.butler.handlers.reflection import ReflectionHandler

    engine = ButlerEngine(isolated_config)

    engine.register_handler(KnowledgeUpdateHandler())
    engine.register_handler(ReflectionHandler())

    mock_report = MagicMock()
    mock_report.to_dict.return_value = {}

    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(return_value=mock_report)
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        await engine.start()

        # Publish unknown event type
        event = ButlerEvent(
            event_type="unknown.event.type",
            payload={"data": "test"},
            source="test",
        )
        results = await engine.submit_event(event)

        # Should have no results
        assert len(results) == 0

        # Engine should still be running
        assert engine._running is True

        await engine.stop()


@pytest.mark.asyncio
async def test_code_changed_to_handler_completed_flow(isolated_config):
    """Test full flow: code.changed → handler.completed → ReflectionHandler."""
    from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler
    from layerkg.butler.handlers.reflection import ReflectionHandler
    from layerkg.pipeline.incremental_updater import UpdateReport

    engine = ButlerEngine(isolated_config)

    engine.register_handler(KnowledgeUpdateHandler())
    engine.register_handler(ReflectionHandler())

    mock_report = UpdateReport(
        changes_detected=1,
        nodes_added=2,
        nodes_updated=0,
        nodes_deleted=0,
        relations_rebuilt=2,
        vectors_updated=1,
        impacted_nodes_count=3,
        orphans_removed=0,
        changeset_id="cs-flow-123",
        elapsed_ms=50.0,
    )

    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(return_value=mock_report)
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        await engine.start()

        # Publish code.changed event
        code_changed_event = ButlerEvent(
            event_type="code.changed",
            payload={"since": "HEAD~1", "repo_path": "/test/repo", "file_path": "src/test.py"},
            source="test",
        )
        results = await engine.submit_event(code_changed_event)
        await asyncio.sleep(0.2)

        # KnowledgeUpdateHandler should have succeeded
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].handler_id == "knowledge.update"

        # Check that ReflectionHandler created a skill
        skill_store = engine._skill_store
        assert skill_store is not None

        skills = await skill_store.search_by_pattern("signature", "code.changed:.py")
        assert len(skills) > 0

        await engine.stop()


@pytest.mark.asyncio
async def test_multiple_handlers_same_event_type(isolated_config):
    """Test that multiple handlers subscribing to the same event type all get called."""
    from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler

    engine = ButlerEngine(isolated_config)

    engine.register_handler(KnowledgeUpdateHandler())

    mock_report = MagicMock()
    mock_report.to_dict.return_value = {}

    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(return_value=mock_report)
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        await engine.start()

        # Subscribe a mock handler that also listens to code.changed
        mock_called = []

        async def mock_handler(event):
            mock_called.append(event.event_id)
            return HandlerResult(handler_id="mock.handler", success=True, result_data={}, error=None)

        # Directly register with scheduler
        from layerkg.butler.scheduler import HandlerSpec

        engine._scheduler.register(
            HandlerSpec(
                handler_id="mock.handler",
                event_types=["code.changed"],
                handler_fn=mock_handler,
            )
        )

        # Publish code.changed event
        event = ButlerEvent(
            event_type="code.changed",
            payload={"since": "HEAD~1"},
            source="test",
        )
        results = await engine.submit_event(event)

        # Should have 2 results: knowledge.update and mock.handler
        assert len(results) == 2
        handler_ids = {r.handler_id for r in results}
        assert "knowledge.update" in handler_ids
        assert "mock.handler" in handler_ids

        await engine.stop()


@pytest.mark.asyncio
async def test_handler_failure_publishes_failed_event(isolated_config):
    """Test that handler failure publishes handler.failed event."""
    from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler

    engine = ButlerEngine(isolated_config)

    engine.register_handler(KnowledgeUpdateHandler())

    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(side_effect=RuntimeError("Connection failed"))
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        await engine.start()

        # Track failed events
        failed_events = []

        async def track_failed(event):
            if event.event_type == "handler.failed":
                failed_events.append(event)

        sub_id = engine._bus.subscribe("handler.failed", track_failed)

        # Publish code.changed event that will fail
        event = ButlerEvent(
            event_type="code.changed",
            payload={"since": "HEAD~1"},
            source="test",
        )
        results = await engine.submit_event(event)
        await asyncio.sleep(0.3)

        # Should have failed result
        assert len(results) == 1
        assert results[0].success is False
        assert "Connection failed" in (results[0].error or "")

        # Verify handler.failed was published
        assert len(failed_events) > 0
        assert failed_events[0].payload["handler_id"] == "knowledge.update"
        assert failed_events[0].payload["success"] is False
        assert "Connection failed" in failed_events[0].payload["error"]

        engine._bus.unsubscribe(sub_id)
        await engine.stop()
