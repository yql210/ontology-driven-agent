from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from ontoagent.domain.business_entry import (
    BusinessEntryEvidence,
    BusinessEntryLookupResult,
    LookupStatus,
)
from ontoagent.domain.query_envelope import QueryBlockReason, QueryEnvelope, QueryEnvelopeStatus


def _evidence() -> BusinessEntryEvidence:
    return BusinessEntryEvidence(
        "repo-1",
        "cap-1",
        "Orders",
        "sales",
        0.8,
        "code-1",
        "create_order",
        "http_api",
        "orders.py",
        1,
        2,
        {"route": "/orders", "method": "POST"},
        0.9,
    )


def _result() -> BusinessEntryLookupResult:
    return BusinessEntryLookupResult(LookupStatus.FOUND, (_evidence(),), ())


def test_query_block_reasons_have_stable_declaration_order() -> None:
    assert tuple(QueryBlockReason) == (
        QueryBlockReason.MISSING_BINDING,
        QueryBlockReason.BINDING_INVALID,
        QueryBlockReason.REPO_MISMATCH,
        QueryBlockReason.GENERATION_MISMATCH,
        QueryBlockReason.SOURCE_REVISION_MISMATCH,
        QueryBlockReason.GRAPH_NAMESPACE_MISMATCH,
        QueryBlockReason.VECTOR_NAMESPACE_MISMATCH,
        QueryBlockReason.SCHEMA_VERSION_MISMATCH,
        QueryBlockReason.INDEX_UNAVAILABLE,
        QueryBlockReason.INDEX_DEGRADED,
        QueryBlockReason.MANIFEST_UNAVAILABLE,
        QueryBlockReason.MANIFEST_INTEGRITY_FAILURE,
    )


def test_blocked_envelope_accepts_partial_identity_and_serializes() -> None:
    envelope = QueryEnvelope(
        QueryEnvelopeStatus.BLOCKED, " repo-1 ", " build-1 ", None, None, (QueryBlockReason.INDEX_UNAVAILABLE,), None
    )
    assert envelope.repo_id == "repo-1"
    assert json.loads(json.dumps(envelope.to_dict())) == envelope.to_dict()
    assert envelope.to_dict()["reasons"] == ["index_unavailable"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"result": _result()},
        {"reasons": ()},
        {"result": "bad"},
        {"build_id": " "},
        {"reasons": (QueryBlockReason.INDEX_DEGRADED, QueryBlockReason.INDEX_UNAVAILABLE)},
        {"reasons": (QueryBlockReason.INDEX_UNAVAILABLE, QueryBlockReason.INDEX_UNAVAILABLE)},
    ],
)
def test_blocked_envelope_rejects_invalid_contract(kwargs: dict[str, object]) -> None:
    values = {
        "status": QueryEnvelopeStatus.BLOCKED,
        "repo_id": "repo-1",
        "build_id": None,
        "generation_id": None,
        "source_revision": None,
        "reasons": (QueryBlockReason.INDEX_UNAVAILABLE,),
        "result": None,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        QueryEnvelope(**values)  # type: ignore[arg-type]


def test_ready_envelope_requires_result_reasons_and_identities() -> None:
    envelope = QueryEnvelope(
        status=QueryEnvelopeStatus.READY,
        repo_id="repo-1",
        build_id="build-1",
        generation_id="gen-1",
        source_revision="rev-1",
        reasons=(),
        result=_result(),
    )
    assert envelope.to_dict()["result"]["status"] == "found"
    for kwargs in ({"result": None}, {"reasons": (QueryBlockReason.BINDING_INVALID,)}, {"source_revision": None}):
        with pytest.raises(ValueError):
            values = {
                "status": QueryEnvelopeStatus.READY,
                "repo_id": "repo-1",
                "build_id": "build-1",
                "generation_id": "gen-1",
                "source_revision": "rev-1",
                "reasons": (),
                "result": _result(),
            }
            values.update(kwargs)
            QueryEnvelope(**values)  # type: ignore[arg-type]


def test_envelope_is_frozen_and_result_has_backward_compatible_dict() -> None:
    envelope = QueryEnvelope(QueryEnvelopeStatus.READY, "repo-1", "build-1", "gen-1", "rev-1", (), _result())
    with pytest.raises(FrozenInstanceError):
        envelope.repo_id = "x"  # type: ignore[misc]
    assert envelope.result.to_dict()["evidences"][0]["entry_name"] == "create_order"
