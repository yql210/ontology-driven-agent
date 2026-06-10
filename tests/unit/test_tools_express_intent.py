"""Tests for express_intent tool in agent/tools.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from layerkg.action_types import ActionResult, FunctionResult


def test_express_intent_refactor() -> None:
    """express_intent calls ActionExecutor.execute and returns JSON result."""
    from layerkg.agent.tools import express_intent

    mock_result = ActionResult(
        success=True,
        action_name="refactor",
        results=[FunctionResult(success=True, data={"suggestion": "split"})],
        summary="操作 'refactor' 执行成功",
    )

    mock_executor = MagicMock()
    mock_executor.execute.return_value = mock_result

    with (
        patch("layerkg.agent.tools._get_action_executor", return_value=mock_executor),
        patch("layerkg.agent.tools.get_neo4j"),
    ):
        raw = express_intent.invoke({"intent_type": "refactor", "target": "Cache", "params": None})
        parsed = json.loads(raw)

    assert parsed["success"] is True
    assert parsed["action_name"] == "refactor"
    assert parsed["summary"] == "操作 'refactor' 执行成功"
    mock_executor.execute.assert_called_once_with("refactor", {"target": "Cache"})


def test_express_intent_unknown_type() -> None:
    """express_intent returns error JSON for unknown intent_type."""
    from layerkg.agent.tools import express_intent

    mock_result = ActionResult(
        success=False,
        action_name="unknown_action",
        error="未知操作类型: unknown_action",
    )

    mock_executor = MagicMock()
    mock_executor.execute.return_value = mock_result

    with (
        patch("layerkg.agent.tools._get_action_executor", return_value=mock_executor),
        patch("layerkg.agent.tools.get_neo4j"),
    ):
        raw = express_intent.invoke({"intent_type": "unknown_action", "target": "Cache", "params": None})
        parsed = json.loads(raw)

    assert parsed["success"] is False
    assert "未知操作类型" in parsed["error"]


def test_express_intent_exception_handling() -> None:
    """express_intent catches exceptions and returns error JSON."""
    from layerkg.agent.tools import express_intent

    mock_executor = MagicMock()
    mock_executor.execute.side_effect = RuntimeError("boom")

    with (
        patch("layerkg.agent.tools._get_action_executor", return_value=mock_executor),
        patch("layerkg.agent.tools.get_neo4j"),
    ):
        raw = express_intent.invoke({"intent_type": "refactor", "target": "Cache", "params": None})
        parsed = json.loads(raw)

    assert "error" in parsed
    assert "boom" in parsed["error"]


def test_all_tools_contains_express_intent() -> None:
    """ALL_TOOLS list contains express_intent, not ontology_action."""
    from layerkg.agent.tools import ALL_TOOLS

    tool_names = [t.name for t in ALL_TOOLS]
    assert "express_intent" in tool_names
    assert "ontology_action" not in tool_names


def test_action_executor_singleton() -> None:
    """_get_action_executor creates and caches a singleton."""
    import layerkg.agent.tools as tools_mod

    # Reset singleton
    tools_mod._action_executor = None

    mock_store = MagicMock()
    with patch("layerkg.action_executor.ActionExecutor") as mock_executor_cls:
        instance = MagicMock()
        mock_executor_cls.return_value = instance

        result1 = tools_mod._get_action_executor(mock_store)
        result2 = tools_mod._get_action_executor(mock_store)

        assert result1 is result2
        mock_executor_cls.assert_called_once_with(mock_store)

    # Cleanup
    tools_mod._action_executor = None
