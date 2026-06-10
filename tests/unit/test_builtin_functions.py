"""Tests for builtin functions registered via the new registry."""

from __future__ import annotations

from layerkg.action_types import ActionContext
from layerkg.functions.registry import clear_registry, get_function


class MockGraphStore:
    """Mock graph store for testing builtin functions."""

    def __init__(self, entities: dict | None = None, query_results: list | None = None):
        self._entities = entities or {}
        self._query_results = query_results or []

    def get_node(self, node_id: str) -> dict | None:
        return self._entities.get(node_id)

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        return self._query_results


def _register_builtins():
    """Import + register builtin functions (works after clear_registry)."""
    from layerkg.functions.builtin import register_all

    register_all()


def test_check_refactor_eligibility_success():
    clear_registry()
    _register_builtins()
    fn = get_function("check_refactor_eligibility")
    assert fn is not None, "check_refactor_eligibility not registered"

    store = MockGraphStore(entities={"e1": {"name": "big_fn", "lines": 250, "branches": 15}})
    ctx = ActionContext(
        graph_store=store, match_data={"entity": {"lines": 250, "branches": 15}, "entity_id": "e1", "max_lines": 100}
    )
    result = fn(ctx)
    assert result.success is True
    assert "total_lines" in result.data
    assert result.data["total_lines"] == 250


def test_check_refactor_eligibility_too_small():
    clear_registry()
    _register_builtins()
    fn = get_function("check_refactor_eligibility")
    assert fn is not None

    store = MockGraphStore(entities={"e1": {"name": "small_fn", "lines": 50, "branches": 2}})
    ctx = ActionContext(
        graph_store=store, match_data={"entity": {"lines": 50, "branches": 2}, "entity_id": "e1", "max_lines": 100}
    )
    result = fn(ctx)
    assert result.success is False
    assert result.error is not None
    assert "50" in result.error


def test_check_refactor_eligibility_no_entity():
    clear_registry()
    _register_builtins()
    fn = get_function("check_refactor_eligibility")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx)
    assert result.success is False
    assert "Entity not found" in result.error


def test_trace_call_chain_success():
    clear_registry()
    _register_builtins()
    fn = get_function("trace_call_chain")
    assert fn is not None

    query_results = [
        {"id": "c1", "name": "helper_a", "entity_type": "function"},
        {"id": "c2", "name": "helper_b", "entity_type": "function"},
    ]
    store = MockGraphStore(entities={"e1": {"name": "main_fn"}}, query_results=query_results)
    ctx = ActionContext(graph_store=store, match_data={"entity": {"name": "main_fn"}, "entity_id": "e1", "depth": 3})
    result = fn(ctx)
    assert result.success is True
    assert result.data["depth"] == 3
    assert len(result.data["call_tree"]) == 2


def test_trace_call_chain_no_entity():
    clear_registry()
    _register_builtins()
    fn = get_function("trace_call_chain")
    assert fn is not None

    ctx = ActionContext(graph_store=MockGraphStore(), match_data={})
    result = fn(ctx)
    assert result.success is False
    assert "Entity not found" in result.error


def test_generate_api_doc_success():
    clear_registry()
    _register_builtins()
    fn = get_function("generate_api_doc")
    assert fn is not None

    store = MockGraphStore(
        entities={
            "e1": {
                "name": "my_func",
                "entityType": "function",
                "params": ["x", "y"],
                "return_type": "int",
                "docstring": "Does stuff.",
            }
        }
    )
    ctx = ActionContext(graph_store=store, match_data={"entity": {"name": "my_func"}, "entity_id": "e1"})
    result = fn(ctx)
    assert result.success is True
    assert "doc_markdown" in result.data
    assert "my_func" in result.data["doc_markdown"]


def test_extract_interface_success():
    clear_registry()
    _register_builtins()
    fn = get_function("extract_interface")
    assert fn is not None

    store = MockGraphStore(entities={"e1": {"name": "Cache", "entityType": "class"}})
    ctx = ActionContext(
        graph_store=store,
        match_data={
            "entity": {"name": "Cache", "entityType": "class"},
            "entity_id": "e1",
            "class_methods": ["get", "set", "_internal", "clear"],
        },
    )
    result = fn(ctx)
    assert result.success is True
    assert result.data["interface_name"] == "ICache"
    assert result.data["public_methods"] == ["clear", "get", "set"]


def test_all_builtins_registered():
    clear_registry()
    _register_builtins()
    from layerkg.functions.registry import list_functions

    names = list_functions()
    assert "check_refactor_eligibility" in names
    assert "trace_call_chain" in names
    assert "generate_api_doc" in names
    assert "extract_interface" in names
