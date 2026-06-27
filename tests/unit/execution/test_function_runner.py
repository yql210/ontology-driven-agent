from __future__ import annotations

from ontoagent.execution.action_types import ActionContext, FunctionResult
from ontoagent.execution.circuit_breaker import CircuitBreaker
from ontoagent.execution.execution_policy import ExecutionPolicy
from ontoagent.execution.function_runner import FunctionRunner
from ontoagent.execution.functions.registry import clear_registry, register_function


def _setup_function(name: str, fn):
    clear_registry()
    register_function(name)(fn)


def _make_ctx() -> ActionContext:
    return ActionContext(graph_store=None)


def test_run_success():
    """Registered function executes and returns its FunctionResult."""

    def my_fn(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True, data={"ok": True})

    _setup_function("my_fn", my_fn)
    runner = FunctionRunner()
    result = runner.run("my_fn", _make_ctx())
    assert result.success
    assert result.data == {"ok": True}


def test_run_unknown_function():
    """Unregistered function returns error FunctionResult."""
    clear_registry()
    runner = FunctionRunner()
    result = runner.run("nonexistent", _make_ctx())
    assert not result.success
    assert "Unknown function" in (result.error or "")


def test_run_with_retry():
    """First attempt fails, second succeeds."""
    call_count = 0

    def flaky_fn(ctx: ActionContext) -> FunctionResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient")
        return FunctionResult(success=True, data={"attempt": call_count})

    _setup_function("flaky_fn", flaky_fn)
    runner = FunctionRunner()
    runner.set_policy("flaky_fn", ExecutionPolicy(max_retries=2, retry_delay=0.01))
    result = runner.run("flaky_fn", _make_ctx())
    assert result.success
    assert result.data == {"attempt": 2}


def test_run_circuit_breaker_open():
    """When breaker is open (not half_open), returns error without calling function."""
    called = False

    def my_fn(ctx: ActionContext) -> FunctionResult:
        nonlocal called
        called = True
        return FunctionResult(success=True)

    _setup_function("my_fn", my_fn)
    runner = FunctionRunner()
    breaker = CircuitBreaker(failure_threshold=1)
    # Force breaker open
    breaker.record_failure()
    assert breaker.is_open
    runner._breakers["my_fn"] = breaker

    result = runner.run("my_fn", _make_ctx())
    assert not result.success
    assert "Circuit breaker open" in (result.error or "")
    assert not called


def test_run_fallback_when_breaker_open():
    """When breaker is open and fallback is set, fallback is called."""

    def my_fn(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True)

    def fallback_fn(ctx: ActionContext, **kwargs) -> FunctionResult:
        return FunctionResult(success=True, data={"fallback": True})

    _setup_function("my_fn", my_fn)
    runner = FunctionRunner()
    runner.set_policy("my_fn", ExecutionPolicy(fallback=fallback_fn))
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    runner._breakers["my_fn"] = breaker

    result = runner.run("my_fn", _make_ctx())
    assert result.success
    assert result.data == {"fallback": True}


def test_run_half_open_probe_no_fallback():
    """HALF_OPEN probe passes through to the real function, not fallback."""

    def my_fn(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True, data={"real": True})

    def fallback_fn(ctx: ActionContext, **kwargs) -> FunctionResult:
        return FunctionResult(success=True, data={"fallback": True})

    _setup_function("my_fn", my_fn)
    runner = FunctionRunner()
    runner.set_policy("my_fn", ExecutionPolicy(fallback=fallback_fn))
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    breaker.record_failure()
    assert breaker.is_open
    # Manually set to half_open for test
    breaker._state = "half_open"

    result = runner.run("my_fn", _make_ctx())
    assert result.success
    assert result.data == {"real": True}  # Not fallback


def test_run_all_retries_exhausted():
    """All retries fail → breaker records failure, returns error."""
    call_count = 0

    def bad_fn(ctx: ActionContext) -> FunctionResult:
        nonlocal call_count
        call_count += 1
        raise RuntimeError(f"fail {call_count}")

    _setup_function("bad_fn", bad_fn)
    runner = FunctionRunner()
    runner.set_policy("bad_fn", ExecutionPolicy(max_retries=2, retry_delay=0.01))
    result = runner.run("bad_fn", _make_ctx())
    assert not result.success
    assert "fail 3" in (result.error or "")
    assert call_count == 3  # 1 initial + 2 retries


def test_run_batch():
    """run_batch executes all functions sequentially, collects results."""

    def fn_a(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True, data={"name": "a"})

    def fn_b(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True, data={"name": "b"})

    clear_registry()
    register_function("fn_a")(fn_a)
    register_function("fn_b")(fn_b)

    runner = FunctionRunner()
    results = runner.run_batch(["fn_a", "fn_b"], _make_ctx())
    assert len(results) == 2
    assert results[0].data == {"name": "a"}
    assert results[1].data == {"name": "b"}
