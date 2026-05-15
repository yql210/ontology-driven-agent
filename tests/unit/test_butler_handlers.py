from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from layerkg.butler.event_bus import ButlerEvent
from layerkg.butler.handlers.base import BaseHandler, HandlerContext, HandlerResult
from layerkg.config import LayerKGConfig


class DummyHandler(BaseHandler):
    """Dummy handler for testing BaseHandler."""

    @property
    def handler_id(self) -> str:
        return "test.dummy"

    @property
    def event_types(self) -> list[str]:
        return ["test.event"]

    async def handle(self, event: ButlerEvent, ctx: HandlerContext) -> HandlerResult:
        return HandlerResult(success=True, data={"test": "value"})


class TestHandlerResult:
    """Test HandlerResult dataclass."""

    def test_handler_result_success_default(self) -> None:
        """Test HandlerResult with default values."""
        result = HandlerResult(success=True)
        assert result.success is True
        assert result.data == {}
        assert result.error is None

    def test_handler_result_with_data(self) -> None:
        """Test HandlerResult with data."""
        result = HandlerResult(success=True, data={"key": "value"})
        assert result.success is True
        assert result.data == {"key": "value"}
        assert result.error is None

    def test_handler_result_with_error(self) -> None:
        """Test HandlerResult with error."""
        result = HandlerResult(success=False, error="Something went wrong")
        assert result.success is False
        assert result.error == "Something went wrong"
        assert result.data == {}


class TestHandlerContext:
    """Test HandlerContext dataclass."""

    def test_handler_context_init(self) -> None:
        """Test HandlerContext initialization."""
        config = LayerKGConfig()
        ctx = HandlerContext(config=config)
        assert ctx.config is config
        assert ctx.guard is None
        assert ctx.skill_store is None
        assert ctx._graph_store is None

    def test_handler_context_with_guard(self) -> None:
        """Test HandlerContext with guard."""
        config = LayerKGConfig()
        guard = MagicMock()
        ctx = HandlerContext(config=config, guard=guard)
        assert ctx.guard is guard
        assert ctx.skill_store is None

    def test_handler_context_with_skill_store(self) -> None:
        """Test HandlerContext with skill_store."""
        config = LayerKGConfig()
        skill_store = MagicMock()
        ctx = HandlerContext(config=config, skill_store=skill_store)
        assert ctx.skill_store is skill_store
        assert ctx.guard is None

    def test_handler_context_get_graph_store_lazy_init(self) -> None:
        """Test HandlerContext.get_graph_store lazy initialization."""
        config = LayerKGConfig()
        ctx = HandlerContext(config=config)

        # Verify _graph_store is None initially
        assert ctx._graph_store is None

        # Note: We don't actually call get_graph_store() here because it would
        # try to connect to Neo4j. The lazy initialization logic is tested
        # via integration tests.

    def test_handler_context_get_graph_store_cached(self) -> None:
        """Test HandlerContext.get_graph_store returns cached instance."""
        config = LayerKGConfig()
        ctx = HandlerContext(config=config)
        mock_store = MagicMock()
        ctx._graph_store = mock_store

        result = ctx.get_graph_store()
        assert result is mock_store


class TestBaseHandler:
    """Test BaseHandler abstract class."""

    def test_dummy_handler_properties(self) -> None:
        """Test DummyHandler implements BaseHandler properties."""
        handler = DummyHandler()
        assert handler.handler_id == "test.dummy"
        assert handler.event_types == ["test.event"]

    @pytest.mark.asyncio
    async def test_dummy_handler_handle(self) -> None:
        """Test DummyHandler.handle returns HandlerResult."""
        handler = DummyHandler()
        event = ButlerEvent(event_type="test.event", payload={"test": "data"})
        config = LayerKGConfig()
        ctx = HandlerContext(config=config)

        result = await handler.handle(event, ctx)
        assert result.success is True
        assert result.data == {"test": "value"}

    def test_base_handler_cannot_instantiate(self) -> None:
        """Test BaseHandler cannot be instantiated directly."""
        with pytest.raises(TypeError):
            BaseHandler()  # type: ignore


class TestKnowledgeUpdateHandler:
    """Test KnowledgeUpdateHandler."""

    def test_handler_properties(self) -> None:
        """Test KnowledgeUpdateHandler properties."""
        from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler

        handler = KnowledgeUpdateHandler()
        assert handler.handler_id == "knowledge.update"
        assert handler.event_types == ["code.changed"]

    @pytest.mark.asyncio
    async def test_handle_success(self) -> None:
        """Test KnowledgeUpdateHandler.handle success case."""
        from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler
        from layerkg.incremental_updater import UpdateReport

        handler = KnowledgeUpdateHandler()
        event = ButlerEvent(
            event_type="code.changed",
            payload={"since": "HEAD~3", "repo_path": "/path/to/repo", "full_scan": False},
        )

        config = LayerKGConfig()
        mock_guard = MagicMock()
        mock_guard.log_operation = AsyncMock()
        ctx = HandlerContext(config=config, guard=mock_guard)

        # Mock IncrementalUpdater
        mock_report = UpdateReport(
            changes_detected=5,
            nodes_added=10,
            nodes_updated=3,
            nodes_deleted=1,
            relations_rebuilt=15,
            vectors_updated=13,
            impacted_nodes_count=20,
            orphans_removed=0,
            changeset_id="cs-test123",
            elapsed_ms=123.45,
        )

        with patch("layerkg.incremental_updater.IncrementalUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.update = MagicMock(return_value=mock_report)
            mock_updater.close = MagicMock()
            mock_updater_class.return_value = mock_updater

            result = await handler.handle(event, ctx)

        assert result.success is True
        assert result.data["changes_detected"] == 5
        assert result.data["changeset_id"] == "cs-test123"
        assert result.error is None
        mock_updater.update.assert_called_once_with(since="HEAD~3", full_scan=False)
        mock_guard.log_operation.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_with_default_payload(self) -> None:
        """Test KnowledgeUpdateHandler.handle with default payload values."""
        from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler
        from layerkg.incremental_updater import UpdateReport

        handler = KnowledgeUpdateHandler()
        event = ButlerEvent(event_type="code.changed", payload={})

        config = LayerKGConfig()
        mock_guard = MagicMock()
        mock_guard.log_operation = AsyncMock()
        ctx = HandlerContext(config=config, guard=mock_guard)

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

        with patch("layerkg.incremental_updater.IncrementalUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.update = MagicMock(return_value=mock_report)
            mock_updater.close = MagicMock()
            mock_updater_class.return_value = mock_updater

            result = await handler.handle(event, ctx)

        assert result.success is True
        # Default values should be used
        mock_updater.update.assert_called_once_with(since="HEAD~1", full_scan=False)

    @pytest.mark.asyncio
    async def test_handle_exception(self) -> None:
        """Test KnowledgeUpdateHandler.handle exception handling."""
        from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler

        handler = KnowledgeUpdateHandler()
        event = ButlerEvent(event_type="code.changed", payload={"since": "HEAD~3"})

        config = LayerKGConfig()
        mock_guard = MagicMock()
        mock_guard.log_operation = AsyncMock()
        ctx = HandlerContext(config=config, guard=mock_guard)

        with patch("layerkg.incremental_updater.IncrementalUpdater") as mock_updater_cls:
            mock_updater = MagicMock()
            mock_updater.update = MagicMock(side_effect=RuntimeError("Connection failed"))
            mock_updater.close = MagicMock()
            mock_updater_cls.return_value = mock_updater

            result = await handler.handle(event, ctx)

        assert result.success is False
        assert "Connection failed" in result.error
        # Should still log operation even on failure
        mock_guard.log_operation.assert_called()

    @pytest.mark.asyncio
    async def test_handle_closes_updater(self) -> None:
        """Test KnowledgeUpdateHandler closes updater after use."""
        from layerkg.butler.handlers.knowledge_update import KnowledgeUpdateHandler
        from layerkg.incremental_updater import UpdateReport

        handler = KnowledgeUpdateHandler()
        event = ButlerEvent(event_type="code.changed", payload={"since": "HEAD~1"})

        config = LayerKGConfig()
        ctx = HandlerContext(config=config)

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

        with patch("layerkg.incremental_updater.IncrementalUpdater") as mock_updater_class:
            mock_updater = MagicMock()
            mock_updater.update = MagicMock(return_value=mock_report)
            mock_updater.close = MagicMock()
            mock_updater_class.return_value = mock_updater

            await handler.handle(event, ctx)

        # Ensure close was called
        mock_updater.close.assert_called_once()


class TestFullBuildHandler:
    """Test FullBuildHandler."""

    def test_handler_properties(self) -> None:
        """Test FullBuildHandler properties."""
        from layerkg.butler.handlers.knowledge_update import FullBuildHandler

        handler = FullBuildHandler()
        assert handler.handler_id == "knowledge.full_build"
        assert handler.event_types == ["build.full"]

    @pytest.mark.asyncio
    async def test_handle_success(self) -> None:
        """Test FullBuildHandler.handle success case."""
        from layerkg.builder import BuildResult
        from layerkg.butler.handlers.knowledge_update import FullBuildHandler

        handler = FullBuildHandler()
        event = ButlerEvent(event_type="build.full", payload={"repo_path": "/path/to/repo"})

        config = LayerKGConfig()
        mock_guard = MagicMock()
        mock_guard.log_operation = AsyncMock()
        ctx = HandlerContext(config=config, guard=mock_guard)

        mock_result = BuildResult(
            files_scanned=100,
            entities_created=500,
            relations_created=1200,
            concepts_created=50,
            semantic_relations_created=100,
            modules_created=10,
            doc_entities_created=20,
            skipped_semantic=False,
            aborted=False,
            elapsed_ms=5000.0,
        )

        with patch("layerkg.builder.LayerKGBuilder") as mock_builder_class:
            mock_builder = MagicMock()
            mock_builder.build = MagicMock(return_value=mock_result)
            mock_builder.close = MagicMock()
            mock_builder_class.return_value = mock_builder

            result = await handler.handle(event, ctx)

        assert result.success is True
        assert result.data["files_scanned"] == 100
        assert result.data["entities_created"] == 500
        assert result.error is None
        mock_builder.build.assert_called_once()
        mock_guard.log_operation.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_with_default_repo_path(self) -> None:
        """Test FullBuildHandler.handle with default repo_path (cwd)."""
        from layerkg.builder import BuildResult
        from layerkg.butler.handlers.knowledge_update import FullBuildHandler

        handler = FullBuildHandler()
        event = ButlerEvent(event_type="build.full", payload={})

        config = LayerKGConfig()
        ctx = HandlerContext(config=config)

        mock_result = BuildResult(
            files_scanned=0,
            entities_created=0,
            relations_created=0,
        )

        with (
            patch("layerkg.builder.LayerKGBuilder") as mock_builder_cls,
            patch("layerkg.butler.handlers.knowledge_update.Path") as mock_path_cls,
        ):
            mock_builder = MagicMock()
            mock_builder.build = MagicMock(return_value=mock_result)
            mock_builder.close = MagicMock()
            mock_builder_cls.return_value = mock_builder
            mock_path_cls.cwd.return_value = Path("/cwd")

            await handler.handle(event, ctx)

        # Should use cwd as default
        mock_path_cls.cwd.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_exception(self) -> None:
        """Test FullBuildHandler.handle exception handling."""
        from layerkg.butler.handlers.knowledge_update import FullBuildHandler

        handler = FullBuildHandler()
        event = ButlerEvent(event_type="build.full", payload={"repo_path": "/path/to/repo"})

        config = LayerKGConfig()
        mock_guard = MagicMock()
        mock_guard.log_operation = AsyncMock()
        ctx = HandlerContext(config=config, guard=mock_guard)

        with patch("layerkg.builder.LayerKGBuilder") as mock_builder_cls:
            mock_builder = MagicMock()
            mock_builder.build = MagicMock(side_effect=RuntimeError("Build failed"))
            mock_builder.close = MagicMock()
            mock_builder_cls.return_value = mock_builder

            result = await handler.handle(event, ctx)

        assert result.success is False
        assert "Build failed" in result.error
        # Should still log operation even on failure
        mock_guard.log_operation.assert_called()

    @pytest.mark.asyncio
    async def test_handle_closes_builder(self) -> None:
        """Test FullBuildHandler closes builder after use."""
        from layerkg.builder import BuildResult
        from layerkg.butler.handlers.knowledge_update import FullBuildHandler

        handler = FullBuildHandler()
        event = ButlerEvent(event_type="build.full", payload={"repo_path": "/path/to/repo"})

        config = LayerKGConfig()
        ctx = HandlerContext(config=config)

        mock_result = BuildResult(
            files_scanned=0,
            entities_created=0,
            relations_created=0,
        )

        with patch("layerkg.builder.LayerKGBuilder") as mock_builder_class:
            mock_builder = MagicMock()
            mock_builder.build = MagicMock(return_value=mock_result)
            mock_builder.close = MagicMock()
            mock_builder_class.return_value = mock_builder

            await handler.handle(event, ctx)

        # Ensure close was called
        mock_builder.close.assert_called_once()
