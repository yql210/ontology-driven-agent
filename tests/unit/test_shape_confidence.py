"""Step 1 — Shape Confidence 维度的单元测试。

覆盖：
- ConstraintShape.effective_severity 根据 confidence 降级的逻辑（4 级 severity 阶梯）。
- ConstraintShape.from_yaml_dict 解析 confidence / source / rationale 字段，含默认值与非法值。
- pipeline/shapes.yaml 全量加载后所有 Shape 仍可用（向后兼容）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ontoagent.domain.exceptions import SchemaValidationError
from ontoagent.domain.shapes import (
    ConstraintExpr,
    ConstraintShape,
    Operation,
    PathExpression,
    Severity,
    ShapeKind,
    ShapeTarget,
)

SHAPES_YAML = Path(__file__).resolve().parents[2] / "src" / "ontoagent" / "pipeline" / "shapes.yaml"


def _make_shape(severity: Severity = Severity.WARN, confidence: float = 1.0) -> ConstraintShape:
    """构造最小可用的 ConstraintShape 用于测试。"""
    return ConstraintShape(
        id="shape:test",
        name="test",
        description="test shape",
        kind=ShapeKind.OPERATIONAL,
        target=ShapeTarget(entry_type="CodeEntity", operation=Operation.UPDATE),
        path=PathExpression.parse("SELF"),
        constraint=ConstraintExpr(field="name", operator="exists"),
        severity=severity,
        confidence=confidence,
    )


# =============================================================================
# effective_severity — 高置信度（>= 0.9）保持原始级别
# =============================================================================


@pytest.mark.unit
def test_effective_severity_high_confidence_returns_original():
    """confidence=0.95 + severity=BLOCK → effective_severity == BLOCK。"""
    shape = _make_shape(severity=Severity.BLOCK, confidence=0.95)

    assert shape.effective_severity == Severity.BLOCK


# =============================================================================
# effective_severity — 中置信度 [0.7, 0.9) 降一级
# =============================================================================


@pytest.mark.unit
def test_effective_severity_medium_confidence_demotes_one_level():
    """confidence=0.8 时 BLOCK → WARN；ESCALATE → BLOCK。"""
    block_shape = _make_shape(severity=Severity.BLOCK, confidence=0.8)
    escalate_shape = _make_shape(severity=Severity.ESCALATE, confidence=0.8)

    assert block_shape.effective_severity == Severity.WARN
    assert escalate_shape.effective_severity == Severity.BLOCK


# =============================================================================
# effective_severity — 低置信度 (< 0.7) 降两级
# =============================================================================


@pytest.mark.unit
def test_effective_severity_low_confidence_demotes_two_levels():
    """confidence=0.5 时 BLOCK → ALLOW；ESCALATE → WARN。"""
    block_shape = _make_shape(severity=Severity.BLOCK, confidence=0.5)
    escalate_shape = _make_shape(severity=Severity.ESCALATE, confidence=0.5)

    assert block_shape.effective_severity == Severity.ALLOW
    assert escalate_shape.effective_severity == Severity.WARN


# =============================================================================
# effective_severity — ALLOW 不再降
# =============================================================================


@pytest.mark.unit
def test_effective_severity_allow_stays_allow():
    """confidence=0.1 + ALLOW → ALLOW（已最低，不再降）。"""
    shape = _make_shape(severity=Severity.ALLOW, confidence=0.1)

    assert shape.effective_severity == Severity.ALLOW


# =============================================================================
# effective_severity — 中阈值边界 0.9
# =============================================================================


@pytest.mark.unit
def test_effective_severity_boundary_at_medium_threshold():
    """confidence=0.9 保持原级；0.89 降一级。"""
    at_threshold = _make_shape(severity=Severity.BLOCK, confidence=0.9)
    just_below = _make_shape(severity=Severity.BLOCK, confidence=0.89)

    assert at_threshold.effective_severity == Severity.BLOCK
    assert just_below.effective_severity == Severity.WARN


# =============================================================================
# effective_severity — 低阈值边界 0.7
# =============================================================================


@pytest.mark.unit
def test_effective_severity_boundary_at_low_threshold():
    """confidence=0.7 降一级；0.69 降两级。"""
    at_threshold = _make_shape(severity=Severity.BLOCK, confidence=0.7)
    just_below = _make_shape(severity=Severity.BLOCK, confidence=0.69)

    assert at_threshold.effective_severity == Severity.WARN
    assert just_below.effective_severity == Severity.ALLOW


# =============================================================================
# from_yaml_dict — 解析三字段
# =============================================================================


@pytest.mark.unit
def test_from_yaml_dict_parses_confidence_source_rationale():
    """显式传入 confidence/source/rationale 时被正确解析。"""
    data = {
        "id": "shape:test_yaml",
        "name": "test_yaml",
        "description": "test",
        "kind": "operational",
        "target": {"entry_type": "CodeEntity", "operation": "UPDATE"},
        "path": "SELF",
        "constraint": {"field": "name", "operator": "exists"},
        "severity": "warn",
        "confidence": 0.85,
        "source": "llm_extraction",
        "rationale": "通过 AST 调用链推断",
    }

    shape = ConstraintShape.from_yaml_dict(data)

    assert shape.confidence == pytest.approx(0.85)
    assert shape.source == "llm_extraction"
    assert shape.rationale == "通过 AST 调用链推断"


# =============================================================================
# from_yaml_dict — 默认值
# =============================================================================


@pytest.mark.unit
def test_from_yaml_dict_defaults_confidence_one_manual_empty_rationale():
    """省略三字段时：confidence=1.0、source='manual'、rationale=''。"""
    data = {
        "id": "shape:test_default",
        "name": "test_default",
        "description": "test",
        "target": {"entry_type": "CodeEntity", "operation": "UPDATE"},
        "path": "SELF",
        "constraint": {"field": "name", "operator": "exists"},
        "severity": "warn",
    }

    shape = ConstraintShape.from_yaml_dict(data)

    assert shape.confidence == pytest.approx(1.0)
    assert shape.source == "manual"
    assert shape.rationale == ""


# =============================================================================
# from_yaml_dict — 非法 confidence 抛 SchemaValidationError
# =============================================================================


@pytest.mark.unit
def test_from_yaml_dict_rejects_out_of_range_confidence():
    """confidence=1.5 超出 [0.0, 1.0]，抛 SchemaValidationError。"""
    data = {
        "id": "shape:test_invalid",
        "name": "test_invalid",
        "description": "test",
        "target": {"entry_type": "CodeEntity", "operation": "UPDATE"},
        "path": "SELF",
        "constraint": {"field": "name", "operator": "exists"},
        "severity": "warn",
        "confidence": 1.5,
    }

    with pytest.raises(SchemaValidationError):
        ConstraintShape.from_yaml_dict(data)


# =============================================================================
# 现有 shapes.yaml 向后兼容
# =============================================================================


@pytest.mark.unit
def test_existing_shapes_yaml_loads_without_changes():
    """加载 pipeline/shapes.yaml 全量成功，且新字段使用默认值。"""
    from ontoagent.execution.shape_registry import ShapeRegistry

    valid_labels = {"CodeEntity", "DataAsset", "ComplianceItem", "Service", "ModuleEntity"}
    registry = ShapeRegistry(valid_labels=valid_labels)
    registry.load_from_yaml(SHAPES_YAML)

    all_shapes = list(registry._shapes.values())
    assert len(all_shapes) >= 4, "shapes.yaml 至少应加载 4 条 Shape"

    for shape in all_shapes:
        assert shape.confidence == pytest.approx(1.0)
        assert shape.source == "manual"
        assert shape.rationale == ""
