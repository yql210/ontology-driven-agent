"""ontology_loader 模块单元测试。

测试 ontology.json → shapes.yaml 格式转换器的所有转换规则。
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import yaml

from ontoagent.domain.shapes import ConstraintShape, Severity
from ontoagent.pipeline.ontology_loader import (
    _camel_to_upper_snake,
    _convert_axioms,
    _convert_entity_types,
    _convert_properties,
    _convert_relations,
    _lower_severity,
    load_ontology_to_shapes,
    write_shapes_yaml,
)

# =============================================================================
# 工具函数测试
# =============================================================================


@pytest.mark.unit
class TestCamelToUpperSnake:
    """测试 camelCase → UPPER_SNAKE_CASE 转换。"""

    def test_simple_camel(self):
        assert _camel_to_upper_snake("hasCustomer") == "HAS_CUSTOMER"

    def test_single_word(self):
        assert _camel_to_upper_snake("name") == "NAME"

    def test_multiple_caps(self):
        assert _camel_to_upper_snake("orderId") == "ORDER_ID"

    def test_already_upper(self):
        assert _camel_to_upper_snake("HAS_CUSTOMER") == "HAS_CUSTOMER"


@pytest.mark.unit
class TestLowerSeverity:
    """测试 severity 降级逻辑。"""

    def test_downgrade_block_to_warn(self):
        assert _lower_severity("block") == "warn"

    def test_downgrade_escalate_to_block(self):
        assert _lower_severity("escalate") == "block"

    def test_downgrade_warn_to_allow(self):
        assert _lower_severity("warn") == "allow"

    def test_allow_stays_allow(self):
        assert _lower_severity("allow") == "allow"

    def test_downgrade_two_levels(self):
        assert _lower_severity("block", 2) == "allow"

    def test_unknown_severity_unchanged(self):
        assert _lower_severity("unknown") == "unknown"


# =============================================================================
# 转换函数测试
# =============================================================================


@pytest.mark.unit
class TestConvertEntityTypes:
    """测试 entity_types → shapes 转换。"""

    def test_converts_entity_with_required_fields(self):
        data = {
            "entity_types": [
                {
                    "name": "客户",
                    "id": "concept_001",
                    "description": "客户信息",
                    "source": "rdb",
                    "confidence": 0.8,
                    "is_entity_type": True,
                }
            ]
        }
        shapes = _convert_entity_types(data)
        assert len(shapes) == 1
        s = shapes[0]
        assert s["id"] == "shape:entity_concept_001"
        assert s["name"] == "实体约束: 客户"
        assert s["target"]["entry_type"] == "ResourceEntity"
        assert s["target"]["ontology_ref"] == "客户"
        assert s["target"]["operation"] == "UPDATE"
        assert s["path"] == "SELF"
        assert s["severity"] == "warn"
        assert s["source"] == "imported"

    def test_skips_non_entity_type(self):
        data = {
            "entity_types": [
                {
                    "name": "非实体",
                    "id": "concept_002",
                    "confidence": 0.5,
                    "is_entity_type": False,
                }
            ]
        }
        shapes = _convert_entity_types(data)
        assert len(shapes) == 0

    def test_multiple_entities(self):
        data = {
            "entity_types": [
                {"name": "A", "id": "c1", "confidence": 0.8, "is_entity_type": True},
                {"name": "B", "id": "c2", "confidence": 0.6, "is_entity_type": True},
                {"name": "C", "id": "c3", "confidence": 0.3, "is_entity_type": False},
            ]
        }
        shapes = _convert_entity_types(data)
        assert len(shapes) == 2

    def test_entity_without_explicit_entity_type_flag(self):
        """is_entity_type 未指定时默认为 True。"""
        data = {"entity_types": [{"name": "X", "id": "cx", "confidence": 0.8}]}
        shapes = _convert_entity_types(data)
        assert len(shapes) == 1


@pytest.mark.unit
class TestConvertAxioms:
    """测试 axioms → shapes 转换。"""

    def test_axiom_below_confidence_dropped(self):
        data = {
            "axioms": [
                {
                    "axiom_type": "DOMAIN",
                    "subject": {"concept_id": "c1"},
                    "obj": {"concept_id": "c2"},
                    "confidence": 0.5,
                    "rationale": "low conf",
                }
            ],
            "entity_types": [],
        }
        shapes = _convert_axioms(data)
        assert len(shapes) == 0

    def test_domain_axiom_creates_warn_shape(self):
        data = {
            "axioms": [
                {
                    "axiom_type": "DOMAIN",
                    "subject": {"concept_id": "c1"},
                    "obj": {"concept_id": "c2"},
                    "confidence": 0.85,
                    "rationale": "test domain",
                }
            ],
            "entity_types": [
                {"name": "Entity1", "id": "c1"},
                {"name": "Entity2", "id": "c2"},
            ],
        }
        shapes = _convert_axioms(data)
        assert len(shapes) == 1
        s = shapes[0]
        assert s["severity"] == "allow"  # warn 降一级: warn → allow
        assert s["path"] == "SELF"
        assert s["source"] == "imported"
        assert "test domain" in s["suggestion"]

    def test_range_axiom_creates_warn_shape(self):
        data = {
            "axioms": [
                {
                    "axiom_type": "RANGE",
                    "subject": {"concept_id": "c1"},
                    "obj": {"concept_id": "c2"},
                    "confidence": 0.95,
                    "rationale": "range check",
                }
            ],
            "entity_types": [
                {"name": "E1", "id": "c1"},
                {"name": "E2", "id": "c2"},
            ],
        }
        shapes = _convert_axioms(data)
        assert len(shapes) == 1
        s = shapes[0]
        assert s["severity"] == "warn"  # 高置信度保持
        assert "RANGE" in s["name"]

    def test_disjoint_with_axiom_bidirectional(self):
        data = {
            "axioms": [
                {
                    "axiom_type": "DISJOINT_WITH",
                    "subject": {"concept_id": "c1"},
                    "obj": {"concept_id": "c2"},
                    "confidence": 0.95,
                    "rationale": "disjoint check",
                }
            ],
            "entity_types": [
                {"name": "A", "id": "c1"},
                {"name": "B", "id": "c2"},
            ],
        }
        shapes = _convert_axioms(data)
        assert len(shapes) == 2  # 双向各一条
        assert shapes[0]["severity"] == "block"
        assert shapes[1]["severity"] == "block"
        # 正向: A → B
        assert shapes[0]["target"]["entry_type"] == "CodeEntity"
        assert shapes[0]["target"]["ontology_ref"] == "A"
        # 反向: B → A
        assert shapes[1]["target"]["entry_type"] == "CodeEntity"
        assert shapes[1]["target"]["ontology_ref"] == "B"

    def test_equivalent_class_axiom(self):
        data = {
            "axioms": [
                {
                    "axiom_type": "EQUIVALENT_CLASS",
                    "subject": {"concept_id": "c1"},
                    "obj": {"concept_id": "c2"},
                    "confidence": 0.95,
                    "rationale": "equivalent",
                }
            ],
            "entity_types": [
                {"name": "X", "id": "c1"},
                {"name": "Y", "id": "c2"},
            ],
        }
        shapes = _convert_axioms(data)
        assert len(shapes) == 2
        assert shapes[0]["severity"] == "escalate"

    def test_axiom_medium_confidence_downgrades(self):
        """confidence 0.7-0.9 降一级。"""
        data = {
            "axioms": [
                {
                    "axiom_type": "DOMAIN",
                    "subject": {"concept_id": "c1"},
                    "obj": {"concept_id": "c2"},
                    "confidence": 0.75,
                    "rationale": "medium conf",
                }
            ],
            "entity_types": [
                {"name": "E1", "id": "c1"},
                {"name": "E2", "id": "c2"},
            ],
        }
        shapes = _convert_axioms(data)
        assert len(shapes) == 1
        # DOMAIN 基础 severity=warn，降一级 → allow
        assert shapes[0]["severity"] == "allow"

    def test_unknown_axiom_type_skipped(self):
        data = {
            "axioms": [
                {
                    "axiom_type": "UNKNOWN_TYPE",
                    "subject": {"concept_id": "c1"},
                    "obj": {"concept_id": "c2"},
                    "confidence": 0.95,
                    "rationale": "unknown",
                }
            ],
            "entity_types": [],
        }
        shapes = _convert_axioms(data)
        assert len(shapes) == 0

    def test_empty_axioms(self):
        shapes = _convert_axioms({"axioms": [], "entity_types": []})
        assert len(shapes) == 0


@pytest.mark.unit
class TestConvertProperties:
    """测试 properties(enum) → shapes 转换。"""

    def test_enum_property_creates_shape(self):
        data = {
            "properties": [
                {
                    "name": "status",
                    "id": "prop_001",
                    "domain_concept_id": "concept_order",
                    "value_type": "enum",
                    "enum_values": ["pending", "paid", "shipped"],
                    "name_cn": "订单状态",
                    "confidence": 0.9,
                    "source": "rdb",
                }
            ],
            "entity_types": [
                {"name": "订单", "id": "concept_order"},
            ],
        }
        shapes = _convert_properties(data)
        assert len(shapes) == 1
        s = shapes[0]
        assert s["id"] == "shape:enum_prop_001"
        assert s["constraint"]["field"] == "name"
        assert s["constraint"]["operator"] == "in"
        assert s["constraint"]["value"] == ["pending", "paid", "shipped"]
        assert s["target"]["entry_type"] == "ResourceEntity"
        assert s["target"]["ontology_ref"] == "订单.status"
        assert s["severity"] == "block"

    def test_non_enum_property_skipped(self):
        data = {
            "properties": [
                {
                    "name": "price",
                    "domain_concept_id": "c1",
                    "value_type": "float",
                    "enum_values": [],
                    "confidence": 0.9,
                }
            ],
            "entity_types": [
                {"name": "Product", "id": "c1"},
            ],
        }
        shapes = _convert_properties(data)
        assert len(shapes) == 0

    def test_enum_with_empty_values_skipped(self):
        data = {
            "properties": [
                {
                    "name": "category",
                    "domain_concept_id": "c1",
                    "value_type": "enum",
                    "enum_values": [],
                    "confidence": 0.9,
                }
            ],
            "entity_types": [],
        }
        shapes = _convert_properties(data)
        assert len(shapes) == 0

    def test_multiple_enum_properties(self):
        data = {
            "properties": [
                {
                    "name": "type",
                    "id": "p1",
                    "domain_concept_id": "c1",
                    "value_type": "enum",
                    "enum_values": ["a", "b"],
                    "confidence": 0.9,
                },
                {
                    "name": "category",
                    "id": "p2",
                    "domain_concept_id": "c1",
                    "value_type": "enum",
                    "enum_values": ["x", "y", "z"],
                    "confidence": 0.9,
                },
            ],
            "entity_types": [
                {"name": "Entity", "id": "c1"},
            ],
        }
        shapes = _convert_properties(data)
        assert len(shapes) == 2

    def test_missing_domain_concept_fallback(self):
        data = {
            "properties": [
                {
                    "name": "role",
                    "id": "p3",
                    "domain_concept_id": "missing_id",
                    "value_type": "enum",
                    "enum_values": ["admin", "user"],
                    "confidence": 0.9,
                }
            ],
            "entity_types": [],
        }
        shapes = _convert_properties(data)
        assert len(shapes) == 1
        # 回退到 concept_id 作为 ontology_ref
        assert shapes[0]["target"]["entry_type"] == "ResourceEntity"
        assert shapes[0]["target"]["ontology_ref"] == "missing_id.role"


@pytest.mark.unit
class TestConvertRelations:
    """测试 relations → shapes 转换。"""

    def test_single_relation_creates_shape(self):
        data = {
            "relations": [
                {
                    "name": "hasCustomer",
                    "id": "rel_001",
                    "domain_concept_id": "concept_order",
                    "range_concept_id": "concept_customer",
                    "confidence": 0.6,
                    "cardinality": "1:N",
                    "source": "rdb",
                    "source_ref": "FK:order.customer_id",
                }
            ],
            "entity_types": [
                {"name": "订单", "id": "concept_order"},
                {"name": "客户", "id": "concept_customer"},
            ],
        }
        shapes = _convert_relations(data)
        assert len(shapes) == 1
        s = shapes[0]
        assert s["id"] == "shape:rel_rel_001"
        assert "HAS_CUSTOMER" in s["path"]
        assert s["path"] == "HAS_CUSTOMER -> ResourceEntity"
        assert s["target"]["entry_type"] == "ResourceEntity"
        assert s["target"]["ontology_ref"] == "订单 --[HAS_CUSTOMER]--> 客户"

    def test_low_confidence_relation_allow_severity(self):
        data = {
            "relations": [
                {
                    "name": "hasProduct",
                    "id": "rel_002",
                    "domain_concept_id": "c1",
                    "range_concept_id": "c2",
                    "confidence": 0.6,
                    "cardinality": "1:N",
                }
            ],
            "entity_types": [
                {"name": "A", "id": "c1"},
                {"name": "B", "id": "c2"},
            ],
        }
        shapes = _convert_relations(data)
        assert len(shapes) == 1
        # confidence < 0.7 → severity=allow
        assert shapes[0]["severity"] == "allow"

    def test_high_confidence_relation_warn_severity(self):
        data = {
            "relations": [
                {
                    "name": "hasOrder",
                    "id": "rel_003",
                    "domain_concept_id": "c1",
                    "range_concept_id": "c2",
                    "confidence": 0.85,
                    "cardinality": "1:N",
                }
            ],
            "entity_types": [
                {"name": "A", "id": "c1"},
                {"name": "B", "id": "c2"},
            ],
        }
        shapes = _convert_relations(data)
        assert shapes[0]["severity"] == "warn"

    def test_multiple_relations(self):
        data = {
            "relations": [
                {
                    "name": "relOne",
                    "id": "r1",
                    "domain_concept_id": "c1",
                    "range_concept_id": "c2",
                    "confidence": 0.8,
                    "cardinality": "1:1",
                },
                {
                    "name": "relTwo",
                    "id": "r2",
                    "domain_concept_id": "c2",
                    "range_concept_id": "c3",
                    "confidence": 0.9,
                    "cardinality": "N:M",
                },
            ],
            "entity_types": [
                {"name": "A", "id": "c1"},
                {"name": "B", "id": "c2"},
                {"name": "C", "id": "c3"},
            ],
        }
        shapes = _convert_relations(data)
        assert len(shapes) == 2
        assert shapes[0]["path"] == "REL_ONE -> ResourceEntity"
        assert shapes[1]["path"] == "REL_TWO -> ResourceEntity"

    def test_missing_concept_fallback(self):
        data = {
            "relations": [
                {
                    "name": "links",
                    "domain_concept_id": "missing_domain",
                    "range_concept_id": "missing_range",
                    "confidence": 0.8,
                    "cardinality": "1:N",
                }
            ],
            "entity_types": [],
        }
        shapes = _convert_relations(data)
        assert len(shapes) == 1
        # 回退到 concept_id
        assert shapes[0]["path"] == "LINKS -> ResourceEntity"
        assert shapes[0]["target"]["entry_type"] == "ResourceEntity"
        assert shapes[0]["target"]["ontology_ref"] == "missing_domain --[LINKS]--> missing_range"

    def test_empty_relations(self):
        shapes = _convert_relations({"relations": [], "entity_types": []})
        assert shapes == []

    def test_relation_name_conversion(self):
        """验证 camelCase → UPPER_SNAKE 在 path 中的转换。"""
        data = {
            "relations": [
                {
                    "name": "orderId",
                    "domain_concept_id": "c1",
                    "range_concept_id": "c2",
                    "confidence": 0.8,
                    "cardinality": "1:N",
                }
            ],
            "entity_types": [
                {"name": "A", "id": "c1"},
                {"name": "B", "id": "c2"},
            ],
        }
        shapes = _convert_relations(data)
        assert shapes[0]["path"] == "ORDER_ID -> ResourceEntity"


# =============================================================================
# 公共 API 测试
# =============================================================================


@pytest.mark.unit
class TestLoadOntologyToShapes:
    """测试 load_ontology_to_shapes 主函数。"""

    def _make_ontology_json(self, data: dict) -> str:
        """将字典写入临时 JSON 文件并返回路径。"""
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")  # noqa: SIM115
        json.dump(data, tmp)
        tmp.close()
        return tmp.name

    def test_loads_real_ontology_file(self):
        """使用真实的 OntologyAutoGen 输出文件。"""
        # 真实文件路径
        real_path = "/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json"
        if not os.path.exists(real_path):
            pytest.skip("真实 ontology.json 不存在")
        shapes = load_ontology_to_shapes(real_path)
        assert isinstance(shapes, list)
        # 至少有 entity_types + properties + relations
        assert len(shapes) > 0

    def test_loads_real_ontology_and_roundtrips(self):
        """验证真实文件转换后的 shape 可被 ConstraintShape.from_yaml_dict 解析。"""
        real_path = "/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json"
        if not os.path.exists(real_path):
            pytest.skip("真实 ontology.json 不存在")

        shapes = load_ontology_to_shapes(real_path)
        for shape_dict in shapes:
            cs = ConstraintShape.from_yaml_dict(shape_dict)
            assert cs.id == shape_dict["id"]
            assert cs.source == "imported"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_ontology_to_shapes("/nonexistent/path.json")

    def test_include_filters(self):
        """测试各 include_* 参数。"""
        data = {
            "entity_types": [
                {"name": "E", "id": "e1", "confidence": 0.8, "is_entity_type": True},
            ],
            "properties": [
                {
                    "name": "status",
                    "id": "p1",
                    "domain_concept_id": "e1",
                    "value_type": "enum",
                    "enum_values": ["a"],
                    "confidence": 0.9,
                },
            ],
            "relations": [
                {
                    "name": "rel",
                    "domain_concept_id": "e1",
                    "range_concept_id": "e1",
                    "confidence": 0.8,
                    "cardinality": "1:1",
                },
            ],
            "axioms": [],
        }
        tmp = self._make_ontology_json(data)
        try:
            # 只包含实体
            shapes = load_ontology_to_shapes(tmp, include_properties=False, include_relations=False)
            assert len(shapes) == 1
            assert "entity" in shapes[0]["id"]

            # 只包含属性
            shapes = load_ontology_to_shapes(tmp, include_entity_types=False, include_relations=False)
            assert len(shapes) == 1
            assert "enum" in shapes[0]["id"]

            # 只包含关系
            shapes = load_ontology_to_shapes(tmp, include_entity_types=False, include_properties=False)
            assert len(shapes) == 1
            assert "rel" in shapes[0]["id"]

            # 全量
            shapes = load_ontology_to_shapes(tmp)
            assert len(shapes) == 3
        finally:
            os.unlink(tmp)

    def test_empty_ontology(self):
        """空 ontology（仅有 version 等元数据）。"""
        tmp = self._make_ontology_json({"version": "1.0", "entity_types": []})
        try:
            shapes = load_ontology_to_shapes(tmp)
            assert shapes == []
        finally:
            os.unlink(tmp)


@pytest.mark.unit
class TestWriteShapesYaml:
    """测试 write_shapes_yaml 输出函数。"""

    def test_writes_to_file(self):
        shapes = [
            {
                "id": "shape:test",
                "name": "test",
                "description": "desc",
                "kind": "operational",
                "target": {"entry_type": "Test", "operation": "READ"},
                "path": "SELF",
                "constraint": {"field": "x", "operator": "in", "value": ["v"]},
                "severity": "warn",
                "suggestion": "",
                "confidence": 1.0,
                "source": "imported",
                "rationale": "",
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            tmp = f.name

        try:
            write_shapes_yaml(shapes, tmp)
            assert os.path.exists(tmp)

            with open(tmp) as f:
                content = f.read()
            assert "version: '2.0'" in content or "version: 2.0" in content
            assert "shape:test" in content
        finally:
            os.unlink(tmp)

    def test_returns_string_when_no_path(self):
        shapes = [
            {
                "id": "shape:x",
                "name": "x",
                "description": "x",
                "kind": "operational",
                "target": {"entry_type": "X", "operation": "READ"},
                "path": "SELF",
                "constraint": {"field": "x", "operator": "in", "value": ["a"]},
                "severity": "warn",
                "suggestion": "",
                "confidence": 1.0,
                "source": "imported",
                "rationale": "",
            }
        ]
        result = write_shapes_yaml(shapes)
        assert isinstance(result, str)
        assert "shape:x" in result

    def test_roundtrip_with_constraint_shape(self):
        """验证 write → load 往返一致性。"""
        shapes_in = [
            {
                "id": "shape:rt",
                "name": "roundtrip",
                "description": "roundtrip test",
                "kind": "operational",
                "target": {"entry_type": "CodeEntity", "operation": "UPDATE"},
                "path": "SELF",
                "constraint": {"field": "sensitivity", "operator": "in", "value": ["high"]},
                "severity": "block",
                "suggestion": "be careful",
                "confidence": 0.85,
                "source": "imported",
                "rationale": "test rationale",
            }
        ]
        yaml_str = write_shapes_yaml(shapes_in)
        parsed = yaml.safe_load(yaml_str)
        assert parsed["shapes"][0]["id"] == "shape:rt"

        # 反向解析为 ConstraintShape
        cs = ConstraintShape.from_yaml_dict(parsed["shapes"][0])
        assert cs.id == "shape:rt"
        assert cs.name == "roundtrip"
        assert cs.severity == Severity.BLOCK
        assert cs.confidence == 0.85
        assert cs.source == "imported"
        assert cs.rationale == "test rationale"
        assert cs.constraint.field == "sensitivity"


# =============================================================================
# 集成测试
# =============================================================================


@pytest.mark.unit
class TestIntegrationWithRealFile:
    """使用真实 ontology.json 的集成测试。"""

    @pytest.fixture
    def real_ontology(self):
        path = "/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json"
        if not os.path.exists(path):
            pytest.skip("真实 ontology.json 不存在")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_stats_match(self, real_ontology):
        """验证转换输出的统计信息合理。"""
        shapes = load_ontology_to_shapes("/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json")
        # 应有 entity_types(64) + enum_properties(2) + relations(3)
        # entity_types: 64 个 is_entity_type=True → 64 shapes
        # properties: 2 个 enum (customer_type, status) → 2 shapes
        # relations: 3 → 3 shapes
        # axioms: 0
        # 总计: 69
        assert len(shapes) > 0
        assert len(shapes) == 69
        print(f"\n总 Shape 数: {len(shapes)}")

    def test_entity_type_shapes_have_valid_targets(self, real_ontology):
        """验证 entity_type 转换的 shape 有合法的 target。"""
        shapes = load_ontology_to_shapes(
            "/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json",
            include_properties=False,
            include_relations=False,
        )
        for s in shapes:
            cs = ConstraintShape.from_yaml_dict(s)
            assert cs.target.operation.value == "UPDATE"
            assert cs.path.is_self()

    def test_enum_property_shapes_from_real(self):
        """验证真实文件中的 enum property 转换。"""
        shapes = load_ontology_to_shapes(
            "/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json",
            include_entity_types=False,
            include_relations=False,
        )
        # 应该有 2 个 enum: customer_type + status
        enum_shapes = [s for s in shapes if "enum" in s["id"]]
        assert len(enum_shapes) == 2

        # customer_type: ["individual", "enterprise"]
        ct_shape = [s for s in enum_shapes if "customer_type" in s["name"]]
        if ct_shape:
            assert ct_shape[0]["constraint"]["value"] == ["individual", "enterprise"]

    def test_relation_shapes_from_real(self):
        """验证真实文件中的 relation 转换。"""
        shapes = load_ontology_to_shapes(
            "/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json",
            include_entity_types=False,
            include_properties=False,
        )
        # 应该有 3 个 relations
        rel_shapes = [s for s in shapes if "rel" in s["id"]]
        assert len(rel_shapes) == 3

        # 验证 path 格式
        for s in rel_shapes:
            assert "->" in s["path"]
            cs = ConstraintShape.from_yaml_dict(s)
            # path 应包含 UPPER_SNAKE 关系名
            assert len(cs.path.tokens) == 1
            assert cs.path.tokens[0].kind == "rel"

    def test_all_shapes_roundtrip(self, real_ontology):
        """验证所有 shape 都可被 ConstraintShape.from_yaml_dict 解析。"""
        shapes = load_ontology_to_shapes("/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json")
        for s in shapes:
            cs = ConstraintShape.from_yaml_dict(s)
            assert isinstance(cs.id, str)
            # 验证 severity 是合法枚举
            assert Severity(cs.severity)
            # 验证 source
            assert cs.source == "imported"

    def test_confidence_range_valid(self, real_ontology):
        """验证所有 confidence 在 [0.0, 1.0] 范围内。"""
        shapes = load_ontology_to_shapes("/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json")
        for s in shapes:
            conf = s["confidence"]
            assert 0.0 <= conf <= 1.0, f"Invalid confidence {conf} in {s['id']}"

    def test_yaml_output_no_invalid_chars(self):
        """验证 YAML 输出不包含非法字符。"""
        shapes = load_ontology_to_shapes("/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json")
        yaml_str = write_shapes_yaml(shapes)
        parsed = yaml.safe_load(yaml_str)
        assert isinstance(parsed, dict)
        assert "shapes" in parsed
        assert len(parsed["shapes"]) == len(shapes)
