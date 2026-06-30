"""DecisionFuser 单元测试。"""

from __future__ import annotations

from ontoagent.domain.shapes import (
    ConstraintExpr,
    ConstraintShape,
    Operation,
    PathExpression,
    Severity,
    ShapeKind,
    ShapeTarget,
)
from ontoagent.execution.decision_fuser import DecisionFuser, DecisionReport
from ontoagent.execution.shape_evaluator import ShapeResult


def _make_shape(
    shape_id: str,
    severity: Severity,
    priority: int = 1,
    suggestion: str = "",
) -> ConstraintShape:
    """构造最小 ConstraintShape 用于测试。"""
    return ConstraintShape(
        id=shape_id,
        name=shape_id,
        description="test shape",
        kind=ShapeKind.OPERATIONAL,
        target=ShapeTarget(resource_type="CodeEntity", operation=Operation.DELETE),
        path=PathExpression.parse("SELF"),
        constraint=ConstraintExpr(field="sensitivity", operator="in", value=["high"]),
        severity=severity,
        priority=priority,
        suggestion=suggestion,
    )


def _make_result(shape: ConstraintShape, triggered: bool = True) -> ShapeResult:
    """构造 ShapeResult。"""
    return ShapeResult(
        shape=shape,
        severity=shape.severity,
        evidence={
            "field": shape.constraint.field,
            "values": ["high"],
            "operator": shape.constraint.operator,
            "expected": shape.constraint.value,
            "path": shape.path.raw,
            "entity_id": "ent-1",
        },
        triggered=triggered,
    )


# ============================================================
# 空输入 / 全未触发
# ============================================================


class TestEmptyAndUntriggered:
    def test_empty_input_returns_allow(self):
        report = DecisionFuser.fuse([])
        assert report.severity == Severity.ALLOW
        assert report.suggestion == ""
        assert report.triggered == []

    def test_all_untriggered_returns_allow(self):
        shape = _make_shape("s1", Severity.WARN, priority=1, suggestion="x")
        report = DecisionFuser.fuse([_make_result(shape, triggered=False)])
        assert report.severity == Severity.ALLOW
        assert report.suggestion == ""
        assert report.triggered == []


# ============================================================
# 单 severity
# ============================================================


class TestSingleSeverity:
    def test_single_warn(self):
        shape = _make_shape("s1", Severity.WARN, priority=1, suggestion="warn-msg")
        report = DecisionFuser.fuse([_make_result(shape)])
        assert report.severity == Severity.WARN
        assert report.suggestion == "warn-msg"

    def test_single_block(self):
        shape = _make_shape("s1", Severity.BLOCK, priority=1, suggestion="block-msg")
        report = DecisionFuser.fuse([_make_result(shape)])
        assert report.severity == Severity.BLOCK
        assert report.suggestion == "block-msg"

    def test_single_escalate(self):
        shape = _make_shape("s1", Severity.ESCALATE, priority=1, suggestion="esc-msg")
        report = DecisionFuser.fuse([_make_result(shape)])
        assert report.severity == Severity.ESCALATE
        assert report.suggestion == "esc-msg"

    def test_single_allow(self):
        shape = _make_shape("s1", Severity.ALLOW, priority=1, suggestion="ok")
        report = DecisionFuser.fuse([_make_result(shape)])
        assert report.severity == Severity.ALLOW
        assert report.suggestion == "ok"


# ============================================================
# Rule 1: severity 严格优先
# ============================================================


class TestSeverityPriority:
    def test_block_dominates_warn(self):
        w = _make_shape("w", Severity.WARN, priority=1, suggestion="w-msg")
        b = _make_shape("b", Severity.BLOCK, priority=1, suggestion="b-msg")
        report = DecisionFuser.fuse([_make_result(w), _make_result(b)])
        assert report.severity == Severity.BLOCK
        assert report.suggestion == "b-msg"

    def test_escalate_dominates_warn(self):
        w = _make_shape("w", Severity.WARN, priority=1, suggestion="w-msg")
        e = _make_shape("e", Severity.ESCALATE, priority=1, suggestion="e-msg")
        report = DecisionFuser.fuse([_make_result(w), _make_result(e)])
        assert report.severity == Severity.ESCALATE
        assert report.suggestion == "e-msg"

    def test_block_dominates_escalate(self):
        e = _make_shape("e", Severity.ESCALATE, priority=1, suggestion="e-msg")
        b = _make_shape("b", Severity.BLOCK, priority=1, suggestion="b-msg")
        report = DecisionFuser.fuse([_make_result(e), _make_result(b)])
        assert report.severity == Severity.BLOCK
        assert report.suggestion == "b-msg"

    def test_block_dominates_all(self):
        shapes = [
            _make_shape("a", Severity.ALLOW, priority=1, suggestion="a"),
            _make_shape("w", Severity.WARN, priority=1, suggestion="w"),
            _make_shape("e", Severity.ESCALATE, priority=1, suggestion="e"),
            _make_shape("b", Severity.BLOCK, priority=1, suggestion="b"),
        ]
        report = DecisionFuser.fuse([_make_result(s) for s in shapes])
        assert report.severity == Severity.BLOCK
        assert report.suggestion == "b"


# ============================================================
# Rule 2: 同 severity 内 priority 降序
# ============================================================


class TestPriorityOrdering:
    def test_higher_priority_wins_within_warn(self):
        low = _make_shape("low", Severity.WARN, priority=1, suggestion="low-msg")
        high = _make_shape("high", Severity.WARN, priority=5, suggestion="high-msg")
        report = DecisionFuser.fuse([_make_result(low), _make_result(high)])
        assert report.severity == Severity.WARN
        assert report.suggestion == "high-msg"

    def test_higher_priority_wins_within_escalate(self):
        low = _make_shape("low", Severity.ESCALATE, priority=1, suggestion="low-msg")
        high = _make_shape("high", Severity.ESCALATE, priority=9, suggestion="high-msg")
        report = DecisionFuser.fuse([_make_result(low), _make_result(high)])
        assert report.severity == Severity.ESCALATE
        assert report.suggestion == "high-msg"


# ============================================================
# Rule 4: 缺少 priority → 强制 ESCALATE
# ============================================================


class TestMissingPriority:
    def test_single_shape_zero_priority_forces_escalate(self):
        shape = _make_shape("s1", Severity.WARN, priority=0, suggestion="x")
        report = DecisionFuser.fuse([_make_result(shape)])
        assert report.severity == Severity.ESCALATE

    def test_one_missing_priority_among_others_forces_escalate(self):
        ok = _make_shape("ok", Severity.BLOCK, priority=5, suggestion="ok")
        bad = _make_shape("bad", Severity.WARN, priority=0, suggestion="bad")
        report = DecisionFuser.fuse([_make_result(ok), _make_result(bad)])
        assert report.severity == Severity.ESCALATE

    def test_negative_priority_treated_as_missing(self):
        shape = _make_shape("s1", Severity.WARN, priority=-1, suggestion="x")
        report = DecisionFuser.fuse([_make_result(shape)])
        assert report.severity == Severity.ESCALATE


# ============================================================
# Rule 3: 多个 BLOCK
# ============================================================


class TestMultipleBlock:
    def test_different_priority_higher_wins(self):
        low = _make_shape("low", Severity.BLOCK, priority=1, suggestion="low-block")
        high = _make_shape("high", Severity.BLOCK, priority=5, suggestion="high-block")
        report = DecisionFuser.fuse([_make_result(low), _make_result(high)])
        assert report.severity == Severity.BLOCK
        assert report.suggestion == "high-block"

    def test_same_priority_intersects_suggestions(self):
        a = _make_shape(
            "a",
            Severity.BLOCK,
            priority=5,
            suggestion="Require SOX approval before deletion",
        )
        b = _make_shape(
            "b",
            Severity.BLOCK,
            priority=5,
            suggestion="Require SOX approval from compliance",
        )
        report = DecisionFuser.fuse([_make_result(a), _make_result(b)])
        assert report.severity == Severity.BLOCK
        lowered = report.suggestion.lower()
        assert "require" in lowered
        assert "sox" in lowered
        assert "approval" in lowered

    def test_same_priority_no_intersection_escalates(self):
        a = _make_shape("a", Severity.BLOCK, priority=5, suggestion="Backup data before deletion")
        b = _make_shape("b", Severity.BLOCK, priority=5, suggestion="Notify compliance team")
        report = DecisionFuser.fuse([_make_result(a), _make_result(b)])
        assert report.severity == Severity.ESCALATE

    def test_three_blocks_same_priority_intersects(self):
        a = _make_shape("a", Severity.BLOCK, priority=5, suggestion="Require approval SOX")
        b = _make_shape("b", Severity.BLOCK, priority=5, suggestion="Require approval GDPR")
        c = _make_shape("c", Severity.BLOCK, priority=5, suggestion="Require approval audit")
        report = DecisionFuser.fuse([_make_result(a), _make_result(b), _make_result(c)])
        assert report.severity == Severity.BLOCK
        lowered = report.suggestion.lower()
        assert "require" in lowered
        assert "approval" in lowered


# ============================================================
# Triggered 列表详情
# ============================================================


class TestTriggeredList:
    def test_contains_all_triggered_shapes(self):
        s1 = _make_shape("s1", Severity.WARN, priority=1, suggestion="m1")
        s2 = _make_shape("s2", Severity.BLOCK, priority=2, suggestion="m2")
        report = DecisionFuser.fuse([_make_result(s1), _make_result(s2)])
        assert len(report.triggered) == 2
        ids = {d["shape_id"] for d in report.triggered}
        assert ids == {"s1", "s2"}

    def test_detail_has_required_fields(self):
        shape = _make_shape("s1", Severity.BLOCK, priority=3, suggestion="nope")
        report = DecisionFuser.fuse([_make_result(shape)])
        assert len(report.triggered) == 1
        detail = report.triggered[0]
        assert detail["shape_id"] == "s1"
        assert detail["severity"] == Severity.BLOCK
        assert detail["priority"] == 3
        assert detail["suggestion"] == "nope"
        assert "evidence" in detail
        assert detail["evidence"]["field"] == "sensitivity"

    def test_untriggered_shapes_excluded_from_list(self):
        triggered = _make_shape("t", Severity.WARN, priority=1, suggestion="t")
        untriggered = _make_shape("u", Severity.BLOCK, priority=2, suggestion="u")
        results = [
            _make_result(triggered, triggered=True),
            _make_result(untriggered, triggered=False),
        ]
        report = DecisionFuser.fuse(results)
        assert len(report.triggered) == 1
        assert report.triggered[0]["shape_id"] == "t"


# ============================================================
# DecisionReport 数据类
# ============================================================


class TestDecisionReport:
    def test_report_is_dataclass_instance(self):
        shape = _make_shape("s1", Severity.WARN, priority=1, suggestion="x")
        report = DecisionFuser.fuse([_make_result(shape)])
        assert isinstance(report, DecisionReport)
        assert hasattr(report, "severity")
        assert hasattr(report, "suggestion")
        assert hasattr(report, "triggered")
