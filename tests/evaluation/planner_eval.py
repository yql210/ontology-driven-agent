"""Phase 3.4 — Planner evaluation set.

≥5 business goals with expected DAG structure.
Verifies that Planner + Composer produce correct execution plans.
"""

from __future__ import annotations

from typing import NamedTuple


class PlannerEvalCase(NamedTuple):
    """A single planner evaluation case.

    Attributes:
        goal: Business goal text.
        expected_domains: Expected business domains in the plan.
        min_nodes: Minimum number of plan nodes expected.
        has_edges: Whether edges between nodes are expected.
    """

    goal: str
    expected_domains: set[str]
    min_nodes: int
    has_edges: bool


# 7 business goals — more than the required 5
EVAL_CASES: list[PlannerEvalCase] = [
    PlannerEvalCase(
        goal="完成订单履约",
        expected_domains={"order", "payment", "logistics", "notification"},
        min_nodes=5,
        has_edges=True,
    ),
    PlannerEvalCase(
        goal="处理用户退款",
        expected_domains={"order", "payment", "inventory", "notification"},
        min_nodes=4,
        has_edges=True,
    ),
    PlannerEvalCase(
        goal="用户注册账号",
        expected_domains={"user"},
        min_nodes=3,
        has_edges=True,
    ),
    PlannerEvalCase(
        goal="生成销售报表",
        expected_domains={"analytics", "content"},
        min_nodes=2,
        has_edges=True,
    ),
    PlannerEvalCase(
        goal="搜索推荐商品",
        expected_domains={"product"},
        min_nodes=2,
        has_edges=True,
    ),
    PlannerEvalCase(
        goal="处理信用卡付款",
        expected_domains={"payment", "analytics"},
        min_nodes=2,
        has_edges=True,
    ),
    PlannerEvalCase(
        goal="审批发布内容",
        expected_domains={"content"},
        min_nodes=2,
        has_edges=True,
    ),
]

assert len(EVAL_CASES) >= 5, f"Expected ≥5 eval cases, got {len(EVAL_CASES)}"
