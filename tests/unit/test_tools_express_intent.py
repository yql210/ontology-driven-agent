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
        mock_executor_cls.assert_called_once_with(
            mock_store, function_runner=mock_executor_cls.call_args[1]["function_runner"]
        )

    # Cleanup
    tools_mod._action_executor = None


class TestGap1FunctionRunnerInjection:
    """Gap 1: express_intent must inject FunctionRunner into ActionExecutor."""

    def test_action_executor_receives_function_runner(self) -> None:
        """ActionExecutor 单例应注入 FunctionRunner."""
        import layerkg.agent.tools as tools_mod

        tools_mod._action_executor = None
        mock_store = MagicMock()

        try:
            executor = tools_mod._get_action_executor(mock_store)
            assert executor._function_runner is not None, (
                "ActionExecutor was created without a FunctionRunner — "
                "functions will bypass retry/circuit-breaker/fallback"
            )
        finally:
            tools_mod._action_executor = None

    def test_function_runner_is_shared_singleton(self) -> None:
        """Repeated _get_action_executor calls share the same FunctionRunner."""
        import layerkg.agent.tools as tools_mod

        mock_store = MagicMock()

        tools_mod._action_executor = None
        try:
            executor1 = tools_mod._get_action_executor(mock_store)
            runner1 = executor1._function_runner

            tools_mod._action_executor = None
            executor2 = tools_mod._get_action_executor(mock_store)
            runner2 = executor2._function_runner

            assert runner1 is runner2, "FunctionRunner should be a shared singleton"
        finally:
            tools_mod._action_executor = None


class TestGap2GeneralFunctionsRegistered:
    """Gap 2: general.py functions (query_entity, etc.) must be in registry at import time."""

    def test_general_functions_registered_on_tools_import(self) -> None:
        """Importing tools.py should trigger general function registration."""
        from layerkg.functions.registry import list_functions

        expected = [
            "query_entity",
            "update_entity",
            "create_entity",
            "create_relation",
            "check_condition",
            "send_notification",
        ]

        # Import tools triggers the chain
        import layerkg.agent.tools  # noqa: F401

        registered = list_functions()
        for name in expected:
            assert name in registered, f"General function '{name}' not in registry. Registered: {registered}"

    def test_query_entity_callable_from_registry(self) -> None:
        """query_entity should be callable from the registry."""
        import layerkg.agent.tools  # noqa: F401
        from layerkg.action_types import ActionContext
        from layerkg.functions.registry import get_function

        fn = get_function("query_entity")
        assert fn is not None
        ctx = ActionContext(
            graph_store=None,
            match_data={"target": "foo", "entity": {"name": "foo", "id": "e1"}},
        )
        result = fn(ctx)
        assert result.success is True
        assert result.data["name"] == "foo"

    def test_check_condition_callable_from_registry(self) -> None:
        """check_condition should be callable from the registry."""
        import layerkg.agent.tools  # noqa: F401
        from layerkg.action_types import ActionContext
        from layerkg.functions.registry import get_function

        fn = get_function("check_condition")
        assert fn is not None
        ctx = ActionContext(
            graph_store=None,
            match_data={"entity": {"lines": 200}},
        )
        result = fn(ctx, field="lines", operator=">", value=100)
        assert result.success is True
        assert result.data["condition_met"] is True
