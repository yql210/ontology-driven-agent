from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from ontoagent.domain.build_binding import BuildBinding, GraphNamespace, VectorNamespace
from ontoagent.domain.index_health import (
    BusinessEntryIndexHealth,
    BusinessEntryIndexStatus,
)


def _health() -> BusinessEntryIndexHealth:
    return BusinessEntryIndexHealth(
        eligible_entries_seen=1,
        capabilities_merged=1,
        realized_by_submitted=1,
        capability_vectors_submitted=1,
        capability_vectors_confirmed=1,
        capability_vectors_failed=0,
        status=BusinessEntryIndexStatus.HEALTHY,
        reasons=(),
    )


def _namespaces(schema_version: str = "1") -> tuple[GraphNamespace, VectorNamespace]:
    return (
        GraphNamespace("neo4j", "graph.example", "ontology"),
        VectorNamespace("chroma", "chroma.example", "business-entry", "embed-v1", schema_version),
    )


def _binding(**overrides: object) -> BuildBinding:
    graph, vector = _namespaces()
    values: dict[str, object] = {
        "binding_version": "1",
        "build_id": "build-1",
        "repo_id": "repo-1",
        "generation_id": "generation-1",
        "source_revision": "abc123",
        "schema_version": "1",
        "created_at": "2026-09-02T10:00:00Z",
        "graph_namespace": graph,
        "vector_namespace": vector,
        "business_entry_index": _health(),
    }
    values.update(overrides)
    return BuildBinding(**values)


def test_namespaces_normalize_and_serialize_without_secrets() -> None:
    graph = GraphNamespace(" neo4j ", " graph-id ", " space ")
    vector = VectorNamespace(" chroma ", " persist-id ", " entries ", " model ", " v1 ")
    assert graph == GraphNamespace("neo4j", "graph-id", "space")
    assert vector.to_dict() == {
        "backend": "chroma",
        "server_or_persist_identity": "persist-id",
        "collection_name": "entries",
        "embedding_model": "model",
        "schema_version": "v1",
    }
    assert "password" not in json.dumps(graph.to_dict()).lower()


def test_graph_namespace_rejects_blank_or_wrong_backend() -> None:
    with pytest.raises(ValueError):
        GraphNamespace("", "identity", "space")
    with pytest.raises(ValueError):
        GraphNamespace("unsupported", "identity", "space")


def test_vector_namespace_rejects_blank_or_wrong_backend() -> None:
    with pytest.raises(ValueError):
        VectorNamespace("", "identity", "collection", "model", "1")
    with pytest.raises(ValueError):
        VectorNamespace("unsupported", "identity", "collection", "model", "1")


def test_build_binding_validates_types_timestamp_and_schema() -> None:
    assert _binding().created_at == "2026-09-02T10:00:00+00:00"
    with pytest.raises(ValueError):
        _binding(created_at="2026-09-02T10:00:00")
    with pytest.raises(ValueError):
        _binding(created_at="2026-09-02T10:00:00+08:00")
    with pytest.raises(ValueError):
        _binding(schema_version="2")
    with pytest.raises(ValueError):
        _binding(vector_namespace=object())
    with pytest.raises(ValueError):
        _binding(business_entry_index=object())


def test_build_binding_is_frozen_and_json_safe() -> None:
    binding = _binding()
    with pytest.raises(FrozenInstanceError):
        binding.build_id = "changed"  # type: ignore[misc]
    payload = binding.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["business_entry_index"]["status"] == "healthy"


def test_index_health_invalid_reason_is_rejected() -> None:
    with pytest.raises(ValueError):
        _binding(business_entry_index=object())
