"""Tests for function registry."""

from __future__ import annotations

from layerkg.execution.action_types import ActionContext, FunctionResult
from layerkg.execution.functions.registry import (
    clear_registry,
    get_function,
    list_functions,
    register_function,
)


def _setup():
    """Clear registry before each test to avoid cross-contamination."""
    clear_registry()


def test_register_and_get():
    _setup()

    @register_function("test_fn_1")
    def my_fn(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True, data={"ok": True})

    fn = get_function("test_fn_1")
    assert fn is my_fn
    assert fn(ActionContext(graph_store=None)).data == {"ok": True}
    _setup()


def test_register_duplicate_raises():
    _setup()

    @register_function("dup_fn")
    def fn1(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True)

    try:

        @register_function("dup_fn")
        def fn2(ctx: ActionContext) -> FunctionResult:
            return FunctionResult(success=False)

        raise AssertionError("Expected ValueError")
    except ValueError as e:
        assert "already registered" in str(e)
    finally:
        _setup()


def test_get_not_found_returns_none():
    _setup()
    assert get_function("nonexistent") is None


def test_list_functions():
    _setup()

    @register_function("z_fn")
    def fn_z(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True)

    @register_function("a_fn")
    def fn_a(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True)

    assert list_functions() == ["a_fn", "z_fn"]
    _setup()


def test_clear_registry():
    _setup()

    @register_function("temp_fn")
    def fn_temp(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True)

    assert get_function("temp_fn") is not None
    clear_registry()
    assert get_function("temp_fn") is None
    assert list_functions() == []
