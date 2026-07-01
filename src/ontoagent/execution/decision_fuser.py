"""DecisionFuser — 将多个 ShapeResult 融合为单一决策。

融合策略（V4 形状约束模型决策层）：
    1. BLOCK > ESCALATE > WARN > ALLOW 严格优先。
    2. 同 severity 内按 shape.priority 降序。
    3. 多个 BLOCK：priority 高的 suggestion 优先；同 priority 取词级交集；无交集升级 ESCALATE。
    4. 任一触发 Shape 缺少 priority (≤0) → 强制 ESCALATE。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ontoagent.domain.shapes import Severity
from ontoagent.execution.shape_evaluator import ShapeResult

__all__ = ["DecisionFuser", "DecisionReport"]

# Severity 越大越严苛；用于 rule 1 的严格优先比较。
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.ALLOW: 0,
    Severity.WARN: 1,
    Severity.ESCALATE: 2,
    Severity.BLOCK: 3,
}


@dataclass
class DecisionReport:
    """融合后的决策报告。

    Attributes:
        severity: 最终处置级别。
        suggestion: 给 Agent 的人类可读建议（融合后）。
        triggered: 所有触发 Shape 的详情列表，每项为 dict（含 shape_id/severity/suggestion/priority/evidence）。
    """

    severity: Severity
    suggestion: str
    triggered: list[dict[str, Any]] = field(default_factory=list)


class DecisionFuser:
    """将 ShapeEvaluator 产出的 ShapeResult 列表融合为单一 DecisionReport。"""

    @staticmethod
    def fuse(results: list[ShapeResult]) -> DecisionReport:
        """融合多个 ShapeResult 为单一决策。

        Args:
            results: ShapeEvaluator.evaluate() 的返回值（含未触发的项）。

        Returns:
            DecisionReport，含最终 severity、fused suggestion、triggered 详情。
        """
        triggered = [r for r in results if r.triggered]
        if not triggered:
            return DecisionReport(severity=Severity.ALLOW, suggestion="", triggered=[])

        details = _build_triggered_details(triggered)

        # Rule 4: 缺少 priority → 强制 ESCALATE
        missing = [r for r in triggered if r.shape.priority <= 0]
        if missing:
            ids = ", ".join(r.shape.id for r in missing)
            return DecisionReport(
                severity=Severity.ESCALATE,
                suggestion=f"Shape 缺少 priority，强制升级人工审批: {ids}",
                triggered=details,
            )

        # Rule 1: 严格 severity 优先
        top_severity = max(triggered, key=lambda r: _SEVERITY_RANK[r.severity]).severity
        top_tier = [r for r in triggered if r.severity == top_severity]

        # Rule 2: 同 severity 内按 priority 降序
        top_tier.sort(key=lambda r: r.shape.priority, reverse=True)
        max_priority = top_tier[0].shape.priority

        # Rule 3: 多个 BLOCK 同 priority → 取交集；无交集 → ESCALATE
        if top_severity == Severity.BLOCK and len(top_tier) > 1:
            same_priority = [r for r in top_tier if r.shape.priority == max_priority]
            if len(same_priority) > 1:
                fused = _intersect_suggestions([r.shape.suggestion for r in same_priority])
                if not fused:
                    ids = ", ".join(r.shape.id for r in same_priority)
                    return DecisionReport(
                        severity=Severity.ESCALATE,
                        suggestion=f"多个 BLOCK Shape 建议冲突，升级人工审批: {ids}",
                        triggered=details,
                    )
                return DecisionReport(severity=Severity.BLOCK, suggestion=fused, triggered=details)

        return DecisionReport(
            severity=top_severity,
            suggestion=top_tier[0].shape.suggestion,
            triggered=details,
        )


# ============================================================
# 内部辅助
# ============================================================


def _build_triggered_details(results: list[ShapeResult]) -> list[dict[str, Any]]:
    """构造触发 Shape 的详情列表，供 Agent / 审计查看。"""
    return [
        {
            "shape_id": r.shape.id,
            "severity": r.severity,
            "suggestion": r.shape.suggestion,
            "priority": r.shape.priority,
            "evidence": r.evidence,
        }
        for r in results
    ]


def _intersect_suggestions(suggestions: list[str]) -> str:
    """对多条 suggestion 按行去重合并，保留原文语义。

    多条 BLOCK Shape 同时触发时，将它们的 suggestion 按行拆分、
    去重后拼接。避免原 word-level intersection 对中文输出无意义结果。

    Args:
        suggestions: 多条 suggestion 字符串。

    Returns:
        按空行分隔的去重合并文本；均为空时返回 ""。
    """
    if not suggestions:
        return ""

    lines: list[str] = []
    seen: set[str] = set()
    for s in suggestions:
        for line in s.split("\n"):
            stripped = line.strip()
            if stripped and stripped not in seen:
                lines.append(stripped)
                seen.add(stripped)
    return "\n".join(lines)
