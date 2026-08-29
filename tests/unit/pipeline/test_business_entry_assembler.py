from __future__ import annotations

import math

import pytest

from ontoagent.domain.business_entry import BusinessEntryLookupResult, LookupReason, LookupStatus, RawBusinessEntry
from ontoagent.pipeline.business_entry_assembler import (
    CapabilityCandidate,
    assemble_business_entry_lookup,
    capability_score_from_cosine_distance,
)
from ontoagent.pipeline.business_entry_repository import RepositoryLookup


def _raw_entry(**overrides: object) -> RawBusinessEntry:
    values: dict[str, object] = {
        "capability_id": "cap-1",
        "capability_name": "Orders",
        "capability_domain": "commerce",
        "capability_repo_id": "repo-a",
        "code_entity_id": "code-1",
        "code_entity_repo_id": "repo-a",
        "entry_name": "create_order",
        "entry_category": "http_api",
        "file_path": "src/orders.py",
        "start_line": 10,
        "end_line": 20,
        "entry_metadata": '{"route": "/orders", "method": "POST"}',
    }
    values.update(overrides)
    return RawBusinessEntry(**values)  # type: ignore[arg-type]


def _assemble(
    candidates: object = (CapabilityCandidate("cap-1", 0.25),), lookup: object = RepositoryLookup((), ())
) -> BusinessEntryLookupResult:
    result = assemble_business_entry_lookup("repo-a", candidates, lookup)  # type: ignore[arg-type]
    assert BusinessEntryLookupResult(result.status, result.evidences, result.reasons) == result
    return result


@pytest.mark.parametrize("distance", [0, 0.0, 2, 2.0])
def test_capability_candidate_accepts_finite_distance_boundaries_and_normalizes_to_float(distance: float) -> None:
    candidate = CapabilityCandidate("cap-1", distance)

    assert candidate.cosine_distance == float(distance)


@pytest.mark.parametrize("capability_id", ["", " ", 1])
@pytest.mark.parametrize("distance", [True, "0.5", -0.1, 2.1, math.inf, -math.inf, math.nan])
def test_capability_candidate_rejects_invalid_fields(capability_id: object, distance: object) -> None:
    with pytest.raises(ValueError):
        CapabilityCandidate(capability_id, distance)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("distance", "score"),
    [(0.0, 1.0), (0.25, 0.75), (1.0, 0.0), (2.0, 0.0)],
)
def test_capability_score_uses_documented_non_probabilistic_similarity_mapping(distance: float, score: float) -> None:
    assert capability_score_from_cosine_distance(distance) == score


@pytest.mark.parametrize("distance", [True, "0.5", -0.1, 2.1, math.inf, -math.inf, math.nan])
def test_capability_score_rejects_invalid_distances(distance: object) -> None:
    with pytest.raises(ValueError):
        capability_score_from_cosine_distance(distance)  # type: ignore[arg-type]


@pytest.mark.parametrize("repo_id", ["", " ", 1])
@pytest.mark.parametrize("candidates", ["cap-1", b"cap-1", {CapabilityCandidate("cap-1", 0.1)}, [object()]])
def test_assemble_rejects_incompatible_input_types_before_result_construction(
    repo_id: object, candidates: object
) -> None:
    with pytest.raises(ValueError):
        assemble_business_entry_lookup(repo_id, candidates, RepositoryLookup((), ()))  # type: ignore[arg-type]


def test_assemble_requires_exact_repository_lookup_instance() -> None:
    class SimilarLookup:
        entries = ()
        reasons = ()

    with pytest.raises(ValueError):
        _assemble(lookup=SimilarLookup())

    class RepositoryLookupSubclass(RepositoryLookup):
        pass

    with pytest.raises(ValueError):
        _assemble(lookup=RepositoryLookupSubclass((), ()))


def test_assemble_stably_deduplicates_candidates_using_the_minimum_distance() -> None:
    lookup = RepositoryLookup(
        (_raw_entry(capability_id="cap-1"), _raw_entry(capability_id="cap-2", code_entity_id="code-2")), ()
    )

    result = _assemble(
        (CapabilityCandidate("cap-1", 0.9), CapabilityCandidate("cap-2", 0.4), CapabilityCandidate("cap-1", 0.2)),
        lookup,
    )

    assert [evidence.capability_id for evidence in result.evidences] == ["cap-1", "cap-2"]
    assert [evidence.capability_score for evidence in result.evidences] == [0.8, 0.6]


def test_assemble_maps_happy_raw_entry_to_evidence() -> None:
    result = _assemble(lookup=RepositoryLookup((_raw_entry(),), ()))

    assert result.status is LookupStatus.FOUND
    assert result.reasons == ()
    assert result.evidences[0].to_dict() == {
        "repo_id": "repo-a",
        "capability_id": "cap-1",
        "capability_name": "Orders",
        "capability_domain": "commerce",
        "capability_score": 0.75,
        "code_entity_id": "code-1",
        "entry_name": "create_order",
        "entry_category": "http_api",
        "file_path": "src/orders.py",
        "start_line": 10,
        "end_line": 20,
        "endpoint": {"method": "POST", "route": "/orders"},
        "extraction_confidence": None,
    }


@pytest.mark.parametrize(
    ("category", "metadata", "endpoint"),
    [
        ("http_api", '{"route": "/orders", "method": "POST"}', (("method", "POST"), ("route", "/orders"))),
        ("rpc_service", '{"route": "Order/Create"}', (("route", "Order/Create"),)),
        ("scheduled", '{"cron": "0 * * * *"}', (("cron", "0 * * * *"),)),
        ("mq_consumer", '{"topic": "orders.created"}', (("topic", "orders.created"),)),
        ("event_handler", '{"event": "OrderCreated"}', (("event", "OrderCreated"),)),
    ],
)
def test_assemble_supports_all_endpoint_category_shapes(
    category: str, metadata: str, endpoint: tuple[tuple[str, str], ...]
) -> None:
    result = _assemble(lookup=RepositoryLookup((_raw_entry(entry_category=category, entry_metadata=metadata),), ()))

    assert result.status is LookupStatus.FOUND
    assert result.evidences[0].endpoint == endpoint


@pytest.mark.parametrize(
    ("category", "metadata", "reason"),
    [
        ("http_api", None, LookupReason.MISSING_ENTRY_METADATA),
        ("rpc_service", "{}", LookupReason.MISSING_ENTRY_METADATA),
        ("scheduled", "{}", LookupReason.MISSING_ENTRY_METADATA),
        ("mq_consumer", "{}", LookupReason.MISSING_ENTRY_METADATA),
        ("event_handler", "{}", LookupReason.MISSING_ENTRY_METADATA),
        ("unknown", '{"route": "/orders"}', LookupReason.UNSUPPORTED_ENTRY_CATEGORY),
    ],
)
def test_assemble_retains_evidence_and_degrades_for_incomplete_or_unknown_metadata(
    category: str, metadata: str | None, reason: LookupReason
) -> None:
    result = _assemble(lookup=RepositoryLookup((_raw_entry(entry_category=category, entry_metadata=metadata),), ()))

    assert result.status is LookupStatus.DEGRADED
    assert result.evidences[0].endpoint == ()
    assert result.reasons == (reason,)


def test_assemble_degrades_missing_file_path_without_dropping_evidence() -> None:
    result = _assemble(lookup=RepositoryLookup((_raw_entry(file_path=None),), ()))

    assert result.status is LookupStatus.DEGRADED
    assert result.evidences[0].file_path is None
    assert result.reasons == (LookupReason.MISSING_ENTRY_METADATA,)


def test_assemble_drops_cross_repo_and_unknown_entries_without_leaking_them() -> None:
    lookup = RepositoryLookup(
        (
            _raw_entry(capability_repo_id="repo-b"),
            _raw_entry(capability_id="unknown", code_entity_id="code-2"),
            _raw_entry(code_entity_id="code-3", code_entity_repo_id="repo-b"),
        ),
        (),
    )

    result = _assemble(lookup=lookup)

    assert result.status is LookupStatus.DEGRADED
    assert result.evidences == ()
    assert result.reasons == (LookupReason.REPO_MISMATCH, LookupReason.CORRUPT_GRAPH_DATA)


def test_assemble_deduplicates_evidence_and_orders_by_candidate_then_raw_order() -> None:
    lookup = RepositoryLookup(
        (
            _raw_entry(capability_id="cap-2", code_entity_id="code-2"),
            _raw_entry(capability_id="cap-1", code_entity_id="code-1"),
            _raw_entry(capability_id="cap-2", code_entity_id="code-2", entry_name="duplicate"),
            _raw_entry(capability_id="cap-1", code_entity_id="code-3"),
        ),
        (),
    )

    result = _assemble((CapabilityCandidate("cap-1", 0.1), CapabilityCandidate("cap-2", 0.2)), lookup)

    assert [(e.capability_id, e.code_entity_id, e.entry_name) for e in result.evidences] == [
        ("cap-1", "code-1", "create_order"),
        ("cap-1", "code-3", "create_order"),
        ("cap-2", "code-2", "create_order"),
    ]


@pytest.mark.parametrize(
    ("candidates", "lookup", "status", "reasons"),
    [
        ((), RepositoryLookup((), ()), LookupStatus.NOT_FOUND, (LookupReason.NO_CAPABILITY_MATCH,)),
        (
            (),
            RepositoryLookup((), (LookupReason.NO_CAPABILITY_MATCH,)),
            LookupStatus.NOT_FOUND,
            (LookupReason.NO_CAPABILITY_MATCH,),
        ),
        (
            (CapabilityCandidate("cap-1", 0.1),),
            RepositoryLookup((), ()),
            LookupStatus.NOT_FOUND,
            (LookupReason.NO_REALIZATION,),
        ),
        (
            (CapabilityCandidate("cap-1", 0.1),),
            RepositoryLookup((), (LookupReason.NO_REALIZATION,)),
            LookupStatus.NOT_FOUND,
            (LookupReason.NO_REALIZATION,),
        ),
        ((), RepositoryLookup((_raw_entry(),), ()), LookupStatus.DEGRADED, (LookupReason.CORRUPT_GRAPH_DATA,)),
        (
            (CapabilityCandidate("cap-1", 0.1),),
            RepositoryLookup((), (LookupReason.REPO_MISMATCH,)),
            LookupStatus.DEGRADED,
            (LookupReason.REPO_MISMATCH,),
        ),
    ],
)
def test_assemble_selects_every_normal_final_status_branch(
    candidates: tuple[CapabilityCandidate, ...],
    lookup: RepositoryLookup,
    status: LookupStatus,
    reasons: tuple[LookupReason, ...],
) -> None:
    result = _assemble(candidates, lookup)

    assert result.status is status
    assert result.reasons == reasons


def test_assemble_preserves_lookup_reason_order_and_stably_appends_generated_reasons() -> None:
    lookup = RepositoryLookup(
        (_raw_entry(capability_id="unknown"), _raw_entry(capability_repo_id="repo-b")),
        (LookupReason.NO_REALIZATION, LookupReason.NO_CAPABILITY_MATCH, LookupReason.NO_REALIZATION),
    )

    result = _assemble(lookup=lookup)

    assert result.reasons == (
        LookupReason.NO_REALIZATION,
        LookupReason.NO_CAPABILITY_MATCH,
        LookupReason.CORRUPT_GRAPH_DATA,
        LookupReason.REPO_MISMATCH,
    )


def test_assemble_maps_only_legal_backend_unavailable_shape_before_candidate_validation() -> None:
    result = _assemble("not candidates", RepositoryLookup((), (LookupReason.BACKEND_UNAVAILABLE,)))

    assert result.status is LookupStatus.BACKEND_UNAVAILABLE
    assert result.evidences == ()
    assert result.reasons == (LookupReason.BACKEND_UNAVAILABLE,)


@pytest.mark.parametrize(
    "lookup",
    [
        RepositoryLookup((), (LookupReason.BACKEND_UNAVAILABLE, LookupReason.NO_REALIZATION)),
        RepositoryLookup((), (LookupReason.BACKEND_UNAVAILABLE, LookupReason.BACKEND_UNAVAILABLE)),
        RepositoryLookup((_raw_entry(),), (LookupReason.BACKEND_UNAVAILABLE,)),
    ],
)
def test_assemble_rejects_malformed_backend_unavailable_shapes(lookup: RepositoryLookup) -> None:
    with pytest.raises(ValueError):
        _assemble(lookup=lookup)
