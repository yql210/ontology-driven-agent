"""End-to-end integration tests for V3.4 Action pipeline.

Tests the full chain: express_intent → ActionExecutor → IntentRouter → Function chain.
Uses a MockGraphStore instead of real Neo4j.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from layerkg.action_executor import ActionExecutor
from layerkg.functions import registry as fn_registry


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
        for eid, ent in self._entities.items():
            if name in ent.get("name", ""):
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
def _ensure_functions_registered() -> None:
    """Ensure builtin functions are registered before each test."""
    fn_registry.clear_registry()
    from layerkg.functions.builtin import register_all

    register_all()
    yield
    fn_registry.clear_registry()


ENTITIES = {
    "ent-1": {
        "name": "Cache",
        "lines": 320,
        "branches": 25,
        "entityType": "function",
        "labels": ["CodeEntity"],
    },
    "ent-2": {
        "name": "SmallHelper",
        "lines": 50,
        "branches": 3,
        "entityType": "function",
        "labels": ["CodeEntity"],
    },
}


def _make_executor(entities: dict[str, dict] | None = None) -> ActionExecutor:
    store = MockGraphStore(entities or ENTITIES)
    yaml_path = Path(__file__).parent.parent.parent / "src" / "layerkg" / "ontology_actions.yaml"
    return ActionExecutor(store, yaml_path=yaml_path)


def test_e2e_refactor_success() -> None:
    """Cache(lines=320) → refactor → returns refactor eligibility."""
    executor = _make_executor()
    result = executor.execute("refactor", {"target": "Cache"})

    assert result.success is True
    assert result.action_name == "refactor"
    assert len(result.results) > 0


def test_e2e_refactor_too_small() -> None:
    """SmallHelper(lines=50) → refactor → criteria fail (lines <= 100)."""
    executor = _make_executor()
    result = executor.execute("refactor", {"target": "SmallHelper"})

    assert result.success is False
    assert "不满足条件" in (result.error or "")


def test_e2e_document_success() -> None:
    """Cache → document → returns API doc."""
    executor = _make_executor()
    result = executor.execute("document", {"target": "Cache"})

    assert result.success is True
    assert result.action_name == "document"


def test_e2e_analyze_impact() -> None:
    """Cache → analyze_impact → returns call chain."""
    executor = _make_executor()
    result = executor.execute("analyze_impact", {"target": "Cache"})

    assert result.success is True
    assert result.action_name == "analyze_impact"


def test_e2e_entity_not_found() -> None:
    """NonExistent → refactor → entity not found."""
    executor = _make_executor()
    result = executor.execute("refactor", {"target": "NonExistent"})

    assert result.success is False
    assert "未找到实体" in (result.error or "")


def test_e2e_unknown_intent() -> None:
    """Unknown intent type → returns error."""
    executor = _make_executor()
    result = executor.execute("unknown_action", {"target": "Cache"})

    assert result.success is False
    assert "未知操作类型" in (result.error or "")
