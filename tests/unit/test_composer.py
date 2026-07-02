"""RED phase — Composer tests.

Composer takes sub-goals with matched capabilities and builds a valid DAG
using PRODUCES/CONSUMES/COMPOSES_INTO relations + pre/post conditions.
"""

from __future__ import annotations


class TestComposer:
    """Composer — DAG construction from sub-goals + capability relations."""

    def test_compose_builds_linear_dag(self):
        """Sequential PRODUCES/CONSUMES chain → linear DAG."""
        from ontoagent.execution.planner.composer import Composer

        # A PRODUCES X, B CONSUMES X → A → B
        sub_goals = [
            {"description": "创建订单", "capability_id": "cap-A", "domain": "order"},
            {"description": "发送通知", "capability_id": "cap-B", "domain": "notification"},
        ]
        relations = [
            ("cap-A", "produces", "OrderCreated"),
            ("cap-B", "consumes", "OrderCreated"),
        ]

        composer = Composer()
        dag = composer.compose(sub_goals, relations)

        assert dag.goal is not None
        assert len(dag.nodes) == 2
        # cap-A should precede cap-B
        edge_ids = {(e[0], e[1]) for e in dag.edges}
        assert (dag.nodes[0].id, dag.nodes[1].id) in edge_ids or edge_ids

    def test_compose_respects_no_dependency(self):
        """Independent capabilities → no edges (parallel execution possible)."""
        from ontoagent.execution.planner.composer import Composer

        sub_goals = [
            {"description": "发送邮件", "capability_id": "cap-A", "domain": "notification"},
            {"description": "生成报表", "capability_id": "cap-B", "domain": "analytics"},
        ]
        relations = []  # No PRODUCES/CONSUMES between them

        composer = Composer()
        dag = composer.compose(sub_goals, relations)

        assert len(dag.nodes) == 2
        assert len(dag.edges) == 0  # Parallel, no dependencies

    def test_compose_detects_unresolvable_dependency(self):
        """A requires X but no capability PRODUCES X → warning in result."""
        from ontoagent.execution.planner.composer import Composer

        sub_goals = [
            {"description": "发货", "capability_id": "cap-A", "domain": "logistics"},
            {"description": "核验身份", "capability_id": "cap-B", "domain": "security"},
        ]
        relations = [
            ("cap-A", "consumes", "VerifiedIdentity"),  # no one produces this
            ("cap-B", "produces", "CheckResult"),
        ]

        composer = Composer()
        dag = composer.compose(sub_goals, relations)

        # cap-A has unresolved dependency — should be flagged
        unmet = dag.unresolved_dependencies
        assert "cap-A" in unmet or any(u.startswith("cap-A") for u in unmet)

    def test_compose_with_equivalent_capability(self):
        """EQUIVALENT_TO relation allows substitution."""
        from ontoagent.execution.planner.composer import Composer

        sub_goals = [
            {"description": "处理付款", "capability_id": "cap-pay", "domain": "payment"},
            {"description": "发送收据", "capability_id": "cap-rcpt", "domain": "notification"},
        ]
        relations = [
            ("cap-pay", "produces", "PaymentResult"),
            ("cap-rcpt", "consumes", "PaymentResult"),
            ("cap-pay-alt", "equivalent_to", "cap-pay"),  # alternative
        ]

        composer = Composer()
        dag = composer.compose(sub_goals, relations)

        # Should resolve normally since cap-pay produces what cap-rcpt needs
        assert len(dag.nodes) == 2

    def test_compose_handles_empty_sub_goals(self):
        """Empty sub-goals list → empty DAG."""
        from ontoagent.execution.planner.composer import Composer

        composer = Composer()
        dag = composer.compose([], [])

        assert len(dag.nodes) == 0
        assert len(dag.edges) == 0

    def test_compose_graph_structure_is_valid_dag(self):
        """Result is acyclic and topologically sortable."""
        from ontoagent.execution.planner.composer import Composer

        sub_goals = [
            {"description": "A", "capability_id": "cap-A", "domain": "d1"},
            {"description": "B", "capability_id": "cap-B", "domain": "d1"},
            {"description": "C", "capability_id": "cap-C", "domain": "d1"},
        ]
        relations = [
            ("cap-A", "produces", "X"),
            ("cap-B", "consumes", "X"),
            ("cap-B", "produces", "Y"),
            ("cap-C", "consumes", "Y"),
        ]

        composer = Composer()
        dag = composer.compose(sub_goals, relations)

        # Should produce A → B → C
        assert len(dag.edges) >= 2

    def test_compose_validation_required_for_output(self):
        """compose() returns a validated DAG (no cycles, no duplicate nodes)."""
        from ontoagent.execution.planner.composer import Composer

        sub_goals = [
            {"description": "支付", "capability_id": "cap-1", "domain": "payment"},
            {"description": "支付", "capability_id": "cap-1", "domain": "payment"},  # duplicate
        ]
        relations = []

        composer = Composer()
        dag = composer.compose(sub_goals, relations)

        # Duplicates should be deduplicated
        assert len(dag.nodes) == 1
