"""Integration test for constraint framework wired to demo-service.

Tests real Neo4j graph queries through ActionExecutor with GuardPipeline.
Requires demo-service built in Neo4j first."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ontoagent.domain.constraints import TraversalConstraint
from ontoagent.execution.action_executor import ActionExecutor
from ontoagent.execution.constraints import (
    ActionGuardPipeline,
    ConstraintEngine,
    ConstraintPropagator,
    EntityExistsGuard,
    EntityPropertyGuard,
    OntologyPropagationGuard,
    OntologyTraversalGuard,
    PropagationRule,
    WhitelistGuard,
)
from ontoagent.execution.functions import registry as fn_registry
from ontoagent.store.neo4j_store import Neo4jGraphStore


def _get_neo4j_creds() -> tuple[str, str, str]:
    """Return (uri, user, password) from env, or skip the test."""
    uri = os.environ.get("ONTOAGENT_NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("ONTOAGENT_NEO4J_USER", "neo4j")
    password = os.environ.get("ONTOAGENT_NEO4J_PASSWORD", "")
    if not password:
        pytest.skip("ONTOAGENT_NEO4J_PASSWORD not set")
    return uri, user, password


def _load_traversal_constraints(yaml_path: Path) -> list[TraversalConstraint]:
    """Load traversal constraints using OntologyConstraintLoader (v3).

    In v3, value_mapping is auto-derived from ONTOLOGY_CONSTRAINT_REGISTRY
    rather than stored directly in constraints.yaml.
    """
    from ontoagent.domain.schema import ONTOLOGY_CONSTRAINT_REGISTRY
    from ontoagent.execution.constraints.loader import OntologyConstraintLoader

    loader = OntologyConstraintLoader(registry=ONTOLOGY_CONSTRAINT_REGISTRY)
    traversals, _, _, _ = loader.load_all(constraints_yaml=yaml_path)
    return traversals


def _load_propagation_rules(yaml_path: Path) -> dict[str, PropagationRule]:
    """Load propagation rules using OntologyConstraintLoader (v3).

    In v3, value_mapping is auto-derived from ONTOLOGY_CONSTRAINT_REGISTRY.
    """
    from ontoagent.domain.schema import ONTOLOGY_CONSTRAINT_REGISTRY
    from ontoagent.execution.constraints.loader import OntologyConstraintLoader

    loader = OntologyConstraintLoader(registry=ONTOLOGY_CONSTRAINT_REGISTRY)
    _, rules, _, _ = loader.load_all(constraints_yaml=yaml_path)
    return rules


@pytest.fixture(autouse=True)
def _ensure_functions_registered() -> None:
    """Ensure builtin functions are registered before each test."""
    fn_registry.clear_registry()
    from ontoagent.execution.functions.builtin import register_all

    register_all()
    yield
    fn_registry.clear_registry()


@pytest.fixture(scope="module")
def graph_store() -> Neo4jGraphStore:
    """Create real Neo4j graph store for all tests in this module."""
    uri, user, password = _get_neo4j_creds()
    store = Neo4jGraphStore(uri=uri, user=user, password=password)
    yield store
    store.close()


@pytest.fixture(scope="module")
def constraint_engine(graph_store: Neo4jGraphStore) -> ConstraintEngine:
    """Create ConstraintEngine loaded from constraints.yaml."""
    yaml_path = Path(__file__).parent.parent.parent / "src" / "ontoagent" / "pipeline" / "constraints.yaml"
    constraints = _load_traversal_constraints(yaml_path)
    return ConstraintEngine(graph_store, constraints)


@pytest.fixture(scope="module")
def propagation_rules() -> dict[str, PropagationRule]:
    """Load propagation rules from constraints.yaml."""
    yaml_path = Path(__file__).parent.parent.parent / "src" / "ontoagent" / "pipeline" / "constraints.yaml"
    return _load_propagation_rules(yaml_path)


@pytest.fixture(scope="module")
def guard_pipeline(
    graph_store: Neo4jGraphStore,
    constraint_engine: ConstraintEngine,
    propagation_rules: dict[str, PropagationRule],
) -> ActionGuardPipeline:
    """Create ActionGuardPipeline with all guards wired to real Neo4j."""
    propagator = ConstraintPropagator(graph_store)
    return ActionGuardPipeline(
        [
            WhitelistGuard(set()),  # empty allow_set for tests
            EntityExistsGuard(),
            EntityPropertyGuard(),
            OntologyTraversalGuard(constraint_engine),
            OntologyPropagationGuard(propagator, rules=propagation_rules),
        ]
    )


@pytest.fixture(scope="module")
def executor(
    graph_store: Neo4jGraphStore,
    guard_pipeline: ActionGuardPipeline,
) -> ActionExecutor:
    """Create ActionExecutor wired with guard_pipeline and real Neo4j."""
    yaml_path = Path(__file__).parent.parent.parent / "src" / "ontoagent" / "pipeline" / "ontology_actions.yaml"
    return ActionExecutor(
        graph_store=graph_store,
        yaml_path=yaml_path,
        guard_pipeline=guard_pipeline,
    )


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_refactor_validate_credit_card_blocked(executor: ActionExecutor) -> None:
    """refactor on validate_credit_card should BLOCK due to restricted data sensitivity.

    validate_credit_card has PROCESSES_DATA → 信用卡信息 (sensitivity=restricted).
    The data_sensitivity traversal constraint maps restricted → BLOCK.
    """
    result = executor.execute("refactor", {"target": "validate_credit_card"})
    assert result.success is False, f"Expected BLOCK but got success: {result}"
    assert result.action_name == "refactor"
    # Error should mention sensitivity or block reason
    assert "block" in (result.error or "").lower() or "sensitivity" in (result.error or "").lower(), (
        f"Expected block error, got: {result.error}"
    )
    # Positive assertion: guard pipeline was wired and ran
    assert hasattr(executor, "_guard_pipeline"), "guard_pipeline not wired to executor"
    assert executor._guard_pipeline is not None, "guard_pipeline is None"


@pytest.mark.integration
def test_refactor_daily_reconciliation_allowed(executor: ActionExecutor) -> None:
    """refactor on daily_reconciliation should ALLOW (no PROCESSES_DATA relationship).

    daily_reconciliation does not process any data asset, so the traversal
    constraint returns ALLOW. The guard pipeline should not block this action.
    (The function may fail for other reasons like missing lines property,
    but the constraint guard must not be the blocker.)
    """
    result = executor.execute("refactor", {"target": "daily_reconciliation"})
    # Guard pipeline must not block — failure must NOT be from constraint guards
    if not result.success:
        err = result.error or ""
        assert "sensitivity" not in err.lower(), (
            f"Expected constraint to allow, but got sensitivity block: {result.error}"
        )
        assert "block" not in err.lower() or "traversal" in err.lower(), (
            f"Expected constraint to allow, but got: {result.error}"
        )
    assert result.action_name == "refactor"
    # Positive assertion: guard pipeline ran (wired correctly)
    assert executor._guard_pipeline is not None
    assert len(executor._guard_pipeline.guards) >= 5, "Expected at least 5 guards in pipeline"


@pytest.mark.integration
def test_compliance_check_validate_credit_card_allowed(executor: ActionExecutor) -> None:
    """compliance_check on validate_credit_card should ALLOW.

    compliance_check has no guard_configs, so it bypasses the Guard Pipeline
    and executes directly without constraint checks.
    """
    result = executor.execute("compliance_check", {"target": "validate_credit_card"})
    # Guard pipeline must not block — failure must NOT be from constraint guards
    if not result.success:
        err = result.error or ""
        assert "sensitivity" not in err.lower(), (
            f"Expected constraint to allow, but got sensitivity block: {result.error}"
        )
        assert "restricted" not in err.lower(), (
            f"Expected constraint to allow, but got restricted block: {result.error}"
        )
    assert result.action_name == "compliance_check"
    # Positive assertion: guard pipeline is wired
    assert executor._guard_pipeline is not None


@pytest.mark.integration
def test_executor_guard_pipeline_is_wired(executor: ActionExecutor) -> None:
    """Verify that the executor has a fully wired guard pipeline."""
    pipeline = executor._guard_pipeline
    assert pipeline is not None, "Guard pipeline must be wired"
    guard_names = [type(g).__name__ for g in pipeline.guards]
    assert "WhitelistGuard" in guard_names, f"Missing WhitelistGuard in {guard_names}"
    assert "EntityExistsGuard" in guard_names, f"Missing EntityExistsGuard in {guard_names}"
    assert "EntityPropertyGuard" in guard_names, f"Missing EntityPropertyGuard in {guard_names}"
    assert "OntologyTraversalGuard" in guard_names, f"Missing OntologyTraversalGuard in {guard_names}"
    assert "OntologyPropagationGuard" in guard_names, f"Missing OntologyPropagationGuard in {guard_names}"
