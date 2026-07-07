"""ShapeTarget ontology_ref 字段与 entry_type 重命名 测试。"""
import pytest

from ontoagent.domain.shapes import ConstraintShape, Operation, ShapeTarget


def test_shape_target_accepts_ontology_ref():
    """新增 ontology_ref 字段可正确设置和读取"""
    target = ShapeTarget(
        entry_type="ResourceEntity",
        operation=Operation.UPDATE,
        ontology_ref="客户",
    )
    assert target.ontology_ref == "客户"
    assert target.entry_type == "ResourceEntity"


def test_shape_target_ontology_ref_defaults_to_none():
    """未提供 ontology_ref 时默认 None"""
    target = ShapeTarget(entry_type="CodeEntity", operation=Operation.READ)
    assert target.ontology_ref is None


def test_from_yaml_dict_legacy_resource_type_compat():
    """旧 YAML 含 resource_type 字段时能 fallback 解析"""
    data = {
        "id": "test:legacy",
        "name": "Legacy Shape",
        "description": "Test",
        "kind": "operational",
        "target": {"resource_type": "CodeEntity", "operation": "READ"},
        "path": "SELF",
        "constraint": {"field": "test"},
        "severity": "warn",
    }
    shape = ConstraintShape.from_yaml_dict(data)
    assert shape.target.entry_type == "CodeEntity"
    assert shape.target.ontology_ref is None


def test_from_yaml_dict_entry_type_takes_precedence():
    """同时有 entry_type 和 resource_type 时，entry_type 优先"""
    data = {
        "id": "test:dual",
        "name": "Dual Shape",
        "description": "Test",
        "kind": "operational",
        "target": {
            "entry_type": "ResourceEntity",
            "resource_type": "CodeEntity",
            "operation": "UPDATE",
        },
        "path": "SELF",
        "constraint": {"field": "test"},
        "severity": "block",
    }
    shape = ConstraintShape.from_yaml_dict(data)
    assert shape.target.entry_type == "ResourceEntity"


def test_from_yaml_dict_missing_entry_type_raises():
    """entry_type 和 resource_type 都缺失时显式报错"""
    data = {
        "id": "test:missing",
        "name": "Missing Entry",
        "description": "Test",
        "kind": "operational",
        "target": {"operation": "READ"},
        "path": "SELF",
        "constraint": {"field": "test"},
        "severity": "warn",
    }
    with pytest.raises(ValueError, match="entry_type"):
        ConstraintShape.from_yaml_dict(data)


def test_from_yaml_dict_reads_ontology_ref():
    """YAML 含 ontology_ref 字段时正确解析"""
    data = {
        "id": "test:with_ref",
        "name": "With Ref",
        "description": "Test",
        "kind": "operational",
        "target": {
            "entry_type": "ResourceEntity",
            "operation": "UPDATE",
            "ontology_ref": "订单表",
        },
        "path": "SELF",
        "constraint": {"field": "test"},
        "severity": "warn",
    }
    shape = ConstraintShape.from_yaml_dict(data)
    assert shape.target.ontology_ref == "订单表"


def test_shape_target_field_order_respects_backward_compat():
    """字段顺序：ontology_ref 在 field_filter 之后，避免位置参数误传"""
    # 验证用两个位置参数（旧代码模式）调用不会崩溃
    target = ShapeTarget("CodeEntity", Operation.READ, None)
    assert target.entry_type == "CodeEntity"
    assert target.ontology_ref is None  # 第三个位置参数是 field_filter，不是 ontology_ref
    assert target.field_filter is None
