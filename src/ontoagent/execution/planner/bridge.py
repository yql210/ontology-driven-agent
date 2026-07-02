"""Bridge: PlanDAG → DAGOrchestrator node dicts.

V5 Phase 5: Converts Planner output to DAGOrchestrator-compatible format,
resolving entities from graph_store for each PlanNode.
"""

from __future__ import annotations

from typing import Any

from ontoagent.domain.shapes import Operation
from ontoagent.execution.planner.data_types import PlanDAG, PlanNode


def plan_to_orchestrator_nodes(
    dag: PlanDAG,
    graph_store: Any,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """Convert PlanDAG to DAGOrchestrator-compatible (nodes, edges).

    Resolves entities from graph_store for each PlanNode.
    Capabilities are stub callables that log and return success
    (actual function wiring is a future phase).

    Args:
        dag: PlanDAG from Planner.plan().
        graph_store: GraphStore for entity resolution.

    Returns:
        Tuple of (nodes, edges) for DAGOrchestrator.execute().
    """
    nodes: list[dict] = []
    for plan_node in dag.nodes:
        entity = _resolve_entity(graph_store, plan_node)
        operations = _infer_operations(plan_node)

        nodes.append({
            "id": plan_node.id,
            "capability": _make_stub_capability(plan_node),
            "sub_goal": plan_node.sub_goal.description,
            "domain": plan_node.sub_goal.domain,
            "entity": entity,
            "operations": operations,
        })

    return nodes, list(dag.edges)


def _resolve_entity(graph_store: Any, node: PlanNode) -> dict | None:
    """Resolve entity from graph_store for a PlanNode.

    Tries exact name match first, then CONTAINS fallback.
    Returns None if no entity found.
    """
    description = node.sub_goal.description

    # Try exact match
    try:
        results = graph_store.query(
            "MATCH (n) WHERE n.name = $name "
            "RETURN n.id AS id, n.name AS name, labels(n) AS labels LIMIT 1",
            {"name": description},
        )
        if results:
            return results[0]
    except Exception:
        pass

    # Try CONTAINS fallback
    try:
        results = graph_store.query(
            "MATCH (n) WHERE n.name CONTAINS $name "
            "RETURN n.id AS id, n.name AS name, labels(n) AS labels LIMIT 1",
            {"name": description},
        )
        if results:
            return results[0]
    except Exception:
        pass

    return None


def _infer_operations(node: PlanNode) -> list[Operation]:
    """Infer Operation types from PlanNode's capability/sub_goal.

    Default: [Operation.EXECUTE] for nodes with a matched capability,
    [Operation.READ] for unresolved nodes.
    """
    if node.capability_id:
        return [Operation.EXECUTE]
    return [Operation.READ]


def _make_stub_capability(node: PlanNode):
    """Create a stub callable for a PlanNode.

    Actual function wiring will be implemented in a future phase.
    """
    sub_goal = node.sub_goal.description
    domain = node.sub_goal.domain or "unknown"

    def stub(payload=None):
        return {
            "result": "ok",
            "sub_goal": sub_goal,
            "domain": domain,
            "capability_id": node.capability_id,
            "payload": payload,
        }

    return stub


__all__ = ["plan_to_orchestrator_nodes"]
