"""RED phase — DAGOrchestrator tests.

Phase 4: DAG-based orchestration engine replacing linear saga.py.
Tests: topological sort, parallel execution, data flow, compensation.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestDAGTopologicalSort:
    """Topological sort produces valid execution order from a DAG."""

    def test_linear_chain(self):
        """A → B → C → order: [A, B, C]."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        orch = DAGOrchestrator()
        order = orch._topological_sort(
            nodes=["A", "B", "C"],
            edges=[("A", "B"), ("B", "C")],
        )
        assert order == ["A", "B", "C"]

    def test_parallel_branches(self):
        """A → B, A → C → both B and C at same level after A."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        orch = DAGOrchestrator()
        order = orch._topological_sort(
            nodes=["A", "B", "C"],
            edges=[("A", "B"), ("A", "C")],
        )
        assert order[0] == "A"
        assert set(order[1:]) == {"B", "C"}  # B and C at same level

    def test_diamond_dag(self):
        """A → B → D, A → C → D."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        orch = DAGOrchestrator()
        order = orch._topological_sort(
            nodes=["A", "B", "C", "D"],
            edges=[("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")],
        )
        assert order[0] == "A"
        assert order[-1] == "D"
        # B and C must both be between A and D
        a_idx = order.index("A")
        d_idx = order.index("D")
        b_idx = order.index("B")
        c_idx = order.index("C")
        assert a_idx < b_idx < d_idx
        assert a_idx < c_idx < d_idx

    def test_single_node(self):
        """Single node → [A]."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        orch = DAGOrchestrator()
        order = orch._topological_sort(nodes=["A"], edges=[])
        assert order == ["A"]

    def test_cycle_detection(self):
        """A → B → C → A raises CycleError."""
        from ontoagent.execution.dag_orchestrator import CycleError, DAGOrchestrator

        orch = DAGOrchestrator()
        with pytest.raises(CycleError):
            orch._topological_sort(
                nodes=["A", "B", "C"],
                edges=[("A", "B"), ("B", "C"), ("C", "A")],
            )


class TestDAGExecution:
    """DAG execution runs nodes in topological order, respecting parallelism."""

    def test_execute_linear_dag(self):
        """Execute A → B → C, all succeed."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        mock_fn = MagicMock(return_value={"status": "ok"})
        orch = DAGOrchestrator()

        result = orch.execute(
            nodes=[
                {"id": "A", "capability": mock_fn},
                {"id": "B", "capability": mock_fn},
                {"id": "C", "capability": mock_fn},
            ],
            edges=[("A", "B"), ("B", "C")],
        )

        assert result.status == "completed"
        assert mock_fn.call_count == 3
        assert len(result.node_results) == 3
        for nr in result.node_results:
            assert nr.status == "ok"

    def test_execute_respects_order(self):
        """B depends on A, so A must execute before B."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        execution_order: list[str] = []

        def make_fn(name: str):
            def fn(_payload=None):
                execution_order.append(name)
                return {"name": name}
            return fn

        orch = DAGOrchestrator()
        orch.execute(
            nodes=[
                {"id": "A", "capability": make_fn("A")},
                {"id": "B", "capability": make_fn("B")},
            ],
            edges=[("A", "B")],
        )

        assert execution_order[0] == "A"
        assert execution_order[1] == "B"

    def test_execute_empty_dag(self):
        """Empty DAG returns empty result."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        orch = DAGOrchestrator()
        result = orch.execute(nodes=[], edges=[])

        assert result.status == "completed"
        assert len(result.node_results) == 0


class TestDataFlow:
    """Data flows from upstream nodes to downstream via PRODUCES/CONSUMES."""

    def test_data_passed_downstream(self):
        """A produces X, B consumes X → B receives A's output."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        received_payload = None

        def producer(_payload=None):
            return {"order_id": 12345}

        def consumer(payload=None):
            nonlocal received_payload
            received_payload = payload
            return {"ok": True}

        orch = DAGOrchestrator(
            relations=[
                ("A", "produces", "OrderCreated"),
                ("B", "consumes", "OrderCreated"),
            ]
        )
        orch.execute(
            nodes=[
                {"id": "A", "capability": producer, "produces": ["OrderCreated"]},
                {"id": "B", "capability": consumer, "consumes": ["OrderCreated"]},
            ],
            edges=[("A", "B")],
        )

        assert received_payload is not None
        assert received_payload.get("order_id") == 12345

    def test_no_data_flow_without_relation(self):
        """Without PRODUCES/CONSUMES relation, no data passed."""
        from ontoagent.execution.dag_orchestrator import DAGOrchestrator

        received_payload = "unchanged"

        def fn_a(_payload=None):
            return {"data": "hello"}

        def fn_b(payload=None):
            nonlocal received_payload
            received_payload = payload
            return {"ok": True}

        orch = DAGOrchestrator()
        orch.execute(
            nodes=[
                {"id": "A", "capability": fn_a},
                {"id": "B", "capability": fn_b},
            ],
            edges=[("A", "B")],
        )

        # Without explicit produces/consumes annotation, no data passed
        assert received_payload is None or received_payload == {}
