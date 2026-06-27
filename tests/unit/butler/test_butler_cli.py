"""Tests for Butler CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from ontoagent.api.cli import main


@pytest.fixture
def isolated_config(tmp_path: Path) -> None:
    """Create isolated config with temporary data_dir."""
    import os

    # Set temporary data dir
    os.environ["ONTOAGENT_DATA_DIR"] = str(tmp_path)


def test_butler_update_with_mocked_engine(isolated_config):
    """Test butler update command with mocked engine."""
    # Mock handler result
    from ontoagent.butler.handlers.base import HandlerResult

    mock_result = HandlerResult(success=True, data={"changes_detected": 5})

    with patch("ontoagent.butler.engine.ButlerEngine") as mock_cls:
        engine_instance = MagicMock()
        engine_instance._running = False
        engine_instance.register_handler = MagicMock()
        engine_instance.start = AsyncMock()
        engine_instance.stop = AsyncMock()
        engine_instance.__aenter__ = AsyncMock(return_value=engine_instance)
        engine_instance.__aexit__ = AsyncMock()
        engine_instance.submit_event = AsyncMock(return_value=[mock_result])
        mock_cls.return_value = engine_instance

        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create a fake repo
            Path("test.py").write_text("print('hello')")

            result = runner.invoke(main, ["butler", "update", "--repo", ".", "--since", "HEAD~1"])

            # Command should succeed
            assert result.exit_code == 0

            # Output should contain the result data
            assert '"changes_detected": 5' in result.output


def test_butler_build_with_mocked_engine(isolated_config):
    """Test butler build command with mocked engine."""
    # Mock handler result
    from ontoagent.butler.handlers.base import HandlerResult

    mock_result = HandlerResult(
        success=True,
        data={
            "files_scanned": 10,
            "entities_created": 50,
            "relations_created": 100,
        },
    )

    with patch("ontoagent.butler.engine.ButlerEngine") as mock_cls:
        engine_instance = MagicMock()
        engine_instance._running = False
        engine_instance.register_handler = MagicMock()
        engine_instance.start = AsyncMock()
        engine_instance.stop = AsyncMock()
        engine_instance.__aenter__ = AsyncMock(return_value=engine_instance)
        engine_instance.__aexit__ = AsyncMock()
        engine_instance.submit_event = AsyncMock(return_value=[mock_result])
        mock_cls.return_value = engine_instance

        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create a fake repo
            Path("test.py").write_text("print('hello')")

            result = runner.invoke(main, ["butler", "build", "--repo", "."])

            # Command should succeed
            assert result.exit_code == 0

            # Output should contain the result data
            assert '"files_scanned": 10' in result.output


def test_butler_status_command(isolated_config):
    """Test butler status command."""
    with patch("ontoagent.butler.engine.ButlerEngine") as mock_cls:
        engine_instance = MagicMock()
        engine_instance._running = False
        engine_instance.status = AsyncMock(
            return_value={
                "running": False,
                "handlers": {},
                "scheduler_status": {},
                "skill_counts": {},
            }
        )
        mock_cls.return_value = engine_instance

        runner = CliRunner()
        result = runner.invoke(main, ["butler", "status"])

        # Command should succeed
        assert result.exit_code == 0

        # Output should contain status
        assert '"running": false' in result.output


def test_butler_serve_help():
    """Test butler serve --help output."""
    runner = CliRunner()
    result = runner.invoke(main, ["butler", "serve", "--help"])

    # Command should succeed
    assert result.exit_code == 0

    # Help should contain key options
    assert "--repo" in result.output
    assert "--poll-interval" in result.output


def test_butler_update_help():
    """Test butler update --help output."""
    runner = CliRunner()
    result = runner.invoke(main, ["butler", "update", "--help"])

    # Command should succeed
    assert result.exit_code == 0

    # Help should contain key options
    assert "--repo" in result.output
    assert "--since" in result.output


def test_butler_build_help():
    """Test butler build --help output."""
    runner = CliRunner()
    result = runner.invoke(main, ["butler", "build", "--help"])

    # Command should succeed
    assert result.exit_code == 0

    # Help should contain --repo option
    assert "--repo" in result.output


def test_butler_group_help():
    """Test butler command group help."""
    runner = CliRunner()
    result = runner.invoke(main, ["butler", "--help"])

    # Command should succeed
    assert result.exit_code == 0

    # Should list subcommands
    assert "serve" in result.output
    assert "update" in result.output
    assert "build" in result.output
    assert "status" in result.output


def test_butler_update_with_error(isolated_config):
    """Test butler update command with handler error."""
    # Mock handler error
    from ontoagent.butler.handlers.base import HandlerResult

    mock_result = HandlerResult(success=False, error="Test error message")

    with patch("ontoagent.butler.engine.ButlerEngine") as mock_cls:
        engine_instance = MagicMock()
        engine_instance._running = False
        engine_instance.register_handler = MagicMock()
        engine_instance.start = AsyncMock()
        engine_instance.stop = AsyncMock()
        engine_instance.__aenter__ = AsyncMock(return_value=engine_instance)
        engine_instance.__aexit__ = AsyncMock()
        engine_instance.submit_event = AsyncMock(return_value=[mock_result])
        mock_cls.return_value = engine_instance

        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create a fake repo
            Path("test.py").write_text("print('hello')")

            result = runner.invoke(main, ["butler", "update", "--repo", "."])

            # Command should indicate error (output or exit code)
            assert result.exit_code != 0 or "Error" in result.output or "error" in result.output
            assert "Error: Test error message" in result.output


def test_butler_build_with_error(isolated_config):
    """Test butler build command with handler error."""
    # Mock handler error
    from ontoagent.butler.handlers.base import HandlerResult

    mock_result = HandlerResult(success=False, error="Build failed")

    with patch("ontoagent.butler.engine.ButlerEngine") as mock_cls:
        engine_instance = MagicMock()
        engine_instance._running = False
        engine_instance.register_handler = MagicMock()
        engine_instance.start = AsyncMock()
        engine_instance.stop = AsyncMock()
        engine_instance.__aenter__ = AsyncMock(return_value=engine_instance)
        engine_instance.__aexit__ = AsyncMock()
        engine_instance.submit_event = AsyncMock(return_value=[mock_result])
        mock_cls.return_value = engine_instance

        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create a fake repo
            Path("test.py").write_text("print('hello')")

            result = runner.invoke(main, ["butler", "build", "--repo", "."])

            # Command should indicate error (output or exit code)
            assert result.exit_code != 0 or "Error" in result.output or "error" in result.output
            assert "Error: Build failed" in result.output


def test_engine_async_context_manager(isolated_config):
    """Test ButlerEngine async context manager support."""
    import asyncio

    from ontoagent.butler.engine import ButlerEngine
    from ontoagent.config import OntoAgentConfig

    async def test_ctx():
        config = OntoAgentConfig.from_env()
        engine = ButlerEngine(config)

        # Test context manager
        async with engine as e:
            assert e is engine
            assert engine._running is True

        # After exit, should be stopped
        assert engine._running is False

    asyncio.run(test_ctx())


def test_engine_start_stop_lifecycle(isolated_config):
    """Test ButlerEngine start/stop lifecycle."""
    import asyncio

    from ontoagent.butler.engine import ButlerEngine
    from ontoagent.config import OntoAgentConfig

    async def test_lifecycle():
        config = OntoAgentConfig.from_env()
        engine = ButlerEngine(config)

        # Initial state
        assert engine._running is False

        # Start
        await engine.start()
        assert engine._running is True

        # Double start is idempotent
        await engine.start()
        assert engine._running is True

        # Stop
        await engine.stop()
        assert engine._running is False

        # Double stop is idempotent
        await engine.stop()
        assert engine._running is False

    asyncio.run(test_lifecycle())
