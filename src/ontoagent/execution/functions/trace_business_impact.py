"""Business impact tracing — reverse BFS along CALLS relationships to find entry points.

后端兼容：自动检测 GraphStore 类型，Neo4j 用 ``length(path)``，NebulaGraph 用 ``length(path)``（nGQL 原生支持）。
"""

from __future__ import annotations

from ontoagent.execution.action_types import ActionContext, FunctionResult
from ontoagent.execution.functions.registry import get_function, register_function


def _is_nebula(store: object) -> bool:
    """检测 store 是否为 NebulaGraphStore。"""
    return type(store).__name__ == "NebulaGraphStore"


def _trace_business_impact(ctx: ActionContext) -> FunctionResult:
    """Trace business impact by reverse BFS along CALLS to find entry points with entry_category."""
    entity_id = ctx.match_data.get("entity_id", "")
    target_id = ctx.match_data.get("target_id", entity_id)
    graph_store = ctx.graph_store

    node = graph_store.get_node(target_id)
    if node is None:
        return FunctionResult(success=False, error=f"Entity not found: {target_id}")

    # Reverse BFS along CALLS relationships to find nodes with entry_category
    # NebulaGraph 也支持 MATCH path = (a)-[:EDGE*1..N]->(b)，且 length(path) 原生可用
    if _is_nebula(graph_store):
        # NebulaGraph: id() 而非 {id: $param}；属性需 Tag 前缀访问
        ngql = (
            f'MATCH (start:`CodeEntity`) WHERE id(start) == "{target_id}" '
            "MATCH path = (entry:`CodeEntity`)-[:`CALLS`*1..10]->(start) "
            "WHERE entry.CodeEntity.entry_category IS NOT NULL "
            "RETURN DISTINCT entry.id AS id, entry.name AS name, "
            "entry.CodeEntity.entry_category AS entry_category, "
            "entry.CodeEntity.business_priority AS business_priority, "
            "entry.CodeEntity.business_owner AS business_owner, "
            "length(path) AS distance "
            "ORDER BY distance"
        )
        params = None
    else:
        ngql = (
            "MATCH (start:CodeEntity {id: $target_id}) "
            "MATCH path = (entry:CodeEntity)-[:CALLS*1..10]->(start) "
            "WHERE entry.entry_category IS NOT NULL "
            "RETURN DISTINCT entry.id AS id, entry.name AS name, "
            "entry.entry_category AS entry_category, "
            "entry.business_priority AS business_priority, "
            "entry.business_owner AS business_owner, "
            "length(path) AS distance "
            "ORDER BY distance"
        )
        params = {"target_id": target_id}

    try:
        results = graph_store.query(ngql, params)
        if results:
            entry_points = [
                {
                    "name": row.get("name", ""),
                    "entry_category": row.get("entry_category", ""),
                    "business_priority": row.get("business_priority", ""),
                    "business_owner": row.get("business_owner", ""),
                    "distance": row.get("distance", 0),
                }
                for row in results
            ]
            return FunctionResult(
                success=True,
                data={"entry_points": entry_points, "count": len(entry_points)},
            )
    except Exception:
        pass

    # No entry_category nodes found in call chain (Phase 1 not executed)
    return FunctionResult(
        success=True,
        data={"entry_points": [], "count": 0, "note": "No business entities loaded yet"},
    )


def register_all() -> None:
    """Register business impact tracing function (idempotent)."""
    if get_function("trace_business_impact") is None:
        register_function("trace_business_impact", danger_level="read_sensitive")(_trace_business_impact)


register_all()
