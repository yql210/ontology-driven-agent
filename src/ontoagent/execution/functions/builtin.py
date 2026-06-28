"""Builtin functions — native ActionContext-based implementations."""

from __future__ import annotations

from ontoagent.execution.action_types import ActionContext, FunctionResult
from ontoagent.execution.functions.registry import get_function, register_function

# ---------------------------------------------------------------------------
# check_refactor_eligibility  (was split_large_function)
# ---------------------------------------------------------------------------


def _check_refactor_eligibility(ctx: ActionContext) -> FunctionResult:
    """Check if a function exceeds complexity thresholds and suggest splits."""
    entity_id = ctx.match_data.get("entity_id", "")
    graph_store = ctx.graph_store

    node = graph_store.get_node(entity_id)
    if node is None:
        return FunctionResult(success=False, error=f"Entity not found: {entity_id}")

    total_lines = ctx.match_data.get("lines", node.get("lines", 0))
    branch_count = ctx.match_data.get("branches", node.get("branches", 0))
    max_lines = ctx.match_data.get("max_lines", 100)

    if not isinstance(total_lines, int) or total_lines <= 0:
        total_lines = int(node.get("lines", 0))

    if not isinstance(branch_count, int) or branch_count < 0:
        branch_count = int(node.get("branches", 0))

    if total_lines <= max_lines:
        return FunctionResult(
            success=False,
            error=f"Function has {total_lines} lines, does not exceed max_lines={max_lines}. No split needed.",
        )

    complexity = min(1.0, (total_lines / max_lines) * 0.5 + (branch_count / 20) * 0.5)
    suggested_splits = _generate_split_suggestions(total_lines, branch_count)

    return FunctionResult(
        success=True,
        data={
            "total_lines": total_lines,
            "branch_count": branch_count,
            "complexity": round(complexity, 2),
            "suggested_splits": suggested_splits,
        },
    )


def _generate_split_suggestions(total_lines: int, branch_count: int) -> list[dict]:
    suggestions: list[dict] = []

    if branch_count > 10:
        validate_lines = min(30, total_lines // 3)
        suggestions.append({"name": "_validate_inputs", "lines": validate_lines, "reason": "输入验证逻辑可独立"})

    if total_lines > 100:
        core_lines = min(40, total_lines // 3)
        suggestions.append({"name": "_process_core", "lines": core_lines, "reason": "核心处理逻辑可独立"})

    if total_lines > 200:
        error_lines = min(25, total_lines // 5)
        suggestions.append({"name": "_handle_errors", "lines": error_lines, "reason": "错误处理逻辑可独立"})

    return suggestions


# ---------------------------------------------------------------------------
# trace_call_chain
# ---------------------------------------------------------------------------


def _trace_call_chain(ctx: ActionContext) -> FunctionResult:
    """Trace CALLS relationships from a given entity."""
    entity_id = ctx.match_data.get("entity_id", "")
    graph_store = ctx.graph_store

    node = graph_store.get_node(entity_id)
    if node is None:
        return FunctionResult(success=False, error=f"Entity not found: {entity_id}")

    depth = ctx.match_data.get("depth", 3)
    if not isinstance(depth, int) or depth < 1:
        depth = 3

    cypher = (
        f"MATCH (caller:CodeEntity)-[:CALLS*1..{depth}]->(callee:CodeEntity) "
        "WHERE caller.id = $entity_id "
        "RETURN callee.id AS id, callee.name AS name, callee.entityType AS entity_type"
    )
    results = graph_store.query(cypher, {"entity_id": entity_id})

    call_tree: list[dict] = [
        {"id": row.get("id", ""), "name": row.get("name", ""), "entity_type": row.get("entity_type", "")}
        for row in results
    ]

    return FunctionResult(success=True, data={"depth": depth, "call_tree": call_tree})


# ---------------------------------------------------------------------------
# generate_api_doc
# ---------------------------------------------------------------------------


def _generate_api_doc(ctx: ActionContext) -> FunctionResult:
    """Generate Markdown API documentation for an entity."""
    entity_id = ctx.match_data.get("entity_id", "")
    graph_store = ctx.graph_store

    node = graph_store.get_node(entity_id)
    if node is None:
        return FunctionResult(success=False, error=f"Entity not found: {entity_id}")

    name = node.get("name", entity_id)
    entity_type = node.get("entityType", "unknown")
    params = node.get("params", [])
    return_type = node.get("return_type", "Any")
    docstring = node.get("docstring", "")

    lines: list[str] = [f"## `{name}`", ""]
    lines.append(f"**类型**: `{entity_type}`")
    lines.append("")

    if params:
        lines.append("**参数**:")
        lines.append("")
        for p in params:
            lines.append(f"- `{p}`")
        lines.append("")

    if return_type:
        lines.append(f"**返回值**: `{return_type}`")
        lines.append("")

    if docstring:
        lines.append(f"**描述**: {docstring}")
        lines.append("")

    if not params and "params" in ctx.match_data:
        ctx_params = ctx.match_data["params"]
        if isinstance(ctx_params, list) and ctx_params:
            lines.append("**参数**:")
            lines.append("")
            for p in ctx_params:
                lines.append(f"- `{p}`")
            lines.append("")

    doc_markdown = "\n".join(lines)

    return FunctionResult(
        success=True,
        data={
            "entity_name": name,
            "entity_type": entity_type,
            "doc_markdown": doc_markdown,
        },
    )


# ---------------------------------------------------------------------------
# extract_interface
# ---------------------------------------------------------------------------


def _extract_interface(ctx: ActionContext) -> FunctionResult:
    """Extract a public interface suggestion from a class."""
    entity_id = ctx.match_data.get("entity_id", "")
    graph_store = ctx.graph_store

    node = graph_store.get_node(entity_id)
    if node is None:
        return FunctionResult(success=False, error=f"Entity not found: {entity_id}")

    class_name = node.get("name", entity_id)
    raw_methods = ctx.match_data.get("class_methods", [])

    public_methods = sorted(m for m in raw_methods if not m.startswith("_"))
    interface_name = f"I{class_name}"

    suggested_interface = {
        "name": interface_name,
        "methods": [{"name": method, "signature": f"def {method}(self, *args, **kwargs)"} for method in public_methods],
    }

    return FunctionResult(
        success=True,
        data={
            "class_name": class_name,
            "public_methods": public_methods,
            "interface_name": interface_name,
            "suggested_interface": suggested_interface,
        },
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all() -> None:
    """Register all builtin functions (idempotent)."""
    if get_function("check_refactor_eligibility") is None:
        register_function("check_refactor_eligibility", danger_level="read")(_check_refactor_eligibility)
    if get_function("trace_call_chain") is None:
        register_function("trace_call_chain", danger_level="read")(_trace_call_chain)
    if get_function("generate_api_doc") is None:
        register_function("generate_api_doc", danger_level="write")(_generate_api_doc)
    if get_function("extract_interface") is None:
        register_function("extract_interface", danger_level="write")(_extract_interface)


register_all()
