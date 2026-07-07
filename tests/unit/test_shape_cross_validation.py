from __future__ import annotations

import pytest

from ontoagent.domain.schema import (
    VALID_ENTITY_LABELS,
    build_entity_field_index,
    entity_field_names,
)
from ontoagent.domain.shapes import (
    ConstraintExpr,
    ConstraintShape,
    Operation,
    PathExpression,
    Severity,
    ShapeKind,
    ShapeTarget,
)
from ontoagent.execution.shape_registry import ShapeRegistry


def _make_shape(
    entry_type: str = "CodeEntity",
    target_label: str = "CodeEntity",
    field: str = "name",
    operator: str = "exists",
) -> ConstraintShape:
    """构造最小可用的 ConstraintShape 用于测试。"""
    path_raw = "SELF" if entry_type == target_label else f"PROCESSES_DATA -> {target_label}"
    return ConstraintShape(
        id="shape:cross_test",
        name="cross_test",
        description="检验字段存在性",
        kind=ShapeKind.OPERATIONAL,
        target=ShapeTarget(entry_type=entry_type, operation=Operation.UPDATE),
        path=PathExpression.parse(path_raw),
        constraint=ConstraintExpr(field=field, operator=operator),
        severity=Severity.WARN,
    )


class TestValidateCrossShape:
    """validate_cross_shape 单元测试。"""

    def test_self_path_valid_field(self) -> None:
        """SELF path + DataAsset + sensitivity 字段应通过。"""
        registry = ShapeRegistry(valid_labels=set(VALID_ENTITY_LABELS))
        field_index = build_entity_field_index()
        shape = _make_shape(entry_type="DataAsset", target_label="DataAsset", field="sensitivity")
        registry.validate_cross_shape(shape, field_index)

    def test_self_path_invalid_field_raises(self) -> None:
        """SELF + 不存在的字段应抛 ValueError。"""
        registry = ShapeRegistry(valid_labels=set(VALID_ENTITY_LABELS))
        field_index = build_entity_field_index()
        shape = _make_shape(entry_type="DataAsset", target_label="DataAsset", field="nonexistent_field_xyz")
        with pytest.raises(ValueError, match="nonexistent_field_xyz"):
            registry.validate_cross_shape(shape, field_index)

    def test_rel_path_validates_target_endpoint(self) -> None:
        """非 SELF path + target_label=DataAsset + sensitivity 应通过。"""
        registry = ShapeRegistry(valid_labels=set(VALID_ENTITY_LABELS))
        field_index = build_entity_field_index()
        shape = _make_shape(entry_type="CodeEntity", target_label="DataAsset", field="sensitivity")
        registry.validate_cross_shape(shape, field_index)

    def test_rel_path_invalid_field_raises(self) -> None:
        """非 SELF path + 不存在的字段应抛 ValueError。"""
        registry = ShapeRegistry(valid_labels=set(VALID_ENTITY_LABELS))
        field_index = build_entity_field_index()
        shape = _make_shape(entry_type="CodeEntity", target_label="DataAsset", field="nonexistent_field_xyz")
        with pytest.raises(ValueError, match="nonexistent_field_xyz"):
            registry.validate_cross_shape(shape, field_index)

    def test_field_on_wrong_endpoint_rejected(self) -> None:
        """非 SELF + CodeEntity endpoint + sensitivity(不属于CodeEntity) → 抛。"""
        registry = ShapeRegistry(valid_labels=set(VALID_ENTITY_LABELS))
        field_index = build_entity_field_index()
        shape = _make_shape(entry_type="DataAsset", target_label="CodeEntity", field="sensitivity")
        with pytest.raises(ValueError, match="sensitivity"):
            registry.validate_cross_shape(shape, field_index)


class TestEntityFieldIndex:
    """build_entity_field_index / entity_field_names 单元测试。"""

    def test_covers_all_valid_labels(self) -> None:
        """field_index keys 应覆盖 VALID_ENTITY_LABELS。"""
        field_index = build_entity_field_index()
        for label in VALID_ENTITY_LABELS:
            assert label in field_index, f"{label} 不在 field_index 中"
            assert len(field_index[label]) > 0, f"{label} 的字段集为空"

    def test_unknown_label_returns_empty(self) -> None:
        """未知 label 返回空 set。"""
        assert entity_field_names("NonexistentLabel") == set()

    def test_valid_field_index(self) -> None:
        """DataAsset 应包含 sensitivity 和 dataType（camelCase 转换后）。"""
        fields = entity_field_names("DataAsset")
        assert "sensitivity" in fields
        assert "dataType" in fields
        # 确认 snake_case 原版不在结果中
        assert "data_type" not in fields
