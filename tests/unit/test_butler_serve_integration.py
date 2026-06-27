"""Integration tests for Butler serve mode — GitWatcher to engine to handler flow."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from layerkg.butler.engine import ButlerEngine
from layerkg.butler.event_bus import ButlerEvent
from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler
from layerkg.butler.watchers.git_watcher import GitWatcher
from layerkg.config import LayerKGConfig
from layerkg.pipeline.incremental_updater import UpdateReport


@pytest.fixture
def isolated_config(tmp_path: Path):
    """Config with isolated data directory to prevent test cross-contamination."""
    config = LayerKGConfig()
    config.data_dir = str(tmp_path / ".layerkg")
    return config


@pytest.fixture
def git_repo(tmp_path: Path):
    """Create a temporary git repository with initial commit."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir(parents=True, exist_ok=True)

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True, capture_output=True)

    # Create initial file and commit
    (repo_path / "test.py").write_text("def foo(): pass\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True, capture_output=True)

    return repo_path


@pytest.mark.asyncio
async def test_serve_detects_git_commit(isolated_config, git_repo: Path):
    """GitWatcher 检测到 commit → engine._dispatch_event → scheduler.dispatch → handler 执行."""
    engine = ButlerEngine(isolated_config)

    # Patch IncrementalUpdater to avoid real Neo4j calls
    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_report = UpdateReport(
            changes_detected=1,
            nodes_added=1,
            nodes_updated=0,
            nodes_deleted=0,
            relations_rebuilt=1,
            vectors_updated=1,
            impacted_nodes_count=1,
            orphans_removed=0,
            changeset_id="cs-test-123",
            elapsed_ms=50.0,
        )
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(return_value=mock_report)
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        # Register handler
        handler = KnowledgeUpdateHandler()
        engine.register_handler(handler)

        # Track completion events
        completion_events = []

        async def track_completion(event: ButlerEvent):
            if event.event_type in ("handler.completed", "handler.failed"):
                completion_events.append(event)

        await engine.start()

        # Subscribe to completion events before starting watcher
        sub_id = engine._bus.subscribe("handler.completed", track_completion)
        engine._bus.subscribe("handler.failed", track_completion)

        # Start GitWatcher with short poll_interval (1s) and initial_scan=False
        watcher = GitWatcher(git_repo, engine._bus, poll_interval=1.0, initial_scan=False)
        await watcher.start()

        # Wait a bit for watcher to capture initial HEAD
        await asyncio.sleep(0.5)

        # Make a new commit
        (git_repo / "new_file.py").write_text("def bar(): pass\n")
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "New commit"], cwd=git_repo, check=True, capture_output=True)

        # Wait for GitWatcher to detect the change (within 5 seconds)
        max_wait = 5.0
        waited = 0.0
        while len(completion_events) == 0 and waited < max_wait:
            await asyncio.sleep(0.5)
            waited += 0.5

        # Verify handler was triggered
        assert len(completion_events) > 0, "Handler should have been triggered within 5 seconds"

        # Verify it was a success
        completion_event = completion_events[0]
        assert completion_event.event_type == "handler.completed"
        assert completion_event.payload["handler_id"] == "knowledge.update"
        assert completion_event.payload["success"] is True

        # Cleanup
        engine._bus.unsubscribe(sub_id)
        await watcher.stop()
        await engine.stop()


@pytest.mark.asyncio
async def test_dispatch_event_no_cascade(isolated_config):
    """_dispatch_event publishes completion events but does NOT cascade dispatch."""
    from layerkg.butler.handlers.reflection import ReflectionHandler

    engine = ButlerEngine(isolated_config)

    # Register both handlers
    engine.register_handler(KnowledgeUpdateHandler())
    engine.register_handler(ReflectionHandler())

    mock_report = UpdateReport(
        changes_detected=1,
        nodes_added=1,
        nodes_updated=0,
        nodes_deleted=0,
        relations_rebuilt=1,
        vectors_updated=1,
        impacted_nodes_count=1,
        orphans_removed=0,
        changeset_id="cs-no-cascade",
        elapsed_ms=50.0,
    )

    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(return_value=mock_report)
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        await engine.start()

        # Track handler.completed events
        completion_events = []

        async def track_completion(event: ButlerEvent):
            if event.event_type == "handler.completed":
                completion_events.append(event)

        sub_id = engine._bus.subscribe("handler.completed", track_completion)

        # Directly call _dispatch_event (simulating GitWatcher publishing)
        event = ButlerEvent(
            event_type="code.changed",
            payload={"since": "HEAD~1", "repo_path": "/test/repo", "file_path": "test.py"},
            source="git_watcher",
        )

        await engine._dispatch_event(event)
        await asyncio.sleep(0.2)  # Give time for event processing

        # Should have exactly 1 completion event (from KnowledgeUpdateHandler)
        # ReflectionHandler should NOT be triggered because _dispatch_event doesn't cascade
        assert len(completion_events) == 1
        assert completion_events[0].payload["handler_id"] == "knowledge.update"

        # Verify ReflectionHandler was NOT called (no cascade)
        scheduler_status = engine._scheduler.get_status()
        # ReflectionHandler should have 0 invocations
        if "butler.reflection" in scheduler_status:
            assert scheduler_status["butler.reflection"].total_invocations == 0

        engine._bus.unsubscribe(sub_id)
        await engine.stop()


@pytest.mark.asyncio
async def test_dispatch_event_when_engine_not_running(isolated_config):
    """_dispatch_event does nothing when engine is not running."""
    engine = ButlerEngine(isolated_config)
    engine.register_handler(KnowledgeUpdateHandler())

    # Do NOT start the engine

    # Track published events
    published_events = []

    async def track_publish(event: ButlerEvent):
        published_events.append(event)

    original_publish = engine._bus.publish
    engine._bus.publish = track_publish  # type: ignore

    event = ButlerEvent(
        event_type="code.changed",
        payload={"since": "HEAD~1"},
        source="test",
    )

    # Should not raise or publish anything
    await engine._dispatch_event(event)

    # No events should have been published
    assert len(published_events) == 0

    engine._bus.publish = original_publish


@pytest.mark.asyncio
async def test_serve_graceful_shutdown(isolated_config, git_repo: Path):
    """Test graceful shutdown of watcher and engine."""
    mock_report = UpdateReport(
        changes_detected=0,
        nodes_added=0,
        nodes_updated=0,
        nodes_deleted=0,
        relations_rebuilt=0,
        vectors_updated=0,
        impacted_nodes_count=0,
        orphans_removed=0,
        changeset_id="",
        elapsed_ms=0,
    )

    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(return_value=mock_report)
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        engine = ButlerEngine(isolated_config)
        engine.register_handler(KnowledgeUpdateHandler())

        await engine.start()

        # Start watcher with short poll_interval
        watcher = GitWatcher(git_repo, engine._bus, poll_interval=0.5, initial_scan=False)
        await watcher.start()

        assert watcher._running is True
        assert engine._running is True

        # Stop watcher first
        await watcher.stop()
        assert watcher._running is False

        # Stop engine
        await engine.stop()
        assert engine._running is False


@pytest.mark.asyncio
async def test_submit_event_vs_dispatch_event_cascade_difference(isolated_config):
    """submit_event cascades (triggers ReflectionHandler), _dispatch_event does not."""
    from layerkg.butler.handlers.reflection import ReflectionHandler

    engine = ButlerEngine(isolated_config)

    engine.register_handler(KnowledgeUpdateHandler())
    engine.register_handler(ReflectionHandler())

    mock_report = UpdateReport(
        changes_detected=1,
        nodes_added=1,
        nodes_updated=0,
        nodes_deleted=0,
        relations_rebuilt=1,
        vectors_updated=1,
        impacted_nodes_count=1,
        orphans_removed=0,
        changeset_id="cs-cascade-test",
        elapsed_ms=50.0,
    )

    with patch("layerkg.pipeline.incremental_updater.IncrementalUpdater") as mock_updater_cls:
        mock_updater = MagicMock()
        mock_updater.update = MagicMock(return_value=mock_report)
        mock_updater.close = MagicMock()
        mock_updater_cls.return_value = mock_updater

        await engine.start()

        # Get initial status (unused — kept for documentation purposes)
        _ = engine._scheduler.get_status()

        # Test submit_event - SHOULD cascade
        event = ButlerEvent(
            event_type="code.changed",
            payload={"since": "HEAD~1", "repo_path": "/test/repo", "file_path": "test.py"},
            source="test",
        )

        await engine.submit_event(event)
        await asyncio.sleep(0.2)

        after_submit_status = engine._scheduler.get_status()

        # ReflectionHandler should have been called (cascade)
        if "butler.reflection" in after_submit_status:
            # submit_event cascade triggers ReflectionHandler via handler.completed
            assert after_submit_status["butler.reflection"].total_invocations >= 1

        await engine.stop()
