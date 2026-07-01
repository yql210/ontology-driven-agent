"""RED phase — Planner tests.

Planner decomposes a business goal into sub-goals, then matches
each sub-goal to capabilities via CapabilityFinder.
"""

from __future__ import annotations

from unittest.mock import MagicMock


class TestPlanner:
    """Planner — goal decomposition + capability matching."""

    def test_decompose_returns_sub_goals(self):
        """decompose(goal) returns a list of SubGoal objects."""
        from ontoagent.execution.planner.planner import Planner, SubGoal

        # Mock CapabilityFinder — not needed for decompose
        mock_finder = MagicMock()
        planner = Planner(finder=mock_finder)

        sub_goals = planner.decompose("完成订单履约")

        assert len(sub_goals) >= 2, f"Expected ≥2 sub-goals, got {len(sub_goals)}"
        assert all(isinstance(sg, SubGoal) for sg in sub_goals)
        for sg in sub_goals:
            assert sg.description, "SubGoal description must not be empty"

    def test_decompose_returns_domain_specific_goals(self):
        """Each sub-goal targets a specific business domain."""
        from ontoagent.execution.planner.planner import Planner

        mock_finder = MagicMock()
        planner = Planner(finder=mock_finder)

        sub_goals = planner.decompose("处理用户下单")

        # Should span multiple domains: order, payment, inventory
        domains = {sg.domain for sg in sub_goals if sg.domain}
        assert len(domains) >= 2, f"Expected ≥2 domains, got {domains}"

    def test_plan_matches_capabilities_to_sub_goals(self):
        """plan(goal) finds capabilities for each sub-goal."""
        from ontoagent.execution.planner.planner import Planner

        mock_finder = MagicMock()
        mock_finder.find.return_value = [
            MagicMock(id="cap-1", name="process_payment", domain="payment", description="支付处理", distance=0.1),
        ]
        planner = Planner(finder=mock_finder)

        plan = planner.plan("处理用户支付")

        assert plan.goal == "处理用户支付"
        assert len(plan.nodes) >= 1
        # At least one node should have a capability assigned
        matched = [n for n in plan.nodes if n.capability_id]
        assert len(matched) >= 1, "Expected at least one matched capability"

    def test_plan_uses_top_k_for_fallback(self):
        """When top-1 fails, plan falls back to broader search (top_k=10)."""
        from ontoagent.execution.planner.planner import Planner

        mock_finder = MagicMock()
        # First call returns empty, second returns a match
        mock_finder.find.side_effect = [
            [],  # top_k=1 fails
            [MagicMock(id="cap-1", name="check", domain="inv", description="check", distance=0.1)],  # top_k=10 succeeds
        ]

        planner = Planner(finder=mock_finder, default_top_k=1, fallback_top_k=10)
        plan = planner.plan("查询库存")

        assert mock_finder.find.call_count == 2
        assert plan.nodes[0].capability_id == "cap-1"

    def test_sub_goal_has_required_fields(self):
        """SubGoal exposes description, domain, and optional constraint fields."""
        from ontoagent.execution.planner.planner import SubGoal

        sg = SubGoal(
            description="验证用户支付方式",
            domain="payment",
            constraints=["支持信用卡和借记卡"],
        )

        assert sg.description == "验证用户支付方式"
        assert sg.domain == "payment"
        assert sg.constraints == ["支持信用卡和借记卡"]

    def test_plan_node_has_dependencies(self):
        """PlanNode tracks which other nodes it depends on for dataflow."""
        from ontoagent.execution.planner.planner import PlanNode, SubGoal

        sg = SubGoal(description="发送订单确认邮件", domain="notification")
        node = PlanNode(
            sub_goal=sg,
            capability_id="cap-ntf-1",
            dependencies=["node-order-created"],
        )

        assert node.capability_id == "cap-ntf-1"
        assert "node-order-created" in node.dependencies

    def test_plan_dag_exposes_nodes_and_edges(self):
        """PlanDAG provides nodes list and edges list for orchestration."""
        from ontoagent.execution.planner.planner import PlanDAG, PlanNode, SubGoal

        node_a = PlanNode(
            sub_goal=SubGoal(description="创建订单", domain="order"),
            capability_id="cap-ord-1",
        )
        node_b = PlanNode(
            sub_goal=SubGoal(description="处理支付", domain="payment"),
            capability_id="cap-pay-1",
        )

        dag = PlanDAG(
            goal="完成订单履约",
            nodes=[node_a, node_b],
            edges=[(node_a.id, node_b.id)],
        )

        assert dag.goal == "完成订单履约"
        assert len(dag.nodes) == 2
        assert len(dag.edges) == 1
        assert dag.edges[0] == (node_a.id, node_b.id)
