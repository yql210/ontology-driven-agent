"""CodeEntity 相关 Action Function 实现。

.. deprecated::
    本模块的 4 个 Function 已迁移至 ``layerkg.functions.builtin``（新 ActionContext 签名）。
    请使用新签名函数，本文件保留仅为兼容，将在后续版本移除。

Phase 1 只实现 split_large_function（只读分析），其余为空壳。
"""

from __future__ import annotations

from typing import Any


def split_large_function(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """拆分大函数 — 只读分析，返回拆分建议。

    输入：
        entity_id: 目标 CodeEntity 的 ID。
        context: {"lines": int, "branches": int, "max_lines": int (可选, 默认100)}。
        graph_store: 图存储实例（只读访问）。

    输出（dict）：
        {
            "success": True,
            "entity_id": "...",
            "analysis": {
                "total_lines": int,
                "branch_count": int,
                "complexity": float,
                "suggested_splits": [...],
            },
            "side_effects": [],
        }

    Raises:
        ValueError: entity_id 不存在。
        ValueError: 函数行数未超过阈值，不需要拆分。
    """
    # 读取节点信息
    node = graph_store.get_node(entity_id)
    if node is None:
        raise ValueError(f"Entity not found: {entity_id}")

    # 从 context 获取参数，降级从节点属性读取
    total_lines = context.get("lines", node.get("lines", 0))
    branch_count = context.get("branches", node.get("branches", 0))
    max_lines = context.get("max_lines", 100)

    if not isinstance(total_lines, int) or total_lines <= 0:
        total_lines = int(node.get("lines", 0))

    if not isinstance(branch_count, int) or branch_count < 0:
        branch_count = int(node.get("branches", 0))

    # 检查是否需要拆分
    if total_lines <= max_lines:
        raise ValueError(f"Function has {total_lines} lines, does not exceed max_lines={max_lines}. No split needed.")

    # 计算复杂度（0-1 范围）
    complexity = min(1.0, (total_lines / max_lines) * 0.5 + (branch_count / 20) * 0.5)

    # 生成拆分建议
    suggested_splits = _generate_split_suggestions(total_lines, branch_count)

    return {
        "success": True,
        "entity_id": entity_id,
        "analysis": {
            "total_lines": total_lines,
            "branch_count": branch_count,
            "complexity": round(complexity, 2),
            "suggested_splits": suggested_splits,
        },
        "side_effects": [],  # Phase 1 只读分析，无副作用
    }


def _generate_split_suggestions(total_lines: int, branch_count: int) -> list[dict]:
    """根据行数和分支数生成拆分建议。

    Args:
        total_lines: 函数总行数。
        branch_count: 分支数量。

    Returns:
        拆分建议列表。
    """
    suggestions: list[dict] = []

    # 如果分支多，建议提取验证逻辑
    if branch_count > 10:
        validate_lines = min(30, total_lines // 3)
        suggestions.append(
            {
                "name": "_validate_inputs",
                "lines": validate_lines,
                "reason": "输入验证逻辑可独立",
            }
        )

    # 如果行数很多，建议提取核心处理
    if total_lines > 100:
        core_lines = min(40, total_lines // 3)
        suggestions.append(
            {
                "name": "_process_core",
                "lines": core_lines,
                "reason": "核心处理逻辑可独立",
            }
        )

    # 如果行数超过 200，建议拆分错误处理
    if total_lines > 200:
        error_lines = min(25, total_lines // 5)
        suggestions.append(
            {
                "name": "_handle_errors",
                "lines": error_lines,
                "reason": "错误处理逻辑可独立",
            }
        )

    return suggestions


def extract_interface(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """提取接口建议 — 只读分析，从类中提取公开方法签名。

    输入：
        entity_id: 目标 CodeEntity 的 ID。
        context: {"class_methods": list[str]} 类方法名列表。
        graph_store: 图存储实例（只读访问）。

    输出（dict）：
        {
            "success": True,
            "entity_id": "...",
            "analysis": {
                "class_name": str,
                "public_methods": [...],
                "interface_name": str,
                "suggested_interface": {...},
            },
            "side_effects": [],
        }

    Raises:
        ValueError: entity_id 不存在。
    """
    node = graph_store.get_node(entity_id)
    if node is None:
        raise ValueError(f"Entity not found: {entity_id}")

    class_name = node.get("name", entity_id)
    raw_methods = context.get("class_methods", [])

    # 过滤公开方法（不以 _ 开头）
    public_methods = [m for m in raw_methods if not m.startswith("_")]

    # 生成接口名称建议（I + 类名）
    interface_name = f"I{class_name}"

    # 构造接口定义
    suggested_interface = {
        "name": interface_name,
        "methods": [
            {
                "name": method,
                "signature": f"def {method}(self, *args, **kwargs)",
            }
            for method in sorted(public_methods)
        ],
    }

    return {
        "success": True,
        "entity_id": entity_id,
        "analysis": {
            "class_name": class_name,
            "public_methods": sorted(public_methods),
            "interface_name": interface_name,
            "suggested_interface": suggested_interface,
        },
        "side_effects": [],
    }


def reduce_complexity(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """降低圈复杂度 — Phase 1 空壳。"""
    raise NotImplementedError("reduce_complexity will be implemented in Phase 2")


def generate_api_doc(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """生成 API 文档建议 — 只读分析，生成 Markdown 格式文档。

    输入：
        entity_id: 目标 CodeEntity 的 ID。
        context: 场景上下文。
        graph_store: 图存储实例（只读访问）。

    输出（dict）：
        {
            "success": True,
            "entity_id": "...",
            "analysis": {
                "entity_name": str,
                "entity_type": str,
                "doc_markdown": str,
            },
            "side_effects": [],
        }

    Raises:
        ValueError: entity_id 不存在。
    """
    node = graph_store.get_node(entity_id)
    if node is None:
        raise ValueError(f"Entity not found: {entity_id}")

    name = node.get("name", entity_id)
    entity_type = node.get("entityType", "unknown")
    params = node.get("params", [])
    return_type = node.get("return_type", "Any")
    docstring = node.get("docstring", "")

    # 生成 Markdown 文档
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

    # 如果没有参数信息，添加从 context 降级
    if not params and "params" in context:
        ctx_params = context["params"]
        if isinstance(ctx_params, list) and ctx_params:
            lines.append("**参数**:")
            lines.append("")
            for p in ctx_params:
                lines.append(f"- `{p}`")
            lines.append("")

    doc_markdown = "\n".join(lines)

    return {
        "success": True,
        "entity_id": entity_id,
        "analysis": {
            "entity_name": name,
            "entity_type": entity_type,
            "doc_markdown": doc_markdown,
        },
        "side_effects": [],
    }


def annotate_complex_logic(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """注释复杂逻辑 — Phase 1 空壳。"""
    raise NotImplementedError("annotate_complex_logic will be implemented in Phase 2")


def trace_call_chain(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """追踪调用链 — 只读分析，从指定实体出发追踪 CALLS 关系。

    输入：
        entity_id: 目标 CodeEntity 的 ID。
        context: {"depth": int (默认3)} 追踪深度。
        graph_store: 图存储实例（只读访问）。

    输出（dict）：
        {
            "success": True,
            "entity_id": "...",
            "analysis": {
                "depth": int,
                "call_tree": [...],
            },
            "side_effects": [],
        }

    Raises:
        ValueError: entity_id 不存在。
    """
    node = graph_store.get_node(entity_id)
    if node is None:
        raise ValueError(f"Entity not found: {entity_id}")

    depth = context.get("depth", 3)
    if not isinstance(depth, int) or depth < 1:
        depth = 3

    # Cypher 查询：追踪 CALLS 关系链
    cypher = (
        f"MATCH (caller:CodeEntity)-[:CALLS*1..{depth}]->(callee:CodeEntity) "
        "WHERE caller.id = $entity_id "
        "RETURN callee.id AS id, callee.name AS name, callee.entityType AS entity_type"
    )
    results = graph_store.query(cypher, {"entity_id": entity_id})

    # 构建调用树
    call_tree: list[dict] = []
    for row in results:
        call_tree.append(
            {
                "id": row.get("id", ""),
                "name": row.get("name", ""),
                "entity_type": row.get("entity_type", ""),
            }
        )

    return {
        "success": True,
        "entity_id": entity_id,
        "analysis": {
            "depth": depth,
            "call_tree": call_tree,
        },
        "side_effects": [],
    }


def find_dependent_modules(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """查找依赖模块 — Phase 1 空壳。"""
    raise NotImplementedError("find_dependent_modules will be implemented in Phase 2")
