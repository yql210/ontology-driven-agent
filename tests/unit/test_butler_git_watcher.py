"""Tests for GitWatcher."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from layerkg.butler.event_bus import ButlerEvent, EventBus
from layerkg.butler.watchers.git_watcher import GitWatcher


class TestGitWatcher:
    """Test GitWatcher."""

    def test_init(self) -> None:
        """Test GitWatcher initialization."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus, poll_interval=10.0, initial_scan=True)

        assert watcher._repo_path == repo_path
        assert watcher._bus is bus
        assert watcher._poll_interval == 10.0
        assert watcher._initial_scan is True
        assert watcher._last_ref is None
        assert watcher._task is None
        assert watcher._running is False

    def test_init_with_defaults(self) -> None:
        """Test GitWatcher initialization with default values."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus)

        assert watcher._poll_interval == 30.0
        assert watcher._initial_scan is False

    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self) -> None:
        """Test start() sets running flag and creates task."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus, poll_interval=0.1)

        await watcher.start()

        assert watcher._running is True
        assert watcher._task is not None

        await watcher.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self) -> None:
        """Test start() can be called multiple times."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus, poll_interval=0.1)

        await watcher.start()
        first_task = watcher._task
        first_running = watcher._running

        await watcher.start()
        second_task = watcher._task
        second_running = watcher._running

        assert first_running is True
        assert second_running is True
        # Task should be the same (idempotent)
        assert first_task is second_task

        await watcher.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running_flag(self) -> None:
        """Test stop() clears running flag and cancels task."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus, poll_interval=0.1)

        await watcher.start()
        await watcher.stop()

        assert watcher._running is False
        assert watcher._task is None

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self) -> None:
        """Test stop() can be called multiple times."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus, poll_interval=0.1)

        await watcher.start()
        await watcher.stop()
        # Should not raise
        await watcher.stop()

        assert watcher._running is False

    @pytest.mark.asyncio
    async def test_get_head_ref_success(self) -> None:
        """Test _get_head_ref returns correct hash."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus)

        mock_result = MagicMock()
        mock_result.stdout = "abc123def456\n"

        with (
            patch.object(Path, "exists", return_value=True),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            ref = watcher._get_head_ref()

            assert ref == "abc123def456"
            mock_run.assert_called_once_with(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )

    @pytest.mark.asyncio
    async def test_get_head_ref_repo_not_exists(self) -> None:
        """Test _get_head_ref raises when repo path doesn't exist."""
        bus = EventBus()
        repo_path = Path("/nonexistent/path")

        watcher = GitWatcher(repo_path=repo_path, bus=bus)

        with pytest.raises(RuntimeError, match="Repository path does not exist"):
            watcher._get_head_ref()

    @pytest.mark.asyncio
    async def test_get_head_ref_git_fails(self) -> None:
        """Test _get_head_ref raises when git command fails."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus)

        with (
            patch.object(Path, "exists", return_value=True),
            patch("subprocess.run", side_effect=FileNotFoundError),
            pytest.raises(FileNotFoundError),
        ):
            watcher._get_head_ref()

    @pytest.mark.asyncio
    async def test_poll_no_change_no_event(self) -> None:
        """Test _poll doesn't publish event when no change."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus)

        mock_result = MagicMock()
        mock_result.stdout = "abc123\n"

        published_events = []

        async def track_publish(event: ButlerEvent) -> None:
            published_events.append(event)

        original_publish = bus.publish
        bus.publish = track_publish  # type: ignore

        with patch.object(Path, "exists", return_value=True), patch("subprocess.run", return_value=mock_result):
            # First poll sets _last_ref
            await watcher._poll()
            assert watcher._last_ref == "abc123"
            assert len(published_events) == 0  # No initial_scan

            # Second poll with same ref
            published_events.clear()
            await watcher._poll()

            # No event should be published
            assert len(published_events) == 0

        bus.publish = original_publish

    @pytest.mark.asyncio
    async def test_poll_initial_scan_publishes_event(self) -> None:
        """Test _poll publishes event on first poll when initial_scan=True."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus, initial_scan=True)

        mock_result = MagicMock()
        mock_result.stdout = "abc123\n"

        published_events = []

        async def track_publish(event: ButlerEvent) -> None:
            published_events.append(event)

        original_publish = bus.publish
        bus.publish = track_publish  # type: ignore

        with patch.object(Path, "exists", return_value=True), patch("subprocess.run", return_value=mock_result):
            await watcher._poll()

            assert len(published_events) == 1
            assert published_events[0].event_type == "code.changed"
            assert published_events[0].payload["full_scan"] is True
            assert published_events[0].payload["repo_path"] == str(repo_path)
            assert published_events[0].source == "git_watcher"

        bus.publish = original_publish

    @pytest.mark.asyncio
    async def test_poll_detects_change_publishes_event(self) -> None:
        """Test _poll publishes event when HEAD changes."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus, initial_scan=False)

        published_events = []

        async def track_publish(event: ButlerEvent) -> None:
            published_events.append(event)

        original_publish = bus.publish
        bus.publish = track_publish  # type: ignore

        # First poll - no event (initial_scan=False)
        mock_result1 = MagicMock()
        mock_result1.stdout = "abc123\n"
        with patch.object(Path, "exists", return_value=True), patch("subprocess.run", return_value=mock_result1):
            await watcher._poll()
            assert len(published_events) == 0

        # Second poll - HEAD changed
        mock_result2 = MagicMock()
        mock_result2.stdout = "def456\n"
        with patch.object(Path, "exists", return_value=True), patch("subprocess.run", return_value=mock_result2):
            await watcher._poll()

            assert len(published_events) == 1
            assert published_events[0].event_type == "code.changed"
            assert published_events[0].payload["since"] == "abc123"
            assert published_events[0].payload["full_scan"] is False
            assert published_events[0].payload["repo_path"] == str(repo_path)
            assert published_events[0].source == "git_watcher"

        bus.publish = original_publish

    @pytest.mark.asyncio
    async def test_poll_handles_gracefully_on_error(self) -> None:
        """Test _poll handles errors gracefully without crashing."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus)

        # Should not raise
        with patch("subprocess.run", side_effect=FileNotFoundError):
            await watcher._poll()

        assert watcher._last_ref is None

    @pytest.mark.asyncio
    async def test_trigger_publishes_event(self) -> None:
        """Test trigger() publishes event immediately."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus)

        published_events = []

        async def track_publish(event: ButlerEvent) -> None:
            published_events.append(event)

        original_publish = bus.publish
        bus.publish = track_publish  # type: ignore

        await watcher.trigger(since="HEAD~3")

        assert len(published_events) == 1
        assert published_events[0].event_type == "code.changed"
        assert published_events[0].payload["since"] == "HEAD~3"
        assert published_events[0].payload["full_scan"] is False
        assert published_events[0].source == "git_watcher.manual"

        bus.publish = original_publish

    @pytest.mark.asyncio
    async def test_trigger_without_since_uses_last_ref(self) -> None:
        """Test trigger() uses _last_ref when since is not provided."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus)
        watcher._last_ref = "abc123"

        published_events = []

        async def track_publish(event: ButlerEvent) -> None:
            published_events.append(event)

        original_publish = bus.publish
        bus.publish = track_publish  # type: ignore

        await watcher.trigger()

        assert len(published_events) == 1
        assert published_events[0].payload["since"] == "abc123"
        assert published_events[0].payload["full_scan"] is False

        bus.publish = original_publish

    @pytest.mark.asyncio
    async def test_trigger_with_no_last_ref_publishes_full_scan(self) -> None:
        """Test trigger() publishes full_scan event when no last_ref."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus)
        watcher._last_ref = None

        published_events = []

        async def track_publish(event: ButlerEvent) -> None:
            published_events.append(event)

        original_publish = bus.publish
        bus.publish = track_publish  # type: ignore

        await watcher.trigger()

        assert len(published_events) == 1
        assert published_events[0].payload["since"] == ""
        assert published_events[0].payload["full_scan"] is True

        bus.publish = original_publish

    @pytest.mark.asyncio
    async def test_poll_loop_runs_continuously(self) -> None:
        """Test _poll_loop runs continuously until stopped."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus, poll_interval=0.05)

        poll_count = 0

        async def counting_poll() -> None:
            nonlocal poll_count
            poll_count += 1
            # Stop after 3 polls
            if poll_count >= 3:
                await watcher.stop()

        watcher._poll = counting_poll  # type: ignore

        await watcher.start()
        # Give some time for polls to happen
        await asyncio.sleep(0.2)

        # Should have polled at least 3 times
        assert poll_count >= 3

    @pytest.mark.asyncio
    async def test_poll_loop_handles_cancelled_error(self) -> None:
        """Test _poll_loop exits gracefully on CancelledError."""
        bus = EventBus()
        repo_path = Path("/test/repo")

        watcher = GitWatcher(repo_path=repo_path, bus=bus, poll_interval=0.01)

        await watcher.start()
        # Stop immediately
        await watcher.stop()

        assert watcher._running is False
