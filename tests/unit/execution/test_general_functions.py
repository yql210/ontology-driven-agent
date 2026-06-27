"""Tests for 6 general-purpose functions in functions/general.py."""

from __future__ import annotations

from layerkg.execution.action_types import ActionContext
from layerkg.execution.functions.registry import clear_registry, get_function


class MockGraphStore:
    """Mock graph store for testing."""

    def __init__(self, entities: dict | None = None, query_results: list | None = None):
        self._entities = entities or {}
        self._query_results = query_results or []

    def get_node(self, node_id: str) -> dict | None:
        return self._entities.get(node_id)

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        return self._query_results


def _register_general():
    """Import + register general functions (works after clear_registry)."""
    from layerkg.execution.functions.general import register_all

    register_all()


# ── query_entity ──────────────────────────────────────────────


def test_query_entity_success():
    clear_registry()
    _register_general()
    fn = get_function("query_entity")
    assert fn is not None

    entity_data = {"name": "my_func", "entity_type": "function", "lines": 42}
    ctx = ActionContext(graph_store=MockGraphStore(), match_data={"target": "my_func", "entity": entity_data})
    result = fn(ctx)
    assert result.success is True
    assert result.data == entity_data


def test_query_entity_no_target():
    clear_registry()
    _register_general()
    fn = get_function("query_entity")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx)
    assert result.success is False
    assert "target" in result.error.lower()


def test_query_entity_not_in_context():
    clear_registry()
    _register_general()
    fn = get_function("query_entity")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={"target": "missing_func"})
    result = fn(ctx)
    assert result.success is False
    assert "not found" in result.error.lower()


# ── update_entity ─────────────────────────────────────────────


def test_update_entity_success():
    clear_registry()
    _register_general()
    fn = get_function("update_entity")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={"entity_id": "e-001"})
    result = fn(ctx, properties={"name": "renamed", "status": "active"})
    assert result.success is True
    assert result.data["updated"] == "e-001"
    assert "name" in result.data["properties"]
    assert "status" in result.data["properties"]


def test_update_entity_no_entity_id():
    clear_registry()
    _register_general()
    fn = get_function("update_entity")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx, properties={"name": "x"})
    assert result.success is False
    assert "entity_id" in result.error.lower()


def test_update_entity_no_properties():
    clear_registry()
    _register_general()
    fn = get_function("update_entity")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={"entity_id": "e-001"})
    result = fn(ctx)
    assert result.success is False
    assert "properties" in result.error.lower()


# ── create_entity ─────────────────────────────────────────────


def test_create_entity_success():
    clear_registry()
    _register_general()
    fn = get_function("create_entity")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx, label="CodeEntity", properties={"name": "new_func", "entity_type": "function"})
    assert result.success is True
    assert result.data["created"] == "CodeEntity"
    assert result.data["properties"]["name"] == "new_func"


def test_create_entity_no_label():
    clear_registry()
    _register_general()
    fn = get_function("create_entity")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx, properties={"name": "x"})
    assert result.success is False
    assert "label" in result.error.lower()


def test_create_entity_invalid_label():
    clear_registry()
    _register_general()
    fn = get_function("create_entity")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx, label="FakeEntity", properties={"name": "x"})
    assert result.success is False
    assert "invalid" in result.error.lower() or "FakeEntity" in result.error


# ── create_relation ───────────────────────────────────────────


def test_create_relation_success():
    clear_registry()
    _register_general()
    fn = get_function("create_relation")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx, rel_type="calls", from_id="e-001", to_id="e-002")
    assert result.success is True
    assert result.data["created_relation"] == "calls"
    assert result.data["from"] == "e-001"
    assert result.data["to"] == "e-002"


def test_create_relation_missing_params():
    clear_registry()
    _register_general()
    fn = get_function("create_relation")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx, rel_type="calls")
    assert result.success is False
    assert "missing" in result.error.lower() or "from_id" in result.error or "to_id" in result.error


# ── check_condition ───────────────────────────────────────────


def test_check_condition_gt():
    clear_registry()
    _register_general()
    fn = get_function("check_condition")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={"entity": {"lines": 250}})
    result = fn(ctx, field="lines", operator=">", value=100)
    assert result.success is True
    assert result.data["condition_met"] is True
    assert result.data["actual"] == 250
    assert result.data["expected"] == 100


def test_check_condition_eq():
    clear_registry()
    _register_general()
    fn = get_function("check_condition")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={"entity": {"status": "running"}})
    result = fn(ctx, field="status", operator="==", value="running")
    assert result.success is True
    assert result.data["condition_met"] is True


def test_check_condition_field_not_found():
    clear_registry()
    _register_general()
    fn = get_function("check_condition")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={"entity": {"lines": 10}})
    result = fn(ctx, field="missing_field", operator="==", value=10)
    assert result.success is False
    assert "field" in result.error.lower()


# ── send_notification ─────────────────────────────────────────


def test_send_notification_success():
    clear_registry()
    _register_general()
    fn = get_function("send_notification")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx, recipients=["alice", "bob"], message="Deploy completed")
    assert result.success is True
    assert "alice" in result.data["notified"]
    assert "bob" in result.data["notified"]
    assert "Deploy completed" in result.data["message_preview"]


def test_send_notification_no_recipients():
    clear_registry()
    _register_general()
    fn = get_function("send_notification")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx, message="Hello")
    assert result.success is False
    assert "recipients" in result.error.lower()
