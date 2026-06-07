"""CodeEntity 相关 Action Function 实现。

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
    """提取接口 — Phase 1 空壳。"""
    raise NotImplementedError("extract_interface will be implemented in Phase 2")


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
    """生成API文档 — Phase 1 空壳。"""
    raise NotImplementedError("generate_api_doc will be implemented in Phase 2")


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
    """追踪调用链 — Phase 1 空壳。"""
    raise NotImplementedError("trace_call_chain will be implemented in Phase 2")


def find_dependent_modules(
    entity_id: str,
    context: dict,
    graph_store: Any,
) -> dict:
    """查找依赖模块 — Phase 1 空壳。"""
    raise NotImplementedError("find_dependent_modules will be implemented in Phase 2")
