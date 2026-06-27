"""E2E integration tests for Phase B: FunctionRunner + SAGA + ActionExecutor integration."""

from __future__ import annotations

from typing import Any

import pytest

from layerkg.execution.action_types import ActionContext, FunctionResult
from layerkg.execution.execution_policy import ExecutionPolicy
from layerkg.execution.function_runner import FunctionRunner
from layerkg.execution.functions.registry import clear_registry, register_function
from layerkg.execution.saga import SagaOrchestrator, SagaStep


class _StubGraphStore:
    """Minimal graph store stub for integration tests."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    def merge_node(self, label: str, node_id: str, properties: dict[str, Any]) -> None:
        self.nodes[node_id] = {"label": label, **properties}


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


def _make_ctx() -> ActionContext:
    return ActionContext(graph_store=_StubGraphStore(), match_data={})


# ---------------------------------------------------------------------------
# test_e2e_function_runner_retry_and_success
# ---------------------------------------------------------------------------


def test_e2e_function_runner_retry_and_success():
    """FunctionRunner retries on failure and eventually succeeds."""
    call_count = 0

    @register_function("flaky_fn")
    def _flaky(ctx: ActionContext) -> FunctionResult:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient error")
        return FunctionResult(success=True, data={"attempt": call_count})

    runner = FunctionRunner()
    runner.set_policy("flaky_fn", ExecutionPolicy(max_retries=3, retry_delay=0.01))

    ctx = _make_ctx()
    result = runner.run("flaky_fn", ctx)

    assert result.success is True
    assert result.data["attempt"] == 3
    assert call_count == 3


# ---------------------------------------------------------------------------
# test_e2e_saga_success_path
# ---------------------------------------------------------------------------


def test_e2e_saga_success_path():
    """SAGA orchestrator completes all steps successfully without compensation."""
    completed_steps: list[str] = []
    compensation_calls: list[str] = []

    def _make_step(name: str) -> tuple[SagaStep, str]:
        def action(ctx: ActionContext) -> FunctionResult:
            completed_steps.append(name)
            return FunctionResult(success=True, data={"step": name})

        def compensation(ctx: ActionContext) -> FunctionResult:
            compensation_calls.append(name)
            return FunctionResult(success=True)

        return SagaStep(name=name, action=action, compensation=compensation), name

    step_a, _ = _make_step("A")
    step_b, _ = _make_step("B")
    step_c, _ = _make_step("C")

    orchestrator = SagaOrchestrator()
    ctx = _make_ctx()
    result = orchestrator.execute([step_a, step_b, step_c], ctx)

    assert result.success is True
    assert completed_steps == ["A", "B", "C"]
    assert compensation_calls == []
    assert result.summary == "SAGA completed 3 steps"


# ---------------------------------------------------------------------------
# test_e2e_saga_failure_compensation
# ---------------------------------------------------------------------------


def test_e2e_saga_failure_compensation():
    """SAGA triggers compensations in reverse order when a step fails."""
    completed_steps: list[str] = []
    compensation_calls: list[str] = []

    def step_action(name: str):
        def action(ctx: ActionContext) -> FunctionResult:
            completed_steps.append(name)
            if name == "B":
                return FunctionResult(success=False, error="B failed")
            return FunctionResult(success=True, data={"step": name})

        return action

    def step_compensation(name: str):
        def compensation(ctx: ActionContext) -> FunctionResult:
            compensation_calls.append(name)
            return FunctionResult(success=True)

        return compensation

    steps = [
        SagaStep(name="A", action=step_action("A"), compensation=step_compensation("A")),
        SagaStep(name="B", action=step_action("B"), compensation=step_compensation("B")),
        SagaStep(name="C", action=step_action("C"), compensation=step_compensation("C")),
    ]

    orchestrator = SagaOrchestrator()
    ctx = _make_ctx()
    result = orchestrator.execute(steps, ctx)

    assert result.success is False
    assert "B" in result.error
    assert completed_steps == ["A", "B"]
    assert compensation_calls == ["A"]
