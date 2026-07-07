from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontoagent.domain.shapes import (
    ConstraintExpr,
    ConstraintShape,
    Operation,
    PathExpression,
    Severity,
    ShapeKind,
    ShapeTarget,
)
from ontoagent.execution.shape_evaluator import ShapeEvaluator, ShapeResult
from ontoagent.execution.shape_registry import ShapeRegistry

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def registry() -> ShapeRegistry:
    return ShapeRegistry(valid_labels={"CodeEntity", "DataAsset"})


@pytest.fixture
def mock_graph_store() -> MagicMock:
    store = MagicMock()
    store.query.return_value = []
    return store


@pytest.fixture
def evaluator(registry: ShapeRegistry, mock_graph_store: MagicMock) -> ShapeEvaluator:
    return ShapeEvaluator(shape_registry=registry, graph_store=mock_graph_store)


def _make_shape(
    shape_id: str,
    *,
    entry_type: str = "CodeEntity",
    operation: Operation = Operation.READ,
    path: str = "SELF",
    field: str = "sensitivity",
    operator: str = "in",
    value: str | list[str] | bool | None = None,
    severity: Severity = Severity.WARN,
    unless_field: str | None = None,
    unless_value: str | list[str] | None = None,
    max_depth: int = 3,
) -> ConstraintShape:
    return ConstraintShape(
        id=shape_id,
        name=shape_id,
        description="test shape",
        kind=ShapeKind.OPERATIONAL,
        target=ShapeTarget(entry_type=entry_type, operation=operation),
        path=PathExpression.parse(path, max_depth=max_depth),
        constraint=ConstraintExpr(
            field=field,
            operator=operator,
            value=value,
            unless_field=unless_field,
            unless_value=unless_value,
        ),
        severity=severity,
    )


# =============================================================================
# SELF path
# =============================================================================


@pytest.mark.unit
class TestSelfPath:
    def test_self_in_triggered(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """SELF 路径：sensitivity in [restricted] 命中 → triggered=True。"""
        registry.register(_make_shape("s1", path="SELF", operator="in", value=["restricted"]))
        mock_graph_store.query.return_value = [{"val": "restricted"}]

        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])

        assert len(results) == 1
        result = results[0]
        assert result.triggered is True
        assert result.severity is Severity.WARN
        assert result.evidence["values"] == ["restricted"]
        assert result.evidence["field"] == "sensitivity"
        assert result.evidence["operator"] == "in"
        assert result.evidence["path"] == "SELF"
        assert result.evidence["entity_id"] == "abc"

    def test_self_in_not_triggered(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """SELF 路径：值不在期望集合中 → triggered=False。"""
        registry.register(_make_shape("s1", path="SELF", operator="in", value=["public"]))
        mock_graph_store.query.return_value = [{"val": "restricted"}]

        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])

        assert len(results) == 1
        assert results[0].triggered is False


# =============================================================================
# 关系路径（PathCompiler + graph_store 查询）
# =============================================================================


@pytest.mark.unit
class TestRelationPath:
    def test_rel_path_collects_multiple_values(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """PROCESSES_DATA -> DataAsset：多个 DataAsset 中只要有一个 restricted 即命中。"""
        registry.register(
            _make_shape(
                "s1",
                entry_type="CodeEntity",
                path="PROCESSES_DATA -> DataAsset",
                operator="in",
                value=["restricted"],
            )
        )
        mock_graph_store.query.return_value = [{"val": "restricted"}, {"val": "public"}]

        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])

        assert len(results) == 1
        assert results[0].triggered is True
        assert results[0].evidence["values"] == ["restricted", "public"]
        # 验证 graph_store 被调用了正确的 Cypher 与参数
        mock_graph_store.query.assert_called_once()
        cypher, params = mock_graph_store.query.call_args.args
        assert "PROCESSES_DATA" in cypher
        assert "DataAsset" in cypher
        assert params == {"entity_id": "abc"}

    def test_rel_path_no_matches(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """路径无匹配节点 → values 空 → triggered=False。"""
        registry.register(_make_shape("s1", path="PROCESSES_DATA -> DataAsset", operator="in", value=["restricted"]))
        mock_graph_store.query.return_value = []

        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])

        assert len(results) == 1
        assert results[0].triggered is False
        assert results[0].evidence["values"] == []


# =============================================================================
# Operators
# =============================================================================


@pytest.mark.unit
class TestOperators:
    @pytest.mark.parametrize(
        "operator,value,row_vals,expected",
        [
            ("in", ["restricted"], ["restricted"], True),
            ("in", ["public"], ["restricted"], False),
            ("in", ["public", "restricted"], ["internal"], False),
            ("not_in", ["public"], ["restricted"], True),
            ("not_in", ["restricted"], ["restricted"], False),
            ("equals", "restricted", ["restricted"], True),
            ("equals", "public", ["restricted"], False),
            ("not_equals", "public", ["restricted"], True),
            ("not_equals", "restricted", ["restricted"], False),
            ("exists", None, ["restricted"], True),
            ("exists", None, [], False),
        ],
    )
    def test_operator_eval(
        self,
        evaluator: ShapeEvaluator,
        registry: ShapeRegistry,
        mock_graph_store: MagicMock,
        operator: str,
        value: object,
        row_vals: list[str],
        expected: bool,
    ) -> None:
        registry.register(_make_shape("s1", operator=operator, value=value))
        mock_graph_store.query.return_value = [{"val": v} for v in row_vals]

        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])

        assert results[0].triggered is expected


# =============================================================================
# unless_field / unless_value
# =============================================================================


@pytest.mark.unit
class TestUnlessClause:
    def test_unless_scalar_match_skips_constraint(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """unless_field 命中（标量）→ 即使约束本会触发，也跳过。"""
        registry.register(
            _make_shape(
                "s1",
                operator="in",
                value=["restricted"],
                unless_field="approved",
                unless_value=True,
            )
        )
        mock_graph_store.query.return_value = [{"val": "restricted"}]
        entity = {"id": "abc", "labels": ["CodeEntity"], "approved": True}

        results = evaluator.evaluate(entity, [Operation.READ])

        assert results[0].triggered is False

    def test_unless_list_match_skips_constraint(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """unless_value 为列表时，命中其中任一值即豁免。"""
        registry.register(
            _make_shape(
                "s1",
                operator="in",
                value=["restricted"],
                unless_field="dept",
                unless_value=["finance", "legal"],
            )
        )
        mock_graph_store.query.return_value = [{"val": "restricted"}]
        entity = {"id": "abc", "labels": ["CodeEntity"], "dept": "legal"}

        results = evaluator.evaluate(entity, [Operation.READ])

        assert results[0].triggered is False

    def test_unless_no_match_still_triggers(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """unless_field 不命中 → 约束正常评估。"""
        registry.register(
            _make_shape(
                "s1",
                operator="in",
                value=["restricted"],
                unless_field="approved",
                unless_value=True,
            )
        )
        mock_graph_store.query.return_value = [{"val": "restricted"}]
        entity = {"id": "abc", "labels": ["CodeEntity"], "approved": False}

        results = evaluator.evaluate(entity, [Operation.READ])

        assert results[0].triggered is True


# =============================================================================
# 多 shape / 多 capability / 多 label
# =============================================================================


@pytest.mark.unit
class TestMultiplex:
    def test_multiple_shapes_same_target(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """同一 (label, operation) 注册多条 Shape → 全部评估。"""
        registry.register(_make_shape("s1", field="sensitivity", operator="in", value=["restricted"]))
        registry.register(_make_shape("s2", field="owner", operator="exists"))
        mock_graph_store.query.return_value = [{"val": "restricted"}]

        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])

        assert len(results) == 2
        ids = {r.shape.id for r in results}
        assert ids == {"s1", "s2"}

    def test_multiple_capabilities(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """传入多个 capability → 每个 operation 各评估一次匹配的 Shape。"""
        registry.register(_make_shape("s1", operation=Operation.READ))
        registry.register(_make_shape("s2", operation=Operation.UPDATE))
        mock_graph_store.query.return_value = [{"val": "restricted"}]

        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ, Operation.UPDATE])

        assert len(results) == 2

    def test_priority_order(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """get_shapes 按 priority 降序返回，evaluate 保持该顺序。"""
        low = ConstraintShape(
            id="low",
            name="low",
            description="",
            kind=ShapeKind.OPERATIONAL,
            target=ShapeTarget(entry_type="CodeEntity", operation=Operation.READ),
            path=PathExpression.parse("SELF"),
            constraint=ConstraintExpr(field="sensitivity", operator="in", value=["restricted"]),
            severity=Severity.WARN,
            priority=1,
        )
        high = ConstraintShape(
            id="high",
            name="high",
            description="",
            kind=ShapeKind.OPERATIONAL,
            target=ShapeTarget(entry_type="CodeEntity", operation=Operation.READ),
            path=PathExpression.parse("SELF"),
            constraint=ConstraintExpr(field="sensitivity", operator="in", value=["restricted"]),
            severity=Severity.BLOCK,
            priority=10,
        )
        registry.register(low)
        registry.register(high)
        mock_graph_store.query.return_value = [{"val": "restricted"}]

        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])

        assert [r.shape.id for r in results] == ["high", "low"]


# =============================================================================
# 边界情况
# =============================================================================


@pytest.mark.unit
class TestEdgeCases:
    def test_empty_capabilities_returns_empty(self, evaluator: ShapeEvaluator, registry: ShapeRegistry) -> None:
        registry.register(_make_shape("s1"))
        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [])
        assert results == []

    def test_no_shapes_registered_returns_empty(self, evaluator: ShapeEvaluator) -> None:
        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])
        assert results == []

    def test_label_not_in_registry_returns_empty(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        """实体标签不匹配任何 Shape 的 entry_type → 不评估。"""
        registry.register(_make_shape("s1", entry_type="CodeEntity"))
        results = evaluator.evaluate({"id": "abc", "labels": ["DataAsset"]}, [Operation.READ])
        assert results == []
        mock_graph_store.query.assert_not_called()

    def test_missing_labels_key_treated_as_empty(self, evaluator: ShapeEvaluator, registry: ShapeRegistry) -> None:
        registry.register(_make_shape("s1"))
        results = evaluator.evaluate({"id": "abc"}, [Operation.READ])
        assert results == []

    def test_shape_result_dataclass_fields(self) -> None:
        """ShapeResult 是 dataclass，含 shape/severity/evidence/triggered 四字段。"""
        from dataclasses import fields as dc_fields

        field_names = {f.name for f in dc_fields(ShapeResult)}
        assert field_names == {"shape", "severity", "evidence", "triggered"}

    # ─── 数值比较 operator ───────────────────────────────────────────

    def test_gt_operator_triggers(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        registry.register(_make_shape("s1", operator=">", value=100))
        mock_graph_store.query.return_value = [{"val": 150}]
        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])
        assert results[0].triggered is True

    def test_gt_operator_does_not_trigger_when_below(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        registry.register(_make_shape("s1", operator=">", value=100))
        mock_graph_store.query.return_value = [{"val": 50}]
        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])
        assert results[0].triggered is False

    def test_lt_operator_triggers(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        registry.register(_make_shape("s1", operator="<", value=100))
        mock_graph_store.query.return_value = [{"val": 50}]
        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])
        assert results[0].triggered is True

    def test_gte_operator_triggers_on_equal(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        registry.register(_make_shape("s1", operator=">=", value=100))
        mock_graph_store.query.return_value = [{"val": 100}]
        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])
        assert results[0].triggered is True

    def test_lte_operator_triggers(
        self, evaluator: ShapeEvaluator, registry: ShapeRegistry, mock_graph_store: MagicMock
    ) -> None:
        registry.register(_make_shape("s1", operator="<=", value=100))
        mock_graph_store.query.return_value = [{"val": 50}]
        results = evaluator.evaluate({"id": "abc", "labels": ["CodeEntity"]}, [Operation.READ])
        assert results[0].triggered is True


# =============================================================================
# allow_set 白名单短路与上游 CALLS 多跳 Shape
# =============================================================================


@pytest.mark.unit
class TestAllowSetShortCircuit:
    def test_allow_set_skips_all_shapes(self, mock_graph_store: MagicMock) -> None:
        """allow_set 命中 → 即使有匹配 Shape 也直接返回空列表。"""
        registry = ShapeRegistry(
            valid_labels={"CodeEntity", "DataAsset"},
            allow_set={"CodeEntity:trusted_module"},
        )
        registry.register(_make_shape("s1", operator="in", value=["restricted"]))
        mock_graph_store.query.return_value = [{"val": "restricted"}]
        evaluator_local = ShapeEvaluator(shape_registry=registry, graph_store=mock_graph_store)

        results = evaluator_local.evaluate(
            {"id": "abc", "labels": ["CodeEntity"], "name": "trusted_module"},
            [Operation.READ],
        )

        assert results == []
        mock_graph_store.query.assert_not_called()

    def test_is_allowed_method(self) -> None:
        """ShapeRegistry.is_allowed 按 'Label:Name' 格式查询。"""
        registry = ShapeRegistry(
            valid_labels={"CodeEntity"},
            allow_set={"CodeEntity:foo", "DataAsset:bar"},
        )
        assert registry.is_allowed("CodeEntity", "foo") is True
        assert registry.is_allowed("DataAsset", "bar") is True
        assert registry.is_allowed("CodeEntity", "baz") is False
        assert registry.is_allowed("Unknown", "foo") is False

    def test_allow_set_empty_by_default(self) -> None:
        """不传 allow_set 时，所有实体都不被允许。"""
        registry = ShapeRegistry(valid_labels={"CodeEntity"})
        assert registry.is_allowed("CodeEntity", "anything") is False


@pytest.mark.unit
class TestUpstreamCallChainShape:
    def test_upstream_risk_multihop_triggered(
        self, mock_graph_store: MagicMock
    ) -> None:
        """^CALLS{1,5} 反向多跳：上游 entryCategory 命中 http_api/rpc_service。"""
        registry = ShapeRegistry(valid_labels={"CodeEntity"})
        registry.register(
            _make_shape(
                "shape:upstream_call_chain_risk",
                operation=Operation.UPDATE,
                path="^CALLS{1,5} -> CodeEntity",
                field="entryCategory",
                operator="in",
                value=["http_api", "rpc_service"],
                max_depth=5,
            )
        )
        mock_graph_store.query.return_value = [{"val": "http_api"}]
        evaluator_local = ShapeEvaluator(shape_registry=registry, graph_store=mock_graph_store)

        results = evaluator_local.evaluate(
            {"id": "abc", "labels": ["CodeEntity"]},
            [Operation.UPDATE],
        )

        assert len(results) == 1
        assert results[0].triggered is True
        assert results[0].evidence["values"] == ["http_api"]

        mock_graph_store.query.assert_called_once()
        cypher, params = mock_graph_store.query.call_args.args
        assert "CALLS" in cypher
        assert "*1..5" in cypher
        assert "<-[" in cypher
        assert "CodeEntity" in cypher
        assert params == {"entity_id": "abc"}

    def test_upstream_risk_multihop_not_triggered(
        self, mock_graph_store: MagicMock
    ) -> None:
        """^CALLS{1,5} 反向多跳：上游 entryCategory 不在期望集合中 → 不命中。"""
        registry = ShapeRegistry(valid_labels={"CodeEntity"})
        registry.register(
            _make_shape(
                "shape:upstream_call_chain_risk",
                operation=Operation.UPDATE,
                path="^CALLS{1,5} -> CodeEntity",
                field="entryCategory",
                operator="in",
                value=["http_api", "rpc_service"],
                max_depth=5,
            )
        )
        mock_graph_store.query.return_value = [{"val": "internal"}]
        evaluator_local = ShapeEvaluator(shape_registry=registry, graph_store=mock_graph_store)

        results = evaluator_local.evaluate(
            {"id": "abc", "labels": ["CodeEntity"]},
            [Operation.UPDATE],
        )

        assert len(results) == 1
        assert results[0].triggered is False
