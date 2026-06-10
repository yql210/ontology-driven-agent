"""End-to-end integration tests for V3.4 Phase D — Connectors + general Functions.

Tests the full chain: express_intent → ActionExecutor → Function via registry.
Uses MockGraphStore and MockConnector — no external services required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from layerkg.action_executor import ActionExecutor
from layerkg.action_types import ActionContext
from layerkg.connectors.base import ConnectorRegistry
from layerkg.connectors.mock_connector import MockConnector
from layerkg.functions import registry as fn_registry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockGraphStore:
    """Minimal mock graph store for E2E testing."""

    def __init__(self, entities: dict[str, dict]) -> None:
        self._entities = entities

    def get_node(self, node_id: str) -> dict | None:
        return self._entities.get(node_id)

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        name = (params or {}).get("name", "")
        for eid, ent in self._entities.items():
            if ent.get("name") == name:
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


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Clear and re-register all functions before each test."""
    fn_registry.clear_registry()
    from layerkg.functions.builtin import register_all as register_builtins
    from layerkg.functions.general import register_all as register_general

    register_builtins()
    register_general()
    yield
    fn_registry.clear_registry()


ENTITIES = {
    "ent-1": {
        "name": "UserService",
        "lines": 250,
        "branches": 12,
        "entityType": "function",
        "labels": ["CodeEntity"],
    },
}


def _make_executor(entities: dict[str, dict] | None = None) -> ActionExecutor:
    store = MockGraphStore(entities or ENTITIES)
    yaml_path = Path(__file__).parent.parent.parent / "src" / "layerkg" / "ontology_actions.yaml"
    return ActionExecutor(store, yaml_path=yaml_path)


# ---------------------------------------------------------------------------
# test_e2e_general_query_entity
# ---------------------------------------------------------------------------


def test_e2e_general_query_entity() -> None:
    """query_entity: retrieve entity data from match_data via registry."""
    fn = fn_registry.get_function("query_entity")
    assert fn is not None

    store = MockGraphStore(ENTITIES)
    ctx = ActionContext(
        graph_store=store,
        match_data={"target": "UserService", "entity": {"name": "UserService", "lines": 250}},
    )
    result = fn(ctx)
    assert result.success is True
    assert result.data["name"] == "UserService"


# ---------------------------------------------------------------------------
# test_e2e_general_create_entity
# ---------------------------------------------------------------------------


def test_e2e_general_create_entity() -> None:
    """create_entity: valid label passes whitelist validation."""
    fn = fn_registry.get_function("create_entity")
    assert fn is not None

    store = MockGraphStore({})
    ctx = ActionContext(graph_store=store, match_data={})
    result = fn(ctx, label="CodeEntity", properties={"name": "MyFunc", "entity_type": "function"})
    assert result.success is True
    assert result.data["created"] == "CodeEntity"


def test_e2e_general_create_entity_invalid_label() -> None:
    """create_entity: invalid label rejected by whitelist."""
    fn = fn_registry.get_function("create_entity")
    assert fn is not None

    store = MockGraphStore({})
    ctx = ActionContext(graph_store=store, match_data={})
    result = fn(ctx, label="FakeEntity", properties={})
    assert result.success is False
    assert "Invalid label" in (result.error or "")


# ---------------------------------------------------------------------------
# test_e2e_general_check_condition
# ---------------------------------------------------------------------------


def test_e2e_general_check_condition() -> None:
    """check_condition: evaluate field comparison."""
    fn = fn_registry.get_function("check_condition")
    assert fn is not None

    store = MockGraphStore(ENTITIES)
    ctx = ActionContext(
        graph_store=store,
        match_data={"entity": {"lines": 250, "entityType": "function"}},
    )
    result = fn(ctx, field="lines", operator=">", value=100)
    assert result.success is True
    assert result.data["condition_met"] is True


# ---------------------------------------------------------------------------
# test_e2e_connector_registry
# ---------------------------------------------------------------------------


def test_e2e_connector_registry() -> None:
    """ConnectorRegistry: register, retrieve, and list connectors."""
    reg = ConnectorRegistry()
    mock = MockConnector()
    mock.add_mock_data([{"id": "log-1", "level": "ERROR", "message": "timeout"}])

    reg.register("mock", mock)

    assert reg.get("mock") is mock
    assert "mock" in reg.list_connectors()

    connector = reg.get("mock")
    assert connector is not None
    assert connector.health_check() is True
    assert len(connector.fetch({})) == 1


# ---------------------------------------------------------------------------
# test_e2e_builtin_via_action_executor
# ---------------------------------------------------------------------------


def test_e2e_builtin_via_action_executor() -> None:
    """Builtin functions still work through ActionExecutor pipeline."""
    executor = _make_executor()
    result = executor.execute("refactor", {"target": "UserService"})

    assert result.success is True
    assert result.action_name == "refactor"
    assert len(result.results) > 0
    assert result.results[0].success is True
    assert "total_lines" in result.results[0].data
