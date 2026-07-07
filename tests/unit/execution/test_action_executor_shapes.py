"""Phase 3c: ActionExecutor + ShapeEvaluator + DecisionFuser integration tests.

Covers:
    - `_check_with_shapes(entity, config)` collects capabilities from config.functions
    - `execute()` branches: shape_registry set → new path; None → fallback to guard pipeline
    - bypass_guard=True short-circuits both paths
"""

from __future__ import annotations

import textwrap
from typing import Any

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
from ontoagent.execution.action_executor import ActionExecutor
from ontoagent.execution.action_types import FunctionResult
from ontoagent.execution.functions.registry import clear_registry, register_function
from ontoagent.execution.shape_registry import ShapeRegistry

# =============================================================================
# Test doubles
# =============================================================================


class MockGraphStore:
    """Mock graph store with configurable entities and shape-query responses."""

    def __init__(
        self,
        entities: dict[str, dict[str, Any]],
        shape_query_values: list[dict[str, Any]] | None = None,
    ) -> None:
        self._entities = entities
        self._shape_query_values = shape_query_values or []
        # Track all queries for assertion
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        return self._entities.get(node_id)

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = params or {}
        self.queries.append((cypher, params))

        name = params.get("name")
        if name:
            for eid, ent in self._entities.items():
                if ent.get("name") == name or name in ent.get("name", ""):
                    return [
                        {
                            "id": eid,
                            "name": ent["name"],
                            "lines": ent.get("lines", 0),
                            "branches": ent.get("branches", 0),
                            "entityType": ent.get("entityType", ""),
                            "labels": ent.get("labels", []),
                        }
                    ]
            return []

        # Shape-evaluator query (has $entity_id and RETURN ... AS val)
        if "entity_id" in params:
            return list(self._shape_query_values)

        return []


def _make_block_shape(
    shape_id: str = "shape:block_test",
    *,
    entry_type: str = "CodeEntity",
    operation: Operation = Operation.UPDATE,
    field: str = "sensitivity",
    value: list[str] | None = None,
    severity: Severity = Severity.BLOCK,
) -> ConstraintShape:
    return ConstraintShape(
        id=shape_id,
        name=shape_id,
        description="test",
        kind=ShapeKind.OPERATIONAL,
        target=ShapeTarget(entry_type=entry_type, operation=operation),
        path=PathExpression.parse("SELF"),
        constraint=ConstraintExpr(field=field, operator="in", value=value or ["restricted"]),
        severity=severity,
        priority=10,
        suggestion="BLOCK 触发",
    )


def _make_warn_shape(
    shape_id: str = "shape:warn_test",
    *,
    entry_type: str = "CodeEntity",
    operation: Operation = Operation.UPDATE,
) -> ConstraintShape:
    return ConstraintShape(
        id=shape_id,
        name=shape_id,
        description="test",
        kind=ShapeKind.OPERATIONAL,
        target=ShapeTarget(entry_type=entry_type, operation=operation),
        path=PathExpression.parse("SELF"),
        constraint=ConstraintExpr(field="entryCategory", operator="in", value=["http_api"]),
        severity=Severity.WARN,
        priority=5,
        suggestion="WARN 提示",
    )


# =============================================================================
# Shared fixtures
# =============================================================================


@pytest.fixture
def yaml_with_refactor(tmp_path):
    """Minimal ontology_actions.yaml with refactor action that triggers a CodeEntity:READ."""
    content = textwrap.dedent("""\
        actions:
          refactor:
            intent_type: refactor
            trigger_hint: "重构"
            bind_to: code_entity
            submission_criteria:
              - "entity exists"
            functions:
              - check_refactor_eligibility
            requires_approval: false
        """)
    yaml_file = tmp_path / "actions.yaml"
    yaml_file.write_text(content, encoding="utf-8")
    return yaml_file


@pytest.fixture(autouse=True)
def _register_test_functions():
    """Register builtin functions for each test; clean up after."""
    clear_registry()
    from ontoagent.execution.functions.builtin import register_all

    register_all()

    yield
    clear_registry()


@pytest.fixture
def block_registry() -> ShapeRegistry:
    """Registry with one BLOCK shape on CodeEntity:READ."""
    registry = ShapeRegistry(valid_labels={"CodeEntity"})
    registry.register(_make_block_shape(operation=Operation.READ))
    return registry


@pytest.fixture
def warn_registry() -> ShapeRegistry:
    """Registry with one WARN shape on CodeEntity:READ."""
    registry = ShapeRegistry(valid_labels={"CodeEntity"})
    registry.register(_make_warn_shape(operation=Operation.READ))
    return registry


@pytest.fixture
def escalate_registry() -> ShapeRegistry:
    """Registry with one ESCALATE shape on CodeEntity:READ."""
    shape = ConstraintShape(
        id="shape:escalate_test",
        name="escalate_test",
        description="test escalate",
        kind=ShapeKind.OPERATIONAL,
        target=ShapeTarget(entry_type="CodeEntity", operation=Operation.READ),
        path=PathExpression.parse("SELF"),
        constraint=ConstraintExpr(field="sensitivity", operator="in", value=["restricted"]),
        severity=Severity.ESCALATE,
        priority=10,
        suggestion="需人工审批",
    )
    registry = ShapeRegistry(valid_labels={"CodeEntity"})
    registry.register(shape)
    return registry


# =============================================================================
# Tests: _check_with_shapes
# =============================================================================


@pytest.mark.unit
class TestCheckWithShapes:
    def test_block_shape_returns_block_reason(self, yaml_with_refactor, block_registry):
        """BLOCK shape → (block_reason, [])."""
        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 200,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            },
            shape_query_values=[{"val": "restricted"}],
        )
        executor = ActionExecutor(store, yaml_path=yaml_with_refactor, shape_registry=block_registry)
        config = executor.intent_map["refactor"]
        entity = store.query("", {"name": "Foo"})[0]

        block_reason, warnings = executor._check_with_shapes(entity, config)

        assert block_reason is not None
        assert "BLOCK" in block_reason or "Shape" in block_reason or block_reason == "BLOCK 触发"
        assert warnings == []

    def test_warn_shape_returns_warning(self, yaml_with_refactor, warn_registry):
        """WARN shape → (None, [warning])."""
        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 200,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            },
            shape_query_values=[{"val": "http_api"}],
        )
        executor = ActionExecutor(store, yaml_path=yaml_with_refactor, shape_registry=warn_registry)
        config = executor.intent_map["refactor"]
        entity = store.query("", {"name": "Foo"})[0]

        block_reason, warnings = executor._check_with_shapes(entity, config)

        assert block_reason is None
        assert len(warnings) == 1

    def test_escalate_returns_warning_not_block(self, yaml_with_refactor, escalate_registry):
        """ESCALATE shape → (None, warnings) — 不阻断，标记 approval_required。"""
        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 200,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            },
            shape_query_values=[{"val": "restricted"}],
        )
        executor = ActionExecutor(store, yaml_path=yaml_with_refactor, shape_registry=escalate_registry)
        config = executor.intent_map["refactor"]
        entity = store.query("", {"name": "Foo"})[0]

        block_reason, warnings = executor._check_with_shapes(entity, config)

        assert block_reason is None, f"ESCALATE should not block, got: {block_reason}"
        assert len(warnings) >= 2, f"Expected at least 2 warnings, got: {warnings}"
        assert any("approval_required" in w for w in warnings), f"Missing approval_required marker in: {warnings}"

    def test_no_matching_shape_returns_pass(self, yaml_with_refactor):
        """No shapes in registry → (None, [])."""
        empty_registry = ShapeRegistry(valid_labels={"CodeEntity"})
        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 50,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            }
        )
        executor = ActionExecutor(store, yaml_path=yaml_with_refactor, shape_registry=empty_registry)
        config = executor.intent_map["refactor"]
        entity = store.query("", {"name": "Foo"})[0]

        block_reason, warnings = executor._check_with_shapes(entity, config)

        assert block_reason is None
        assert warnings == []

    def test_capabilities_collected_from_functions(self, yaml_with_refactor, block_registry):
        """_check_with_shapes collects capabilities from config.functions."""
        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 200,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            },
            shape_query_values=[{"val": "restricted"}],
        )
        executor = ActionExecutor(store, yaml_path=yaml_with_refactor, shape_registry=block_registry)
        config = executor.intent_map["refactor"]
        # config.functions should be ["check_refactor_eligibility"]
        # which has capabilities ["CodeEntity:READ"] per functions.yaml
        assert "check_refactor_eligibility" in config.functions

        entity = store.query("", {"name": "Foo"})[0]
        executor._check_with_shapes(entity, config)

        # ShapeEvaluator should have issued a graph query (shape path compiled)
        assert any("entity_id" in params for _, params in store.queries)

    def test_no_capabilities_returns_pass(self, yaml_with_refactor, block_registry):
        """If a function has no capabilities, no shapes evaluated → pass."""

        # Register a function with no capabilities
        @register_function("noop_fn")
        def _noop(ctx) -> FunctionResult:
            return FunctionResult(success=True)

        content = textwrap.dedent("""\
            actions:
              noop_action:
                intent_type: noop_action
                trigger_hint: "noop"
                bind_to: code_entity
                submission_criteria:
                  - "entity exists"
                functions:
                  - noop_fn
                requires_approval: false
            """)
        yaml_file = yaml_with_refactor.parent / "noop.yaml"
        yaml_file.write_text(content, encoding="utf-8")

        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 200,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            }
        )
        executor = ActionExecutor(store, yaml_path=yaml_file, shape_registry=block_registry)
        config = executor.intent_map["noop_action"]
        entity = store.query("", {"name": "Foo"})[0]

        block_reason, warnings = executor._check_with_shapes(entity, config)

        assert block_reason is None
        assert warnings == []


# =============================================================================
# Tests: execute() branches between new and legacy path
# =============================================================================


@pytest.mark.unit
class TestExecuteShapeBranch:
    def test_shape_registry_blocks_execution(self, yaml_with_refactor, block_registry):
        """With shape_registry set and BLOCK shape triggered, execute returns failure."""
        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 200,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            },
            shape_query_values=[{"val": "restricted"}],
        )
        executor = ActionExecutor(store, yaml_path=yaml_with_refactor, shape_registry=block_registry)

        result = executor.execute("refactor", {"target": "Foo"})

        assert result.success is False
        assert result.error is not None

    def test_shape_registry_warns_passes(self, yaml_with_refactor, warn_registry):
        """With shape_registry set and WARN-only shape, execute succeeds with warning."""
        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 200,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            },
            shape_query_values=[{"val": "http_api"}],
        )
        executor = ActionExecutor(store, yaml_path=yaml_with_refactor, shape_registry=warn_registry)

        result = executor.execute("refactor", {"target": "Foo"})

        assert result.success is True

    def test_no_shape_registry_falls_back_to_guard_pipeline(self, yaml_with_refactor):
        """Without shape_registry, legacy guard pipeline runs (still passes for plain criteria)."""
        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 200,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            }
        )
        # No shape_registry → legacy path
        executor = ActionExecutor(store, yaml_path=yaml_with_refactor)

        result = executor.execute("refactor", {"target": "Foo"})

        assert result.success is True

    def test_bypass_guard_skips_shape_check(self, yaml_with_refactor, block_registry):
        """bypass_guard=True must skip both new and legacy constraint checks."""
        store = MockGraphStore(
            {
                "ent-1": {
                    "name": "Foo",
                    "lines": 200,
                    "entityType": "function",
                    "labels": ["CodeEntity"],
                }
            },
            shape_query_values=[{"val": "restricted"}],
        )
        executor = ActionExecutor(store, yaml_path=yaml_with_refactor, shape_registry=block_registry)

        result = executor.execute("refactor", {"target": "Foo"}, bypass_guard=True)

        assert result.success is True
