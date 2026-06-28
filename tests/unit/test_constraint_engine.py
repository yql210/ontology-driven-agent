from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontoagent.domain.constraints import GuardDecision, GuardLevel, TraversalConstraint
from ontoagent.execution.constraints.engine import ConstraintEngine


class TestConstraintEngine:
    """ConstraintEngine 单元测试。"""

    @pytest.fixture
    def mock_graph_store(self):
        """创建 mock 图存储。"""
        store = MagicMock()
        return store

    @pytest.fixture
    def sample_constraint(self):
        """创建示例遍历约束。"""
        return TraversalConstraint(
            name="data_sensitivity",
            source_label="CodeEntity",
            relation_chain=["PROCESSES_DATA"],
            target_label="DataAsset",
            collect_property="sensitivity",
            value_mapping={"high": GuardLevel.BLOCK, "medium": GuardLevel.WARN, "low": GuardLevel.ALLOW},
            aggregation="max",
        )

    @pytest.fixture
    def engine(self, mock_graph_store, sample_constraint):
        """创建 ConstraintEngine 实例。"""
        return ConstraintEngine(mock_graph_store, [sample_constraint])

    def test_evaluate_with_known_constraint_returns_guard_decision(self, engine, mock_graph_store, sample_constraint):
        """已知约束应返回 GuardDecision。"""
        entity_id = "entity-1"
        target_id = "data-1"

        # Setup: entity exists
        mock_graph_store.get_node.return_value = {"id": entity_id, "name": "MyFunction"}

        # Setup: traversal returns a target with id and val
        mock_graph_store.query.return_value = [{"id": target_id, "val": "high"}]

        result = engine.evaluate(entity_id, constraint_name="data_sensitivity")

        assert isinstance(result, GuardDecision)
        assert result.level == GuardLevel.BLOCK
        assert "data-1.sensitivity=high → block" in result.reason

    def test_evaluate_with_unknown_constraint_returns_allow(self, engine, mock_graph_store):
        """未知约束应返回 ALLOW。"""
        mock_graph_store.get_node.return_value = {"id": "entity-1", "name": "Test"}

        result = engine.evaluate("entity-1", constraint_name="nonexistent")

        assert result.level == GuardLevel.ALLOW
        assert "Unknown constraint" in result.reason

    def test_evaluate_entity_not_found_returns_block(self, engine, mock_graph_store):
        """实体未找到应返回 BLOCK。"""
        mock_graph_store.get_node.return_value = None

        result = engine.evaluate("missing-id", constraint_name="data_sensitivity")

        assert result.level == GuardLevel.BLOCK
        assert "Entity not found" in result.reason

    def test_traverse_with_empty_results_returns_allow(self, engine, mock_graph_store):
        """遍历无结果应返回 ALLOW。"""
        entity_id = "entity-1"

        mock_graph_store.get_node.return_value = {"id": entity_id, "name": "MyFunction"}
        mock_graph_store.query.return_value = []  # No targets found

        result = engine.evaluate(entity_id, constraint_name="data_sensitivity")

        assert result.level == GuardLevel.ALLOW
        assert "No DataAsset found" in result.reason

    def test_value_mapping_correctly_maps_sensitivity_values(self, engine, mock_graph_store):
        """value_mapping 应正确映射 sensitivity 值。"""
        entity_id = "entity-1"

        mock_graph_store.get_node.return_value = {"id": entity_id, "name": "Processor"}
        mock_graph_store.query.return_value = [
            {"id": "data-high", "val": "high"},
            {"id": "data-medium", "val": "medium"},
            {"id": "data-low", "val": "low"},
        ]

        result = engine.evaluate(entity_id, constraint_name="data_sensitivity")

        # With max aggregation, high should win
        assert result.level == GuardLevel.BLOCK
        assert "data-high" in result.reason
        assert "data-medium" in result.reason
        assert "data-low" in result.reason

    def test_aggregation_max_prioritizes_block_over_warn(self, engine, mock_graph_store):
        """max 聚合时 BLOCK 优先于 WARN。"""
        entity_id = "entity-1"

        mock_graph_store.get_node.return_value = {"id": entity_id, "name": "Proc"}
        mock_graph_store.query.return_value = [
            {"id": "a", "val": "medium"},
            {"id": "b", "val": "low"},
        ]

        result = engine.evaluate(entity_id, constraint_name="data_sensitivity")

        assert result.level == GuardLevel.WARN

    def test_aggregation_exists_returns_warn_when_any_non_allow(self, mock_graph_store, sample_constraint):
        """exists 聚合时任一非 ALLOW 应返回 WARN。"""
        constraint = TraversalConstraint(
            name="data_sensitivity",
            source_label="CodeEntity",
            relation_chain=["PROCESSES_DATA"],
            target_label="DataAsset",
            collect_property="sensitivity",
            value_mapping={"high": GuardLevel.BLOCK, "medium": GuardLevel.WARN, "low": GuardLevel.ALLOW},
            aggregation="exists",
        )
        engine = ConstraintEngine(mock_graph_store, [constraint])
        entity_id = "entity-1"

        mock_graph_store.get_node.return_value = {"id": entity_id, "name": "Proc"}
        mock_graph_store.query.return_value = [
            {"id": "a", "val": "low"},
            {"id": "b", "val": "medium"},
        ]

        result = engine.evaluate(entity_id, constraint_name="data_sensitivity")

        assert result.level == GuardLevel.WARN

    def test_aggregation_exists_returns_allow_when_all_allow(self, mock_graph_store):
        """exists 聚合时全部 ALLOW 应返回 ALLOW。"""
        constraint = TraversalConstraint(
            name="data_sensitivity",
            source_label="CodeEntity",
            relation_chain=["PROCESSES_DATA"],
            target_label="DataAsset",
            collect_property="sensitivity",
            value_mapping={"high": GuardLevel.BLOCK, "medium": GuardLevel.WARN, "low": GuardLevel.ALLOW},
            aggregation="exists",
        )
        engine = ConstraintEngine(mock_graph_store, [constraint])
        entity_id = "entity-1"

        mock_graph_store.get_node.return_value = {"id": entity_id, "name": "Proc"}
        mock_graph_store.query.return_value = [{"id": "a", "val": "low"}]

        result = engine.evaluate(entity_id, constraint_name="data_sensitivity")

        assert result.level == GuardLevel.ALLOW

    def test_relation_chain_validation_on_load(self, mock_graph_store):
        """加载时应校验 relation_chain 合法性。"""
        # This constraint uses a valid known relation type
        constraint = TraversalConstraint(
            name="test_constraint",
            source_label="CodeEntity",
            relation_chain=["CALLS"],  # CALLS -> calls is a valid relation
            target_label="CodeEntity",
            collect_property="lines",
            value_mapping={},
            aggregation="max",
        )
        # Should not raise
        engine = ConstraintEngine(mock_graph_store, [constraint])
        assert engine is not None

    def test_relation_chain_with_unknown_type_passes_validation(self, mock_graph_store):
        """未知关系类型不应触发校验失败（向后兼容）。"""
        constraint = TraversalConstraint(
            name="future_constraint",
            source_label="CustomEntity",
            relation_chain=["FUTURE_RELATION"],
            target_label="OtherEntity",
            collect_property="prop",
            value_mapping={},
            aggregation="max",
        )
        # Unknown relation types not in RELATION_CONSTRAINTS are silently allowed
        engine = ConstraintEngine(mock_graph_store, [constraint])
        assert engine is not None

    def test_multi_hop_traversal(self, mock_graph_store):
        """多跳关系链遍历。"""
        constraint = TraversalConstraint(
            name="compliance_check",
            source_label="CodeEntity",
            relation_chain=["RUNS_AS", "GOVERNED_BY"],
            target_label="ComplianceItem",
            collect_property="level",
            value_mapping={"critical": GuardLevel.BLOCK},
            aggregation="max",
        )
        engine = ConstraintEngine(mock_graph_store, [constraint])

        entity_id = "entity-1"

        mock_graph_store.get_node.return_value = {"id": entity_id, "name": "MyService"}

        # First hop: RUNS_AS -> ServiceEntity, second hop: GOVERNED_BY -> ComplianceItem
        def query_side_effect(query, params):
            if "RUNS_AS" in query:
                return [{"id": "svc-1", "val": "critical"}]
            if "GOVERNED_BY" in query:
                return [{"id": "comp-1", "val": "critical"}]
            return []

        mock_graph_store.query.side_effect = query_side_effect

        result = engine.evaluate(entity_id, constraint_name="compliance_check")

        assert result.level == GuardLevel.BLOCK
        assert "critical" in result.reason

    def test_no_collect_property_on_targets(self, engine, mock_graph_store):
        """目标实体缺少 collect_property 时应返回 ALLOW。"""
        entity_id = "entity-1"
        target_id = "data-1"

        mock_graph_store.get_node.return_value = {"id": entity_id, "name": "Func"}
        mock_graph_store.query.return_value = [{"id": target_id, "val": None}]

        result = engine.evaluate(entity_id, constraint_name="data_sensitivity")

        assert result.level == GuardLevel.ALLOW
        assert "No sensitivity found" in result.reason


def test_aggregation_min_strategy():
    """'min' aggregation picks the least severe level."""
    store = MagicMock()
    store.get_node.return_value = {"id": "entity-1", "name": "Test"}
    store.query.return_value = [
        {"id": "a", "val": "high"},
        {"id": "b", "val": "low"},
    ]
    constraint = TraversalConstraint(
        name="test",
        source_label="CodeEntity",
        relation_chain=["PROCESSES_DATA"],
        target_label="DataAsset",
        collect_property="sensitivity",
        value_mapping={"high": GuardLevel.BLOCK, "medium": GuardLevel.WARN, "low": GuardLevel.ALLOW},
        aggregation="min",
    )
    engine = ConstraintEngine(store, [constraint])
    result = engine.evaluate("entity-1", constraint_name="test")
    assert result.level == GuardLevel.ALLOW


def test_invalid_relation_type_raises_on_init():
    """Relation type that doesn't match allowlist should raise ValueError."""
    store = MagicMock()
    constraint = TraversalConstraint(
        name="bad",
        source_label="CodeEntity",
        relation_chain=["DROP TABLE"],  # spaces not allowed
        target_label="CodeEntity",
        collect_property="lines",
        value_mapping={},
        aggregation="max",
    )
    with pytest.raises(ValueError, match="Invalid relation type"):
        ConstraintEngine(store, [constraint])


def test_invalid_collect_property_raises_on_init():
    """Collect property with invalid chars should raise ValueError."""
    store = MagicMock()
    constraint = TraversalConstraint(
        name="bad",
        source_label="CodeEntity",
        relation_chain=["CALLS"],
        target_label="CodeEntity",
        collect_property="1bad; DROP",  # starts with digit, contains semicolon
        value_mapping={},
        aggregation="max",
    )
    with pytest.raises(ValueError, match="Invalid collect_property"):
        ConstraintEngine(store, [constraint])
