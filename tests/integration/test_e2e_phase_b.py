"""E2E integration tests for Phase B: FunctionRunner integration."""

from __future__ import annotations

from typing import Any

import pytest

from ontoagent.execution.action_types import ActionContext, FunctionResult
from ontoagent.execution.execution_policy import ExecutionPolicy
from ontoagent.execution.function_runner import FunctionRunner
from ontoagent.execution.functions.registry import clear_registry, register_function


class _StubGraphStore:
    """Minimal graph store stub for integration tests."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []

    def merge_node(self, label: str, node_id: str, properties: dict[str, Any]) -> None:
        self.nodes[node_id] = {"label": label, **properties}

    def merge_relation(self, from_id: str, rel_type: str, to_id: str, properties: dict[str, Any] | None = None) -> None:
        pass


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
