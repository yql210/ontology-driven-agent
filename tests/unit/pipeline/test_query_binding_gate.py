from __future__ import annotations

from unittest.mock import Mock

import pytest

from ontoagent.domain.build_binding import BuildBinding, GraphNamespace, VectorNamespace
from ontoagent.domain.business_entry import BusinessEntryLookupResult, LookupReason, LookupStatus
from ontoagent.domain.index_health import (
    BusinessEntryIndexHealth,
    BusinessEntryIndexStatus,
    IndexHealthReason,
)
from ontoagent.domain.query_envelope import QueryBlockReason, QueryEnvelopeStatus
from ontoagent.pipeline.query_binding_gate import QueryBindingGate


def _health(status: BusinessEntryIndexStatus = BusinessEntryIndexStatus.HEALTHY) -> BusinessEntryIndexHealth:
    if status is BusinessEntryIndexStatus.HEALTHY:
        return BusinessEntryIndexHealth(1, 1, 1, 1, 1, 0, status, ())
    if status is BusinessEntryIndexStatus.DEGRADED:
        return BusinessEntryIndexHealth(1, 1, 1, 1, 0, 1, status, (IndexHealthReason.CAPABILITY_VECTOR_WRITE_FAILED,))
    return BusinessEntryIndexHealth(0, 0, 0, 0, 0, 0, status, (IndexHealthReason.NO_ELIGIBLE_ENTRIES,))


def _binding(
    *,
    backend: str = "neo4j",
    health_status: BusinessEntryIndexStatus = BusinessEntryIndexStatus.HEALTHY,
) -> BuildBinding:
    return BuildBinding(
        "1",
        "build-1",
        "repo-1",
        "generation-1",
        "revision-1",
        "1",
        "2026-09-02T10:00:00Z",
        GraphNamespace(backend, "graph.example", "ontology"),
        VectorNamespace("chroma", "chroma.example", "business-entry", "embed-v1", "1"),
        _health(health_status),
    )


def _result(status: LookupStatus = LookupStatus.NOT_FOUND) -> BusinessEntryLookupResult:
    reasons = () if status is LookupStatus.FOUND else (LookupReason.NO_CAPABILITY_MATCH,)
    return BusinessEntryLookupResult(status, (), reasons)


def _gate(binding: object = None, result: BusinessEntryLookupResult | None = None) -> tuple[QueryBindingGate, Mock]:
    finder = Mock()
    finder.find.return_value = _result() if result is None else result
    return QueryBindingGate(_binding() if binding is None else binding, finder), finder


def test_find_returns_ready_binding_and_forwards_exact_generation() -> None:
    gate, finder = _gate()

    envelope = gate.find(" repo-1 ", "orders")

    assert envelope.status is QueryEnvelopeStatus.READY
    assert envelope.repo_id == "repo-1"
    assert envelope.build_id == "build-1"
    assert envelope.generation_id == "generation-1"
    assert envelope.source_revision == "revision-1"
    assert envelope.result == _result()
    finder.find.assert_called_once_with("repo-1", "orders", top_k=5, domain=None, generation_id="generation-1")


@pytest.mark.parametrize(
    ("repo_id", "generation_id", "source_revision", "reason"),
    [
        (" ", None, None, QueryBlockReason.BINDING_INVALID),
        ("repo-2", None, None, QueryBlockReason.REPO_MISMATCH),
        ("repo-1", "generation-2", None, QueryBlockReason.GENERATION_MISMATCH),
        ("repo-1", None, "revision-2", QueryBlockReason.SOURCE_REVISION_MISMATCH),
        ("repo-1", " ", None, QueryBlockReason.BINDING_INVALID),
        ("repo-1", None, " ", QueryBlockReason.BINDING_INVALID),
    ],
)
def test_find_fails_closed_for_missing_mismatched_or_malformed_request_identity(
    repo_id: object,
    generation_id: object,
    source_revision: object,
    reason: QueryBlockReason,
) -> None:
    gate, finder = _gate()

    envelope = gate.find(repo_id, "orders", generation_id=generation_id, source_revision=source_revision)  # type: ignore[arg-type]

    assert envelope.status is QueryEnvelopeStatus.BLOCKED
    assert envelope.reasons == (reason,)
    assert envelope.result is None
    finder.find.assert_not_called()


def test_find_fails_closed_for_non_neo4j_binding() -> None:
    gate, finder = _gate(_binding(backend="nebula"))

    envelope = gate.find("repo-1", "orders")

    assert envelope.status is QueryEnvelopeStatus.BLOCKED
    assert envelope.reasons == (QueryBlockReason.GRAPH_NAMESPACE_MISMATCH,)
    finder.find.assert_not_called()


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (BusinessEntryIndexStatus.DEGRADED, QueryBlockReason.INDEX_DEGRADED),
        (BusinessEntryIndexStatus.UNAVAILABLE, QueryBlockReason.INDEX_UNAVAILABLE),
    ],
)
def test_find_fails_closed_for_unhealthy_business_entry_index(
    status: BusinessEntryIndexStatus, reason: QueryBlockReason
) -> None:
    gate, finder = _gate(_binding(health_status=status))

    envelope = gate.find("repo-1", "orders")

    assert envelope.status is QueryEnvelopeStatus.BLOCKED
    assert envelope.reasons == (reason,)
    finder.find.assert_not_called()


def test_find_keeps_not_found_as_a_ready_finder_result() -> None:
    result = _result(LookupStatus.NOT_FOUND)
    gate, finder = _gate(result=result)

    envelope = gate.find("repo-1", "orders", generation_id="generation-1", source_revision="revision-1")

    assert envelope.status is QueryEnvelopeStatus.READY
    assert envelope.result is result
    finder.find.assert_called_once_with("repo-1", "orders", top_k=5, domain=None, generation_id="generation-1")


def test_find_fails_closed_for_an_invalid_explicit_binding() -> None:
    gate, finder = _gate(object())

    envelope = gate.find("repo-1", "orders")

    assert envelope.status is QueryEnvelopeStatus.BLOCKED
    assert envelope.reasons == (QueryBlockReason.BINDING_INVALID,)
    finder.find.assert_not_called()
