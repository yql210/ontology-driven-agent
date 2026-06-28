"""Tests for ActionGuardPipeline and built-in guards."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontoagent.domain.schema import GuardDecision, GuardLevel
from ontoagent.execution.action_types import ActionConfig
from ontoagent.execution.constraints.guard_pipeline import ActionGuard, ActionGuardPipeline
from ontoagent.execution.constraints.guards import (
    EntityExistsGuard,
    EntityPropertyGuard,
    OntologyPropagationGuard,
    OntologyTraversalGuard,
)


class MockGraphStore:
    """Minimal mock for guard tests that don't need real graph queries."""

    def __init__(self, entities: dict[str, dict] | None = None) -> None:
        self._entities = entities or {}

    def get_node(self, node_id: str):
        return self._entities.get(node_id)

    def query(self, cypher: str, params: dict | None = None):
        return []


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def valid_entity() -> dict:
    return {"id": "ent-001", "name": "Cache", "lines": 320, "branches": 15}


@pytest.fixture
def small_entity() -> dict:
    return {"id": "ent-002", "name": "Small", "lines": 50, "branches": 3}


@pytest.fixture
def empty_entity() -> dict:
    return {}


@pytest.fixture
def graph_store():
    return MockGraphStore()


@pytest.fixture
def refactor_config() -> ActionConfig:
    return ActionConfig(
        name="refactor",
        intent_type="refactor",
        trigger_hint="重构",
        bind_to="code_entity",
        submission_criteria=["entity exists", "entity.lines > 100"],
        functions=["check_refactor_eligibility"],
    )


# ---------------------------------------------------------------------------
# ActionGuardPipeline tests
# ---------------------------------------------------------------------------


class TestActionGuardPipeline:
    def test_pipeline_with_no_guards_passes(self, refactor_config, valid_entity, graph_store):
        """Pipeline with no guards should always return None (pass)."""
        pipeline = ActionGuardPipeline([])
        block_reason, warnings = pipeline.check(refactor_config, valid_entity, graph_store)
        assert block_reason is None
        assert warnings == []

    def test_pipeline_with_entity_exists_blocks_on_missing(self, refactor_config, empty_entity, graph_store):
        """EntityExistsGuard blocks when entity has no id."""
        pipeline = ActionGuardPipeline([EntityExistsGuard()])
        block_reason, warnings = pipeline.check(refactor_config, empty_entity, graph_store)
        assert block_reason == "目标实体不存在"

    def test_pipeline_with_entity_exists_passes_on_valid(self, refactor_config, valid_entity, graph_store):
        """EntityExistsGuard allows when entity has an id."""
        pipeline = ActionGuardPipeline([EntityExistsGuard()])
        block_reason, warnings = pipeline.check(refactor_config, valid_entity, graph_store)
        assert block_reason is None

    def test_pipeline_entity_property_blocks_on_lines_too_low(self, refactor_config, small_entity, graph_store):
        """EntityPropertyGuard blocks when lines <= 100."""
        pipeline = ActionGuardPipeline([EntityPropertyGuard()])
        block_reason, warnings = pipeline.check(refactor_config, small_entity, graph_store)
        assert block_reason is not None
        assert "不满足条件" in block_reason

    def test_pipeline_entity_property_passes_on_large_entity(self, refactor_config, valid_entity, graph_store):
        """EntityPropertyGuard allows when lines > 100."""
        pipeline = ActionGuardPipeline([EntityPropertyGuard()])
        block_reason, warnings = pipeline.check(refactor_config, valid_entity, graph_store)
        assert block_reason is None

    def test_pipeline_stops_at_first_block(self, refactor_config, small_entity, graph_store):
        """Pipeline returns the first BLOCK decision and does not evaluate further guards."""

        class RecordingGuard(ActionGuard):
            def __init__(self, name: str, decision: GuardDecision):
                self.name = name
                self._decision = decision
                self.evaluated = False

            def evaluate(self, config, entity, gs):
                self.evaluated = True
                return self._decision

        first = RecordingGuard("first", GuardDecision(level=GuardLevel.BLOCK, reason="blocked by first"))
        second = RecordingGuard("second", GuardDecision(level=GuardLevel.ALLOW, reason="allow"))
        third = RecordingGuard("third", GuardDecision(level=GuardLevel.BLOCK, reason="blocked by third"))

        pipeline = ActionGuardPipeline([first, second, third])
        block_reason, warnings = pipeline.check(refactor_config, small_entity, graph_store)

        assert block_reason == "blocked by first"
        assert first.evaluated is True
        assert second.evaluated is False
        assert third.evaluated is False

    def test_pipeline_continues_past_allow_and_warn(self, refactor_config, valid_entity, graph_store):
        """Pipeline continues past ALLOW and WARN decisions."""

        class RecordingGuard(ActionGuard):
            def __init__(self, name: str, decision: GuardDecision):
                self.name = name
                self._decision = decision
                self.evaluated = False

            def evaluate(self, config, entity, gs):
                self.evaluated = True
                return self._decision

        first = RecordingGuard("first", GuardDecision(level=GuardLevel.ALLOW, reason="allow"))
        second = RecordingGuard("second", GuardDecision(level=GuardLevel.WARN, reason="warn"))
        third = RecordingGuard("third", GuardDecision(level=GuardLevel.ALLOW, reason="allow2"))

        pipeline = ActionGuardPipeline([first, second, third])
        block_reason, warnings = pipeline.check(refactor_config, valid_entity, graph_store)

        assert block_reason is None
        assert warnings == ["warn"]
        assert first.evaluated is True
        assert second.evaluated is True
        assert third.evaluated is True


# ---------------------------------------------------------------------------
# EntityExistsGuard tests
# ---------------------------------------------------------------------------


class TestEntityExistsGuard:
    def test_blocks_on_none_entity(self):
        guard = EntityExistsGuard()
        decision = guard.evaluate(None, None, None)
        assert decision.level == GuardLevel.BLOCK
        assert decision.reason == "目标实体不存在"

    def test_blocks_on_empty_dict(self):
        guard = EntityExistsGuard()
        decision = guard.evaluate(None, {}, None)
        assert decision.level == GuardLevel.BLOCK

    def test_blocks_on_missing_id(self):
        guard = EntityExistsGuard()
        decision = guard.evaluate(None, {"name": "test"}, None)
        assert decision.level == GuardLevel.BLOCK

    def test_allows_on_valid_entity(self, valid_entity):
        guard = EntityExistsGuard()
        decision = guard.evaluate(None, valid_entity, None)
        assert decision.level == GuardLevel.ALLOW
        assert "实体存在" in decision.reason


# ---------------------------------------------------------------------------
# EntityPropertyGuard tests
# ---------------------------------------------------------------------------


class TestEntityPropertyGuard:
    def test_passes_when_no_criteria_match(self):
        guard = EntityPropertyGuard()
        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            submission_criteria=["entity exists"],  # not a property check
        )
        decision = guard.evaluate(config, {"id": "x", "lines": 50}, None)
        assert decision.level == GuardLevel.ALLOW

    def test_blocks_on_lines_not_greater(self):
        guard = EntityPropertyGuard()
        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            submission_criteria=["entity.lines > 100"],
        )
        decision = guard.evaluate(config, {"id": "x", "lines": 50}, None)
        assert decision.level == GuardLevel.BLOCK
        assert "lines(50) <= 100" in decision.reason

    def test_blocks_on_lines_not_greater_or_equal(self):
        guard = EntityPropertyGuard()
        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            submission_criteria=["entity.lines >= 100"],
        )
        decision = guard.evaluate(config, {"id": "x", "lines": 50}, None)
        assert decision.level == GuardLevel.BLOCK
        assert "lines(50) < 100" in decision.reason

    def test_passes_when_condition_met(self):
        guard = EntityPropertyGuard()
        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            submission_criteria=["entity.lines > 100"],
        )
        decision = guard.evaluate(config, {"id": "x", "lines": 320}, None)
        assert decision.level == GuardLevel.ALLOW

    def test_ignores_malformed_expression(self):
        guard = EntityPropertyGuard()
        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            submission_criteria=["entity.bad expr"],  # not splittable into 3 parts
        )
        decision = guard.evaluate(config, {"id": "x"}, None)
        assert decision.level == GuardLevel.ALLOW


# ---------------------------------------------------------------------------
# OntologyTraversalGuard tests
# ---------------------------------------------------------------------------


class TestOntologyTraversalGuard:
    def test_passes_when_no_entity_id(self):
        engine = MagicMock()
        guard = OntologyTraversalGuard(engine)
        decision = guard.evaluate(None, {}, None)
        assert decision.level == GuardLevel.ALLOW
        engine.evaluate.assert_not_called()

    def test_passes_when_no_traversal_guard_configs(self, valid_entity):
        engine = MagicMock()
        guard = OntologyTraversalGuard(engine)
        config = ActionConfig(name="test", intent_type="test", trigger_hint="", bind_to="")
        decision = guard.evaluate(config, valid_entity, None)
        assert decision.level == GuardLevel.ALLOW
        engine.evaluate.assert_not_called()

    def test_calls_engine_for_traversal_config(self, valid_entity):
        engine = MagicMock()
        engine.evaluate.return_value = GuardDecision(level=GuardLevel.ALLOW, reason="ok")
        guard = OntologyTraversalGuard(engine)
        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            guard_configs=[{"type": "traversal", "constraint": "data_sensitivity"}],
        )
        decision = guard.evaluate(config, valid_entity, None)
        assert decision.level == GuardLevel.ALLOW
        engine.evaluate.assert_called_once_with("ent-001", "data_sensitivity")

    def test_blocks_when_engine_blocks(self, valid_entity):
        engine = MagicMock()
        engine.evaluate.return_value = GuardDecision(level=GuardLevel.BLOCK, reason="sensitive data")
        guard = OntologyTraversalGuard(engine)
        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            guard_configs=[{"type": "traversal", "constraint": "data_sensitivity"}],
        )
        decision = guard.evaluate(config, valid_entity, None)
        assert decision.level == GuardLevel.BLOCK
        assert decision.reason == "sensitive data"


# ---------------------------------------------------------------------------
# OntologyPropagationGuard tests
# ---------------------------------------------------------------------------


class TestOntologyPropagationGuard:
    def test_passes_when_no_entity_id(self):
        propagator = MagicMock()
        guard = OntologyPropagationGuard(propagator)
        decision = guard.evaluate(None, {}, None)
        assert decision.level == GuardLevel.ALLOW
        propagator.propagate.assert_not_called()

    def test_passes_when_no_propagation_guard_configs(self, valid_entity):
        propagator = MagicMock()
        guard = OntologyPropagationGuard(propagator)
        config = ActionConfig(name="test", intent_type="test", trigger_hint="", bind_to="")
        decision = guard.evaluate(config, valid_entity, None)
        assert decision.level == GuardLevel.ALLOW
        propagator.propagate.assert_not_called()

    def test_calls_propagator_for_propagation_config(self, valid_entity):
        from ontoagent.execution.constraints.propagator import PropagationResult

        propagator = MagicMock()
        propagator.propagate.return_value = PropagationResult(
            reached_nodes=[],
            aggregated_level="allow",
            path_count=0,
        )

        from ontoagent.execution.constraints.propagator import PropagationRule

        rule = PropagationRule(name="test_rule", along=["CALLS"], collect_property="risk_level")
        guard = OntologyPropagationGuard(propagator, rules={"test_rule": rule})

        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            guard_configs=[{"type": "propagation", "rule": "test_rule"}],
        )
        decision = guard.evaluate(config, valid_entity, None)
        assert decision.level == GuardLevel.ALLOW
        propagator.propagate.assert_called_once_with("ent-001", rule)

    def test_blocks_when_propagation_blocks(self, valid_entity):
        from ontoagent.execution.constraints.propagator import PropagationResult

        propagator = MagicMock()
        propagator.propagate.return_value = PropagationResult(
            reached_nodes=[{}, {}],
            aggregated_level="block",
            path_count=2,
        )

        from ontoagent.execution.constraints.propagator import PropagationRule

        rule = PropagationRule(name="risk_rule", along=["CALLS"], collect_property="risk_level")
        guard = OntologyPropagationGuard(propagator, rules={"risk_rule": rule})

        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            guard_configs=[{"type": "propagation", "rule": "risk_rule"}],
        )
        decision = guard.evaluate(config, valid_entity, None)
        assert decision.level == GuardLevel.BLOCK
        assert "risk_rule" in decision.reason
        assert "2" in decision.reason

    def test_continues_on_warn(self, valid_entity):
        from ontoagent.execution.constraints.propagator import PropagationResult

        propagator = MagicMock()
        propagator.propagate.return_value = PropagationResult(
            reached_nodes=[{}],
            aggregated_level="warn",
            path_count=1,
        )

        from ontoagent.execution.constraints.propagator import PropagationRule

        rule = PropagationRule(name="warn_rule", along=["CALLS"], collect_property="risk_level")
        guard = OntologyPropagationGuard(propagator, rules={"warn_rule": rule})

        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            guard_configs=[{"type": "propagation", "rule": "warn_rule"}],
        )
        decision = guard.evaluate(config, valid_entity, None)
        assert decision.level == GuardLevel.ALLOW

    def test_ignores_unknown_rule_name(self, valid_entity):
        propagator = MagicMock()
        guard = OntologyPropagationGuard(propagator, rules={})

        config = ActionConfig(
            name="test",
            intent_type="test",
            trigger_hint="",
            bind_to="",
            guard_configs=[{"type": "propagation", "rule": "nonexistent"}],
        )
        decision = guard.evaluate(config, valid_entity, None)
        assert decision.level == GuardLevel.ALLOW
        propagator.propagate.assert_not_called()
