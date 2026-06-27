"""Tests for action_types data structures."""

from __future__ import annotations

from layerkg.execution.action_types import ActionConfig, ActionContext, ActionResult, FunctionResult


def test_function_result_defaults():
    result = FunctionResult(success=True)
    assert result.success is True
    assert result.data == {}
    assert result.error is None


def test_function_result_with_data():
    result = FunctionResult(success=True, data={"key": "value"}, error=None)
    assert result.data == {"key": "value"}


def test_action_context_call_function_success():
    """ActionContext.call_function dispatches to registered function."""

    def mock_fn(ctx: ActionContext, **kwargs) -> FunctionResult:
        return FunctionResult(success=True, data={"echo": ctx.match_data.get("x")})

    # We need to temporarily register via the registry
    from layerkg.execution.functions import registry

    registry._registry["mock_test_fn"] = mock_fn
    try:
        ctx = ActionContext(graph_store=None, match_data={"x": 42})
        result = ctx.call_function("mock_test_fn")
        assert result.success is True
        assert result.data == {"echo": 42}
    finally:
        del registry._registry["mock_test_fn"]


def test_action_context_call_function_not_found():
    ctx = ActionContext(graph_store=None)
    result = ctx.call_function("nonexistent_function")
    assert result.success is False
    assert "not registered" in result.error


def test_action_result_to_dict():
    r1 = FunctionResult(success=True, data={"a": 1})
    r2 = FunctionResult(success=False, error="fail")
    ar = ActionResult(
        success=False,
        action_name="refactor",
        results=[r1, r2],
        summary="partial",
        error="Function 'x' failed: fail",
    )
    d = ar.to_dict()
    assert d["success"] is False
    assert d["action_name"] == "refactor"
    assert len(d["results"]) == 2
    assert d["results"][0] == {"success": True, "data": {"a": 1}, "error": None}
    assert d["results"][1] == {"success": False, "data": {}, "error": "fail"}
    assert d["summary"] == "partial"
    assert d["error"] == "Function 'x' failed: fail"


def test_action_config_defaults():
    config = ActionConfig(name="test", intent_type="test", trigger_hint="hint", bind_to="code_entity")
    assert config.submission_criteria == []
    assert config.functions == []
    assert config.requires_approval is False


def test_action_result_defaults():
    ar = ActionResult(success=True, action_name="doc")
    assert ar.results == []
    assert ar.summary == ""
    assert ar.error is None
