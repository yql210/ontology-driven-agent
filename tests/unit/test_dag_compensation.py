"""RED phase — DAGOrchestrator compensation tests.

Phase 4.3: On node failure, reverse-compensate completed predecessors.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestCompensation:
    """Compensation: on failure, reverse-compensate completed nodes."""

    def test_single_node_failure_no_compensation(self):
        """Single node fails → no compensation needed (nothing to rollback)."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        def fail_fn(_payload=None):
            raise RuntimeError("node failed")

        orch = DAGOrchestrator()
        result = orch.execute(
            nodes=[{"id": "A", "capability": fail_fn}],
            edges=[],
        )

        assert result.status == "failed"
        assert result.failed_node_id == "A"

    def test_linear_compensation_on_failure(self):
        """A (ok) → B (fails) → compensate A."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        compensate_called: list[str] = []

        def make_fn(name: str, *, should_fail: bool = False):
            def fn(_payload=None):
                if should_fail:
                    raise RuntimeError(f"{name} failed")
                return {"name": name}

            def compensate(output=None):
                compensate_called.append(name)

            fn.compensate = compensate  # type: ignore[attr-defined]
            return fn

        orch = DAGOrchestrator()
        result = orch.execute(
            nodes=[
                {"id": "A", "capability": make_fn("A")},
                {"id": "B", "capability": make_fn("B", should_fail=True)},
            ],
            edges=[("A", "B")],
        )

        assert result.status == "failed"
        assert result.failed_node_id == "B"
        assert "A" in compensate_called, f"Expected A to be compensated, got {compensate_called}"

    def test_partial_dag_compensation(self):
        """A → B (fails) → compensate A; C (parallel) runs uncompensated."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        compensate_called: list[str] = []

        def make_fn(name: str, *, should_fail: bool = False):
            def fn(_payload=None):
                import time
                time.sleep(0.01)  # tiny delay for deterministic ordering
                if should_fail:
                    raise RuntimeError(f"{name} failed")
                return {"name": name}

            def compensate(output=None):
                compensate_called.append(name)

            fn.compensate = compensate  # type: ignore[attr-defined]
            return fn

        orch = DAGOrchestrator()
        result = orch.execute(
            nodes=[
                {"id": "A", "capability": make_fn("A")},
                {"id": "B", "capability": make_fn("B", should_fail=True)},
                {"id": "C", "capability": make_fn("C")},
            ],
            edges=[("A", "B")],  # C is independent (parallel with A→B chain)
        )

        assert result.status == "failed"
        assert "A" in compensate_called, f"A should be compensated, got {compensate_called}"

    def test_compensation_reverse_order(self):
        """A → B → C (fails) → compensate B then A (reverse order)."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        compensate_order: list[str] = []

        def make_fn(name: str, *, should_fail: bool = False):
            def fn(_payload=None):
                if should_fail:
                    raise RuntimeError(f"{name} failed")
                return {"name": name}

            def compensate(output=None):
                compensate_order.append(name)

            fn.compensate = compensate  # type: ignore[attr-defined]
            return fn

        orch = DAGOrchestrator()
        orch.execute(
            nodes=[
                {"id": "A", "capability": make_fn("A")},
                {"id": "B", "capability": make_fn("B")},
                {"id": "C", "capability": make_fn("C", should_fail=True)},
            ],
            edges=[("A", "B"), ("B", "C")],
        )

        # Compensation must be reverse order: B then A (closest to failure first)
        assert compensate_order == ["B", "A"], f"Expected ['B', 'A'], got {compensate_order}"

    def test_node_without_compensate_skipped(self):
        """Node without .compensate attr is silently skipped during rollback."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        def ok_fn(_payload=None):
            return {"ok": True}

        def fail_fn(_payload=None):
            raise RuntimeError("fail")

        orch = DAGOrchestrator()
        result = orch.execute(
            nodes=[
                {"id": "A", "capability": ok_fn},  # no compensate method
                {"id": "B", "capability": fail_fn},
            ],
            edges=[("A", "B")],
        )

        assert result.status == "failed"
        # A should be compensated, but having no compensate method → silently skipped
        assert result.failed_node_id == "B"


class TestExecutionResult:
    """ExecutionResult exposes node-level and DAG-level status."""

    def test_result_summary(self):
        """ExecutionResult has status, node_results, elapsed_ms."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator, ExecutionResult

        orch = DAGOrchestrator()
        result = orch.execute(
            nodes=[{"id": "A", "capability": lambda _p=None: {"ok": True}}],
            edges=[],
        )

        assert result.status == "completed"
        assert result.elapsed_ms >= 0
        assert result.failed_node_id is None

    def test_result_node_detail(self):
        """Each NodeResult has node_id, status, output, error."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        def ok_fn(_payload=None):
            return {"result": 42}

        orch = DAGOrchestrator()
        result = orch.execute(
            nodes=[{"id": "A", "capability": ok_fn}],
            edges=[],
        )

        nr = result.node_results[0]
        assert nr.node_id == "A"
        assert nr.status == "ok"
        assert nr.output == {"result": 42}
        assert nr.error is None

    def test_result_on_failure(self):
        """Failed node has status=failed with error message."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        def fail_fn(_payload=None):
            raise ValueError("bad input")

        orch = DAGOrchestrator()
        result = orch.execute(
            nodes=[{"id": "A", "capability": fail_fn}],
            edges=[],
        )

        nr = result.node_results[0]
        assert nr.status == "failed"
        assert "bad input" in (nr.error or "")
