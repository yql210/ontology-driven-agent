"""Planner data types — SubGoal, PlanNode, PlanDAG."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class SubGoal:
    """A single sub-goal decomposed from a business goal.

    Attributes:
        description: Natural language description of the sub-goal.
        domain: Business domain this sub-goal belongs to.
        constraints: Additional constraints or requirements.
    """

    description: str
    domain: str | None = None
    constraints: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.description or not self.description.strip():
            raise ValueError("SubGoal.description cannot be empty")


@dataclass
class PlanNode:
    """A node in the plan DAG — one capability assigned to one sub-goal.

    Attributes:
        sub_goal: The sub-goal this node fulfills.
        capability_id: Matched capability entity ID (None if unresolved).
        dependencies: IDs of PlanNodes this node depends on (dataflow).
        id: Unique node identifier.
    """

    sub_goal: SubGoal
    capability_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class PlanDAG:
    """A directed acyclic graph representing the execution plan.

    Attributes:
        goal: The original business goal.
        nodes: All plan nodes.
        edges: (from_node_id, to_node_id) pairs representing execution order.
        unresolved_dependencies: Nodes with missing capability matches.
    """

    goal: str
    nodes: list[PlanNode] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)

    @property
    def unresolved_dependencies(self) -> dict[str, list[str]]:
        """Return nodes that have unresolved issues (missing caps or deps)."""
        unres: dict[str, list[str]] = {}
        for node in self.nodes:
            reasons: list[str] = []
            if node.capability_id is None:
                reasons.append("no_capability_match")
            # Check for unresolved consume messages in dependencies
            for dep in node.dependencies:
                if "consumes" in dep and "PRODUCES" in dep:
                    reasons.append(dep)
            # Check for missing node dependencies
            missing = [d for d in node.dependencies if d not in reasons and not any(n.id == d for n in self.nodes)]
            if missing:
                reasons.append(f"missing_dependencies: {missing}")
            if reasons:
                # Use capability_id as key if available for readable output
                key = node.capability_id or node.id
                unres[key] = reasons
        return unres

    @property
    def is_complete(self) -> bool:
        """True if all nodes have resolved capabilities."""
        return len(self.unresolved_dependencies) == 0
