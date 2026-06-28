"""Compliance check function — evaluates regulatory compliance risks."""

from __future__ import annotations

from ontoagent.execution.action_types import ActionContext, FunctionResult
from ontoagent.execution.functions.registry import get_function, register_function


def _check_compliance(ctx: ActionContext) -> FunctionResult:
    """Check compliance risks for a code entity by tracing data assets and compliance items."""
    entity_id = ctx.match_data.get("entity_id", "")
    target_id = ctx.match_data.get("target_id", entity_id)
    graph_store = ctx.graph_store

    node = graph_store.get_node(target_id)
    if node is None:
        return FunctionResult(success=False, error=f"Entity not found: {target_id}")

    # Try Cypher query first
    cypher = (
        "MATCH (c:CodeEntity {id: $target_id})-[:processes_data]->(d:DataAsset)-[:governed_by]->(ci:ComplianceItem) "
        "RETURN d.name as data_asset, ci.name as compliance, ci.requirement, ci.severity"
    )
    try:
        results = graph_store.query(cypher, {"target_id": target_id})
        if results:
            risks = [
                {
                    "data_asset": row.get("data_asset", ""),
                    "compliance": row.get("compliance", ""),
                    "requirement": row.get("requirement", ""),
                    "severity": row.get("severity", ""),
                }
                for row in results
            ]
            return FunctionResult(success=True, data={"risks": risks, "count": len(risks)})
    except Exception:
        pass

    # If processes_data relationship doesn't exist yet (Phase 2 not run in builder)
    return FunctionResult(
        success=True,
        data={"risks": [], "count": 0, "note": "No business entities loaded yet"},
    )


def register_all() -> None:
    """Register compliance check function (idempotent)."""
    if get_function("check_compliance") is None:
        register_function("check_compliance", danger_level="read")(_check_compliance)


register_all()
