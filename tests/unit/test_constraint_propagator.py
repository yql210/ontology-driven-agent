from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontoagent.execution.constraints.propagator import (
    ConstraintPropagator,
    PropagationResult,
    PropagationRule,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_store():
    """Return a MagicMock graph store."""
    return MagicMock()


@pytest.fixture
def propagator(graph_store):
    """Return a ConstraintPropagator with a mocked graph store."""
    return ConstraintPropagator(graph_store)


def _make_rule(**overrides):
    """Factory for PropagationRule with sensible defaults."""
    defaults = {
        "name": "test_rule",
        "along": ["CALLS"],
        "direction": "forward",
        "max_depth": 5,
        "collect_property": "risk_level",
        "value_mapping": {"P0": "block", "P1": "warn"},
        "aggregation": "max",
    }
    defaults.update(overrides)
    return PropagationRule(**defaults)


# ---------------------------------------------------------------------------
# Tests: propagate()
# ---------------------------------------------------------------------------


def test_propagate_single_hop(graph_store, propagator):
    """Single-hop forward propagation finds directly connected nodes."""
    rule = _make_rule(along=["CALLS"], max_depth=1)

    # Node A --CALLS--> Node B (risk_level=P0)
    graph_store.query.return_value = [
        {"b": {"id": "B", "risk_level": "P0"}},
    ]

    result = propagator.propagate("A", rule)

    assert isinstance(result, PropagationResult)
    assert result.path_count == 1
    assert len(result.reached_nodes) == 1
    assert result.reached_nodes[0]["id"] == "B"
    assert result.aggregated_level == "block"


def test_propagate_multi_hop_bfs(graph_store, propagator):
    """Multi-hop BFS traverses breadth-first and collects all reachable values."""
    rule = _make_rule(along=["CALLS"], max_depth=3)

    # Simulate BFS:
    # Level 0: A -> returns B, C
    # Level 1: B -> returns D (P1), C -> returns E (P0)
    # Level 2: D -> returns nothing, E -> returns F (P0)
    call_count = 0
    responses = [
        [{"b": {"id": "B", "risk_level": None}}, {"b": {"id": "C", "risk_level": "P1"}}],
        [{"b": {"id": "D", "risk_level": "P0"}}],
        [],
        [],
        [],
    ]

    def side_effect(query, params):
        nonlocal call_count
        res = responses[call_count] if call_count < len(responses) else []
        call_count += 1
        return res

    graph_store.query.side_effect = side_effect

    result = propagator.propagate("A", rule)

    # C (P1), D (P0) — B had no risk_level, so not collected
    assert result.path_count == 2
    # P0 > P1 → block
    assert result.aggregated_level == "block"


def test_propagate_depth_limit(graph_store, propagator):
    """BFS stops at max_depth, not going deeper."""
    rule = _make_rule(along=["CALLS"], max_depth=2)

    # Depth 0: A -> B
    # Depth 1: B -> C (P1)
    # Depth 2: C -> D (P0) — should NOT be visited
    call_count = 0
    responses = [
        [{"b": {"id": "B", "risk_level": None}}],
        [{"b": {"id": "C", "risk_level": "P1"}}],
        [{"b": {"id": "D", "risk_level": "P0"}}],  # depth=2, should be skipped
    ]

    def side_effect(query, params):
        nonlocal call_count
        res = responses[call_count] if call_count < len(responses) else []
        call_count += 1
        return res

    graph_store.query.side_effect = side_effect

    result = propagator.propagate("A", rule)

    # Only C (P1) collected; D at depth 2 was not queried because loop stops
    # when depth >= max_depth — but B at depth 0 goes to depth 1 for C.
    # C is enqueued at depth 1. When C is popped, depth=1 < 2, so it queries
    # and finds D at depth 2. D is enqueued. When D is popped, depth=2 >= 2
    # so it skips. So D is not collected, only C.
    assert result.path_count == 1
    assert result.aggregated_level == "warn"


def test_propagate_backward_direction(graph_store, propagator):
    """Backward propagation traverses incoming relations."""
    rule = _make_rule(along=["CALLS"], direction="backward", max_depth=2)

    # Backward: MATCH (a)-[:CALLS]->(b) WHERE b.id = $id RETURN a
    # A: nothing incoming
    graph_store.query.return_value = [
        {"a": {"id": "X", "risk_level": "P0"}},
    ]

    result = propagator.propagate("A", rule)

    assert result.path_count == 1
    assert result.reached_nodes[0]["id"] == "X"
    assert result.aggregated_level == "block"


def test_propagate_aggregation_max(graph_store, propagator):
    """'max' aggregation picks the most severe level."""
    rule = _make_rule(aggregation="max")

    graph_store.query.side_effect = [
        [{"b": {"id": "B", "risk_level": "P1"}}],
        [{"b": {"id": "C", "risk_level": "P0"}}],
        [],
        [],
    ]

    result = propagator.propagate("A", rule)
    assert result.aggregated_level == "block"


def test_propagate_graph_query_exception_handled(graph_store, propagator):
    """Exceptions from graph_store.query are caught, not propagated."""
    rule = _make_rule(max_depth=1)

    graph_store.query.side_effect = RuntimeError("Boom!")

    result = propagator.propagate("A", rule)
    # Should return empty result, not raise
    assert result.path_count == 0
    assert result.aggregated_level == "allow"


def test_propagate_no_matching_property(graph_store, propagator):
    """Nodes without the collected property are skipped."""
    rule = _make_rule(collect_property="risk_level", max_depth=1)

    graph_store.query.return_value = [
        {"b": {"id": "B"}},  # no risk_level
    ]

    result = propagator.propagate("A", rule)
    assert result.path_count == 0
    assert result.aggregated_level == "allow"


def test_propagate_unknown_value_mapped_to_allow(graph_store, propagator):
    """A property value not in value_mapping defaults to 'allow'."""
    rule = _make_rule(value_mapping={"P0": "block"}, max_depth=1)

    graph_store.query.return_value = [
        {"b": {"id": "B", "risk_level": "P1"}},
    ]

    result = propagator.propagate("A", rule)
    assert result.path_count == 1
    assert result.aggregated_level == "allow"


# ---------------------------------------------------------------------------
# Tests: find_entry_points()
# ---------------------------------------------------------------------------


def test_find_entry_points_returns_nodes_with_entry_category(graph_store, propagator):
    """find_entry_points returns CodeEntity nodes with entryCategory set."""
    graph_store.query.side_effect = [
        # First query: find entry points (nodes with entryCategory)
        [{"a": {"id": "E1", "entryCategory": "rest_api", "name": "handle_request"}}],
        # Second query: continue BFS for non-entry
        [],
    ]

    result = propagator.find_entry_points("A", max_depth=5)

    assert len(result) == 1
    assert result[0]["id"] == "E1"
    assert result[0]["entryCategory"] == "rest_api"


def test_find_entry_points_bfs_continues_past_non_entry_nodes(graph_store, propagator):
    """BFS continues past nodes without entryCategory to find deeper entry points."""
    call_count = 0
    responses = [
        # Depth 0: A -> no entry points found
        [],
        # Depth 0: A -> non-entry neighbor B
        [{"a": {"id": "B"}}],
        # Depth 1: B -> entry point E1
        [{"a": {"id": "E1", "entryCategory": "rest_api"}}],
        # Depth 1: B -> non-entry neighbors
        [],
        # Depth 2: E1 -> (no deeper, loop ends or more)
        [],
        [],
    ]

    def side_effect(query, params):
        nonlocal call_count
        res = responses[call_count] if call_count < len(responses) else []
        call_count += 1
        return res

    graph_store.query.side_effect = side_effect

    result = propagator.find_entry_points("A", max_depth=5)
    assert len(result) == 1
    assert result[0]["id"] == "E1"


def test_find_entry_points_depth_limit(graph_store, propagator):
    """find_entry_points respects max_depth."""
    graph_store.query.side_effect = [
        [],  # entry point query depth 0
        [{"a": {"id": "B"}}],  # non-entry depth 0
        [],  # entry point query depth 1
        [{"a": {"id": "C"}}],  # non-entry depth 1
        [{"a": {"id": "E2", "entryCategory": "grpc"}}],  # depth 2 → should NOT be queried (max_depth=2)
        [],
    ]

    result = propagator.find_entry_points("A", max_depth=2)
    # E2 at depth 2 should NOT appear because depth >= max_depth stops before query
    assert len(result) == 0


def test_find_entry_points_empty_graph(graph_store, propagator):
    """Empty graph returns empty list."""
    graph_store.query.return_value = []

    result = propagator.find_entry_points("A", max_depth=5)
    assert isinstance(result, list)
    assert len(result) == 0


def test_find_entry_points_duplicate_visited_not_added(graph_store, propagator):
    """Already-visited nodes are not re-added as entry points."""
    call_count = 0
    responses = [
        [{"a": {"id": "E1", "entryCategory": "rest_api"}}],
        [{"a": {"id": "E1"}}],  # same node as non-entry (should be skipped)
        [],
    ]

    def side_effect(query, params):
        nonlocal call_count
        res = responses[call_count] if call_count < len(responses) else []
        call_count += 1
        return res

    graph_store.query.side_effect = side_effect

    result = propagator.find_entry_points("A", max_depth=5)
    # E1 appears once (first as entry point, second time skipped because visited)
    assert len(result) == 1
    assert result[0]["id"] == "E1"
