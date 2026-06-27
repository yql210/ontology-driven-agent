"""Tests for ReflectionHandler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from layerkg.butler.event_bus import ButlerEvent
from layerkg.butler.handlers.base import HandlerContext
from layerkg.butler.skills.store import SkillEntity, SkillLayer
from layerkg.config import LayerKGConfig


class TestReflectionHandler:
    """Test ReflectionHandler."""

    def test_handler_properties(self) -> None:
        """Test ReflectionHandler properties."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()
        assert handler.handler_id == "butler.reflection"
        assert handler.event_types == ["handler.completed"]

    @pytest.mark.asyncio
    async def test_handle_new_pattern_creates_candidate_skill(self) -> None:
        """Test ReflectionHandler creates candidate skill for new pattern."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()

        # Create event with handler.completed payload
        event = ButlerEvent(
            event_type="handler.completed",
            payload={
                "original_event_type": "code.changed",
                "handler_id": "knowledge.update",
                "success": True,
                "file_extension": ".py",
                "duration_ms": 123.4,
            },
        )

        config = LayerKGConfig()
        mock_skill_store = MagicMock()
        mock_skill_store.search_by_pattern = AsyncMock(return_value=[])
        mock_skill_store.create = AsyncMock(return_value="skill-123")
        ctx = HandlerContext(config=config, skill_store=mock_skill_store)

        result = await handler.handle(event, ctx)

        assert result.success is True
        mock_skill_store.search_by_pattern.assert_called_once_with("signature", "code.changed:.py")

        # Verify created skill
        created_skill = mock_skill_store.create.call_args[0][0]
        assert created_skill.layer == SkillLayer.RULE
        assert created_skill.status == "candidate"
        assert created_skill.confidence == 0.5
        assert created_skill.pattern == {"signature": "code.changed:.py"}
        assert created_skill.hit_count == 0

    @pytest.mark.asyncio
    async def test_handle_existing_pattern_increments_hit_count(self) -> None:
        """Test ReflectionHandler increments hit_count for existing pattern."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()

        event = ButlerEvent(
            event_type="handler.completed",
            payload={
                "original_event_type": "code.changed",
                "handler_id": "knowledge.update",
                "success": True,
                "file_extension": ".py",
            },
        )

        config = LayerKGConfig()
        mock_skill_store = MagicMock()
        existing_skill = SkillEntity(
            skill_id="skill-123",
            name="Test Skill",
            layer=SkillLayer.RULE,
            pattern={"signature": "code.changed:.py"},
            action={"run": "knowledge.update"},
            confidence=0.5,
            source="reflection",
            status="candidate",
            hit_count=1,
        )
        mock_skill_store.search_by_pattern = AsyncMock(return_value=[existing_skill])
        mock_skill_store.increment_hit_count = AsyncMock(return_value=True)
        mock_skill_store.update = AsyncMock(return_value=True)
        ctx = HandlerContext(config=config, skill_store=mock_skill_store)

        result = await handler.handle(event, ctx)

        assert result.success is True
        mock_skill_store.increment_hit_count.assert_called_once_with("skill-123")
        # After increment: hit_count=2, confidence=0.7
        mock_skill_store.update.assert_called_once_with("skill-123", confidence=0.7)

    @pytest.mark.asyncio
    async def test_handle_promotes_to_active_when_confidence_high(self) -> None:
        """Test ReflectionHandler promotes skill to active when confidence >= 0.8."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()

        event = ButlerEvent(
            event_type="handler.completed",
            payload={
                "original_event_type": "code.changed",
                "handler_id": "knowledge.update",
                "success": True,
                "file_extension": ".py",
            },
        )

        config = LayerKGConfig()
        mock_skill_store = MagicMock()
        existing_skill = SkillEntity(
            skill_id="skill-123",
            name="Test Skill",
            layer=SkillLayer.RULE,
            pattern={"signature": "code.changed:.py"},
            action={"run": "knowledge.update"},
            confidence=0.7,
            source="reflection",
            status="candidate",
            hit_count=2,
        )
        mock_skill_store.search_by_pattern = AsyncMock(return_value=[existing_skill])
        mock_skill_store.increment_hit_count = AsyncMock(return_value=True)
        mock_skill_store.update = AsyncMock(return_value=True)
        ctx = HandlerContext(config=config, skill_store=mock_skill_store)

        result = await handler.handle(event, ctx)

        assert result.success is True
        # After increment: hit_count=3, confidence=0.8 -> should promote to active
        mock_skill_store.update.assert_called_once_with("skill-123", confidence=0.8, status="active")

    @pytest.mark.asyncio
    async def test_handle_caps_confidence_at_1_0(self) -> None:
        """Test ReflectionHandler caps confidence at 1.0."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()

        event = ButlerEvent(
            event_type="handler.completed",
            payload={
                "original_event_type": "code.changed",
                "handler_id": "knowledge.update",
                "success": True,
                "file_extension": ".py",
            },
        )

        config = LayerKGConfig()
        mock_skill_store = MagicMock()
        existing_skill = SkillEntity(
            skill_id="skill-123",
            name="Test Skill",
            layer=SkillLayer.RULE,
            pattern={"signature": "code.changed:.py"},
            action={"run": "knowledge.update"},
            confidence=0.95,
            source="reflection",
            status="active",
            hit_count=10,
        )
        mock_skill_store.search_by_pattern = AsyncMock(return_value=[existing_skill])
        mock_skill_store.increment_hit_count = AsyncMock(return_value=True)
        mock_skill_store.update = AsyncMock(return_value=True)
        ctx = HandlerContext(config=config, skill_store=mock_skill_store)

        result = await handler.handle(event, ctx)

        assert result.success is True
        # After increment: confidence should be capped at 1.0
        # Status is already active, so no status update needed
        mock_skill_store.update.assert_called_once_with("skill-123", confidence=1.0)

    @pytest.mark.asyncio
    async def test_handle_unknown_file_extension(self) -> None:
        """Test ReflectionHandler with unknown file extension."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()

        event = ButlerEvent(
            event_type="handler.completed",
            payload={
                "original_event_type": "build.full",
                "handler_id": "knowledge.full_build",
                "success": True,
                # file_extension missing
            },
        )

        config = LayerKGConfig()
        mock_skill_store = MagicMock()
        mock_skill_store.search_by_pattern = AsyncMock(return_value=[])
        mock_skill_store.create = AsyncMock(return_value="skill-456")
        ctx = HandlerContext(config=config, skill_store=mock_skill_store)

        result = await handler.handle(event, ctx)

        assert result.success is True
        # Should use "unknown" for missing file_extension
        mock_skill_store.search_by_pattern.assert_called_once_with("signature", "build.full:unknown")

    @pytest.mark.asyncio
    async def test_handle_no_skill_store_returns_error(self) -> None:
        """Test ReflectionHandler returns error when skill_store is None."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()

        event = ButlerEvent(
            event_type="handler.completed",
            payload={
                "original_event_type": "code.changed",
                "handler_id": "knowledge.update",
                "success": True,
                "file_extension": ".py",
            },
        )

        config = LayerKGConfig()
        ctx = HandlerContext(config=config, skill_store=None)

        result = await handler.handle(event, ctx)

        assert result.success is False
        assert "skill_store not available" in result.error

    @pytest.mark.asyncio
    async def test_generate_signature(self) -> None:
        """Test signature generation logic."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()

        # Test various inputs
        assert handler._generate_signature("code.changed", ".py") == "code.changed:.py"
        assert handler._generate_signature("build.full", "unknown") == "build.full:unknown"
        assert handler._generate_signature("handler.failed", ".java") == "handler.failed:.java"

    @pytest.mark.asyncio
    async def test_calculate_confidence(self) -> None:
        """Test confidence calculation."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()

        assert handler._calculate_confidence(0) == 0.5  # Base confidence
        assert handler._calculate_confidence(1) == 0.6
        assert handler._calculate_confidence(2) == 0.7
        assert handler._calculate_confidence(3) == 0.8
        assert handler._calculate_confidence(4) == 0.9
        assert handler._calculate_confidence(5) == 1.0
        assert handler._calculate_confidence(10) == 1.0  # Capped at 1.0

    @pytest.mark.asyncio
    async def test_should_promote_to_active(self) -> None:
        """Test promotion logic."""
        from layerkg.butler.handlers.reflection import ReflectionHandler

        handler = ReflectionHandler()

        assert handler._should_promote_to_active(0.5) is False
        assert handler._should_promote_to_active(0.7) is False
        assert handler._should_promote_to_active(0.8) is True
        assert handler._should_promote_to_active(0.9) is True
        assert handler._should_promote_to_active(1.0) is True
