from __future__ import annotations

import os
import tempfile

import yaml

from ontoagent.domain.constraints import GuardLevel
from ontoagent.domain.ontology_constraints import ConstraintFieldDescriptor
from ontoagent.domain.schema import ONTOLOGY_CONSTRAINT_REGISTRY


class TestOntologyConstraintLoader:
    """测试 OntologyConstraintLoader 的三层加载 + 覆盖合并逻辑。"""

    def test_empty_registry_returns_empty_with_warnings(self):
        """空注册表 → 空遍历列表 + '未注册' warnings"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(registry={})
        yaml_data = {
            "traversal_constraints": {
                "test": {
                    "name": "test",
                    "source_label": "A",
                    "relation_chain": ["R"],
                    "target_label": "B",
                    "collect_property": "p",
                    "aggregation": "max",
                }
            },
            "propagation_rules": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            traversals, _rules, warnings = loader.load_all(constraints_yaml=tmp)
            assert traversals[0].value_mapping == {}
            assert any("未在" in w for w in warnings)
        finally:
            os.unlink(tmp)

    def test_auto_fill_value_mapping_from_registry(self):
        """注册表中 DataAsset.sensitivity → value_mapping 自动填充"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(registry=ONTOLOGY_CONSTRAINT_REGISTRY)
        yaml_data = {
            "traversal_constraints": {
                "data_sensitivity": {
                    "name": "data_sensitivity",
                    "source_label": "CodeEntity",
                    "relation_chain": ["PROCESSES_DATA"],
                    "target_label": "DataAsset",
                    "collect_property": "sensitivity",
                    "aggregation": "max",
                }
            },
            "propagation_rules": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp)
            assert len(traversals) == 1
            c = traversals[0]
            assert c.value_mapping["restricted"] == GuardLevel.BLOCK
            assert c.value_mapping["public"] == GuardLevel.ALLOW
            assert c.ontology_source == "DataAsset.sensitivity"
        finally:
            os.unlink(tmp)

    def test_multiple_constraints_independent(self):
        """多约束各自独立填充"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(
            registry={
                "A.x": ConstraintFieldDescriptor("x", {1: GuardLevel.BLOCK}),
                "B.y": ConstraintFieldDescriptor("y", {2: GuardLevel.WARN}),
            }
        )
        yaml_data = {
            "traversal_constraints": {
                "c1": {
                    "name": "c1",
                    "source_label": "S",
                    "relation_chain": ["R1"],
                    "target_label": "A",
                    "collect_property": "x",
                    "aggregation": "max",
                },
                "c2": {
                    "name": "c2",
                    "source_label": "S",
                    "relation_chain": ["R2"],
                    "target_label": "B",
                    "collect_property": "y",
                    "aggregation": "max",
                },
            },
            "propagation_rules": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp)
            assert len(traversals) == 2
            assert traversals[0].value_mapping == {1: GuardLevel.BLOCK}
            assert traversals[1].value_mapping == {2: GuardLevel.WARN}
        finally:
            os.unlink(tmp)

    def test_ontology_source_field_populated(self):
        """ontology_source 字段正确填充"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(
            registry={
                "Target.field": ConstraintFieldDescriptor("field", {"x": GuardLevel.BLOCK}),
            }
        )
        yaml_data = {
            "traversal_constraints": {
                "c": {
                    "name": "c",
                    "source_label": "S",
                    "relation_chain": ["R"],
                    "target_label": "Target",
                    "collect_property": "field",
                    "aggregation": "max",
                },
            },
            "propagation_rules": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp)
            assert traversals[0].ontology_source == "Target.field"
        finally:
            os.unlink(tmp)

    def test_missing_registry_warns(self):
        """缺失注册表时输出 WARN"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(registry={})
        yaml_data = {
            "traversal_constraints": {
                "c": {
                    "name": "c",
                    "source_label": "S",
                    "relation_chain": ["R"],
                    "target_label": "X",
                    "collect_property": "y",
                    "aggregation": "max",
                },
            },
            "propagation_rules": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            _traversals, _rules, warnings = loader.load_all(constraints_yaml=tmp)
            assert any("WARN" in w for w in warnings)
            assert any("X.y" in w for w in warnings)
        finally:
            os.unlink(tmp)

    def test_patch_modify_overrides_value_mapping(self):
        """patch override → modify 修改 value_mapping"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(
            registry={
                "A.x": ConstraintFieldDescriptor(
                    "x", {"high": GuardLevel.BLOCK, "low": GuardLevel.ALLOW}
                ),
            }
        )
        yaml_data = {
            "traversal_constraints": {
                "c": {
                    "name": "c",
                    "source_label": "S",
                    "relation_chain": ["R"],
                    "target_label": "A",
                    "collect_property": "x",
                    "aggregation": "max",
                },
            },
            "propagation_rules": {},
        }
        overrides = {"overrides": [{"type": "patch", "target": "c", "modify": {"high": "warn"}}]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_yaml = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(overrides, f)
            tmp_ov = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp_yaml, overrides_yaml=tmp_ov)
            assert traversals[0].value_mapping["high"] == GuardLevel.WARN
            assert traversals[0].value_mapping["low"] == GuardLevel.ALLOW
        finally:
            os.unlink(tmp_yaml)
            os.unlink(tmp_ov)

    def test_patch_remove_values(self):
        """patch override → remove_values 移除约束"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(
            registry={
                "A.x": ConstraintFieldDescriptor("x", {"a": GuardLevel.BLOCK, "b": GuardLevel.WARN}),
            }
        )
        yaml_data = {
            "traversal_constraints": {
                "c": {
                    "name": "c",
                    "source_label": "S",
                    "relation_chain": ["R"],
                    "target_label": "A",
                    "collect_property": "x",
                    "aggregation": "max",
                },
            },
            "propagation_rules": {},
        }
        overrides = {"overrides": [{"type": "patch", "target": "c", "remove_values": ["a"]}]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_yaml = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(overrides, f)
            tmp_ov = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp_yaml, overrides_yaml=tmp_ov)
            assert "a" not in traversals[0].value_mapping
            assert "b" in traversals[0].value_mapping
        finally:
            os.unlink(tmp_yaml)
            os.unlink(tmp_ov)

    def test_patch_add_values(self):
        """patch override → add_values 新增约束值"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(
            registry={
                "A.x": ConstraintFieldDescriptor("x", {"a": GuardLevel.ALLOW}),
            }
        )
        yaml_data = {
            "traversal_constraints": {
                "c": {
                    "name": "c",
                    "source_label": "S",
                    "relation_chain": ["R"],
                    "target_label": "A",
                    "collect_property": "x",
                    "aggregation": "max",
                },
            },
            "propagation_rules": {},
        }
        overrides = {"overrides": [{"type": "patch", "target": "c", "add_values": {"new_val": "block"}}]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_yaml = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(overrides, f)
            tmp_ov = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp_yaml, overrides_yaml=tmp_ov)
            assert traversals[0].value_mapping["new_val"] == GuardLevel.BLOCK
        finally:
            os.unlink(tmp_yaml)
            os.unlink(tmp_ov)

    def test_allow_all_records_warning(self):
        """allow_all → 记录 INFO warning"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(registry={})
        yaml_data = {"traversal_constraints": {}, "propagation_rules": {}}
        overrides = {
            "overrides": [
                {
                    "type": "allow_all",
                    "target_entity": "CodeEntity:validate_credit_card",
                    "reason": "脱敏",
                }
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_yaml = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(overrides, f)
            tmp_ov = f.name
        try:
            _traversals, _rules, warnings = loader.load_all(constraints_yaml=tmp_yaml, overrides_yaml=tmp_ov)
            assert any("allow_all" in w for w in warnings)
        finally:
            os.unlink(tmp_yaml)
            os.unlink(tmp_ov)

    def test_add_constraint_appends(self):
        """add_constraint → 追加到 traversals"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(
            registry={
                "B.y": ConstraintFieldDescriptor("y", {"x": GuardLevel.WARN}),
            }
        )
        yaml_data = {"traversal_constraints": {}, "propagation_rules": {}}
        overrides = {
            "overrides": [
                {
                    "type": "add_constraint",
                    "constraint": {
                        "name": "new_c",
                        "source_label": "A",
                        "relation_chain": ["R"],
                        "target_label": "B",
                        "collect_property": "y",
                        "aggregation": "max",
                    },
                }
            ]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_yaml = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(overrides, f)
            tmp_ov = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp_yaml, overrides_yaml=tmp_ov)
            assert len(traversals) == 1
        finally:
            os.unlink(tmp_yaml)
            os.unlink(tmp_ov)

    def test_empty_overrides_no_change(self):
        """空覆盖 → 约束不变"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(
            registry={
                "A.x": ConstraintFieldDescriptor("x", {"v": GuardLevel.BLOCK}),
            }
        )
        yaml_data = {
            "traversal_constraints": {
                "c": {
                    "name": "c",
                    "source_label": "S",
                    "relation_chain": ["R"],
                    "target_label": "A",
                    "collect_property": "x",
                    "aggregation": "max",
                },
            },
            "propagation_rules": {},
        }
        overrides = {"overrides": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_yaml = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(overrides, f)
            tmp_ov = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp_yaml, overrides_yaml=tmp_ov)
            assert traversals[0].value_mapping["v"] == GuardLevel.BLOCK
        finally:
            os.unlink(tmp_yaml)
            os.unlink(tmp_ov)

    def test_no_yaml_file_returns_empty(self):
        """无 YAML 文件 → 空结果"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(registry={})
        traversals, rules, warnings = loader.load_all()
        assert traversals == []
        assert rules == {}
        assert warnings == []

    def test_duplicate_constraint_names_independent(self):
        """重复约束名各自独立"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(
            registry={
                "A.x": ConstraintFieldDescriptor("x", {"v": GuardLevel.BLOCK}),
            }
        )
        yaml_data = {
            "traversal_constraints": {
                "c": {
                    "name": "c",
                    "source_label": "S",
                    "relation_chain": ["R1"],
                    "target_label": "A",
                    "collect_property": "x",
                    "aggregation": "max",
                },
                "c2": {
                    "name": "c2",
                    "source_label": "S",
                    "relation_chain": ["R2"],
                    "target_label": "A",
                    "collect_property": "x",
                    "aggregation": "max",
                },
            },
            "propagation_rules": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp)
            assert len(traversals) == 2
        finally:
            os.unlink(tmp)

    def test_propagation_value_mapping_from_registry(self):
        """propagation 规则也自动从注册表填充 value_mapping"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(
            registry={
                "CodeEntity.entry_category": ConstraintFieldDescriptor(
                    "entry_category",
                    {"http_api": GuardLevel.WARN, "scheduled": GuardLevel.ALLOW},
                ),
            }
        )
        yaml_data = {
            "traversal_constraints": {},
            "propagation_rules": {
                "upstream_risk": {
                    "name": "upstream_risk",
                    "along": ["CALLS"],
                    "direction": "backward",
                    "max_depth": 5,
                    "collect_property": "entry_category",
                    "aggregation": "exists",
                }
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            _traversals, rules, _warnings = loader.load_all(constraints_yaml=tmp)
            assert "upstream_risk" in rules
            rule = rules["upstream_risk"]
            assert rule.value_mapping["http_api"] == "warn"
            assert rule.value_mapping["scheduled"] == "allow"
        finally:
            os.unlink(tmp)

    def test_propagation_fallback_to_yaml_when_no_registry_match(self):
        """propagation — 注册表无匹配时回退到 YAML value_mapping"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(registry={})
        yaml_data = {
            "traversal_constraints": {},
            "propagation_rules": {
                "r": {
                    "name": "r",
                    "along": ["CALLS"],
                    "direction": "forward",
                    "max_depth": 3,
                    "collect_property": "unknown_field",
                    "aggregation": "max",
                    "value_mapping": {"x": "block"},
                }
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            _traversals, rules, _warnings = loader.load_all(constraints_yaml=tmp)
            assert rules["r"].value_mapping["x"] == "block"
        finally:
            os.unlink(tmp)

    def test_data_sensitivity_check_not_present(self):
        """约束列表中不含 data_sensitivity_check"""
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(registry=ONTOLOGY_CONSTRAINT_REGISTRY)
        yaml_data = {
            "traversal_constraints": {
                "data_sensitivity": {
                    "name": "data_sensitivity",
                    "source_label": "CodeEntity",
                    "relation_chain": ["PROCESSES_DATA"],
                    "target_label": "DataAsset",
                    "collect_property": "sensitivity",
                    "aggregation": "max",
                }
            },
            "propagation_rules": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp)
            names = [c.name for c in traversals]
            assert "data_sensitivity_check" not in names
        finally:
            os.unlink(tmp)

    def test_full_pipeline_loader_to_engine(self):
        """全管道：loader → ConstraintEngine 可正常构造"""
        from unittest.mock import MagicMock

        from ontoagent.execution.constraints.engine import ConstraintEngine
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        loader = OntologyConstraintLoader(registry=ONTOLOGY_CONSTRAINT_REGISTRY)
        yaml_data = {
            "traversal_constraints": {
                "data_sensitivity": {
                    "name": "data_sensitivity",
                    "source_label": "CodeEntity",
                    "relation_chain": ["PROCESSES_DATA"],
                    "target_label": "DataAsset",
                    "collect_property": "sensitivity",
                    "aggregation": "max",
                }
            },
            "propagation_rules": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp = f.name
        try:
            traversals, _rules, _warnings = loader.load_all(constraints_yaml=tmp)
            engine = ConstraintEngine(MagicMock(), traversals)
            assert engine is not None
            assert len(engine._constraints) == 1
        finally:
            os.unlink(tmp)
