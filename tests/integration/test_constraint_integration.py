"""Integration test for constraint framework wired to demo-service.

Tests real Neo4j graph queries through ActionExecutor with Shape path (V5).
Requires demo-service built in Neo4j first."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ontoagent.execution.action_executor import ActionExecutor
from ontoagent.execution.shape_registry import ShapeRegistry
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
def shape_registry() -> ShapeRegistry:
    """Create ShapeRegistry loaded from shapes.yaml."""
    from ontoagent.domain.schema import ONTOLOGY_ENTITY_LABELS

    shapes_yaml = Path(__file__).parent.parent.parent / "src" / "ontoagent" / "pipeline" / "shapes.yaml"
    registry = ShapeRegistry(valid_labels=set(ONTOLOGY_ENTITY_LABELS))
    registry.load_from_yaml(shapes_yaml)
    return registry


@pytest.fixture(scope="module")
def executor(
    graph_store: Neo4jGraphStore,
    shape_registry: ShapeRegistry,
) -> ActionExecutor:
    """Create ActionExecutor wired with shape_registry and real Neo4j."""
    yaml_path = Path(__file__).parent.parent.parent / "src" / "ontoagent" / "pipeline" / "ontology_actions.yaml"
    return ActionExecutor(
        graph_store=graph_store,
        yaml_path=yaml_path,
        shape_registry=shape_registry,
    )


# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_refactor_validate_credit_card_blocked(
    executor: ActionExecutor,
    graph_store: Neo4jGraphStore,
) -> None:
    """refactor on validate_credit_card should BLOCK due to restricted data sensitivity.

    validate_credit_card has PROCESSES_DATA → 信用卡信息 (sensitivity=restricted).
    Shape shape:sensitive_data maps restricted → BLOCK.
    """
    rows = graph_store.query(
        "MATCH (n {name: $name}) RETURN n.id AS id LIMIT 1",
        {"name": "validate_credit_card"},
    )
    if not rows:
        pytest.skip("validate_credit_card node not found in Neo4j — demo-service not built")
    result = executor.execute("refactor", {"target": "validate_credit_card"})
    assert result.success is False, f"Expected BLOCK but got success: {result}"
    assert result.action_name == "refactor"
    # Error should mention sensitivity or block reason
    assert "block" in (result.error or "").lower() or "sensitivity" in (result.error or "").lower(), (
        f"Expected block error, got: {result.error}"
    )
    # Positive assertion: shape_registry was wired
    assert hasattr(executor, "_shape_registry"), "shape_registry not wired to executor"
    assert executor._shape_registry is not None, "shape_registry is None"


@pytest.mark.integration
def test_refactor_daily_reconciliation_allowed(executor: ActionExecutor) -> None:
    """refactor on daily_reconciliation should ALLOW (no PROCESSES_DATA relationship).

    daily_reconciliation does not process any data asset, so Shape
    shape:sensitive_data is not triggered. The function may fail for other
    reasons but the constraint must not be the blocker.
    """
    result = executor.execute("refactor", {"target": "daily_reconciliation"})
    # Shape path must not block — failure must NOT be from constraint shapes
    if not result.success:
        err = result.error or ""
        assert "sensitivity" not in err.lower(), (
            f"Expected constraint to allow, but got sensitivity block: {result.error}"
        )
        assert "Shape" not in err, (
            f"Expected constraint to allow, but got Shape block: {result.error}"
        )
    assert result.action_name == "refactor"
    # Positive assertion: shape_registry is wired
    assert executor._shape_registry is not None


@pytest.mark.integration
def test_compliance_check_validate_credit_card_allowed(executor: ActionExecutor) -> None:
    """compliance_check on validate_credit_card should ALLOW.

    compliance_check reads operation, shape:sensitive_data targets UPDATE.
    No matching Shape triggers.
    """
    result = executor.execute("compliance_check", {"target": "validate_credit_card"})
    # Shape path must not block
    if not result.success:
        err = result.error or ""
        assert "sensitivity" not in err.lower(), (
            f"Expected constraint to allow, but got sensitivity block: {result.error}"
        )
    assert result.action_name == "compliance_check"
    assert executor._shape_registry is not None


@pytest.mark.integration
def test_executor_shape_registry_is_wired(executor: ActionExecutor) -> None:
    """Verify that the executor has a fully wired shape_registry."""
    registry = executor._shape_registry
    assert registry is not None, "Shape registry must be wired"
    assert len(registry) >= 4, f"Expected at least 4 shapes, got {len(registry)}"
