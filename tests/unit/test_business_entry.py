from __future__ import annotations

import math

import pytest

from ontoagent.domain.business_entry import (
    BusinessEntryEvidence,
    BusinessEntryLookupResult,
    LookupReason,
    LookupStatus,
    RawBusinessEntry,
    endpoint_from_metadata,
)
from ontoagent.domain.exceptions import BusinessEntryBackendUnavailable, OntoAgentError


def _evidence(**overrides: object) -> BusinessEntryEvidence:
    values: dict[str, object] = {
        "repo_id": "repo-a",
        "capability_id": "capability-1",
        "capability_name": "Orders",
        "capability_domain": "sales",
        "capability_score": 0.8,
        "code_entity_id": "code-1",
        "entry_name": "create_order",
        "entry_category": "http_api",
        "file_path": "src/orders.py",
        "start_line": 10,
        "end_line": 15,
        "endpoint": {"route": "/orders", "method": "POST"},
        "extraction_confidence": 0.9,
    }
    values.update(overrides)
    return BusinessEntryEvidence(**values)  # type: ignore[arg-type]


def _raw_entry(**overrides: object) -> RawBusinessEntry:
    values: dict[str, object] = {
        "capability_id": "capability-1",
        "capability_name": "Orders",
        "capability_domain": "sales",
        "capability_repo_id": "repo-a",
        "code_entity_id": "code-1",
        "code_entity_repo_id": "repo-a",
        "entry_name": "create_order",
        "entry_category": "http_api",
        "file_path": "src/orders.py",
        "start_line": 10,
        "end_line": 15,
        "entry_metadata": '{"route": "/orders", "method": "POST"}',
    }
    values.update(overrides)
    return RawBusinessEntry(**values)  # type: ignore[arg-type]


def test_lookup_enums_have_stable_wire_values() -> None:
    assert {member.name: member.value for member in LookupStatus} == {
        "FOUND": "found",
        "NOT_FOUND": "not_found",
        "DEGRADED": "degraded",
        "BACKEND_UNAVAILABLE": "backend_unavailable",
    }
    assert {member.name: member.value for member in LookupReason} == {
        "NO_CAPABILITY_MATCH": "no_capability_match",
        "NO_REALIZATION": "no_realization",
        "REPO_MISMATCH": "repo_mismatch",
        "MISSING_ENTRY_METADATA": "missing_entry_metadata",
        "UNSUPPORTED_ENTRY_CATEGORY": "unsupported_entry_category",
        "CORRUPT_GRAPH_DATA": "corrupt_graph_data",
        "BACKEND_UNAVAILABLE": "backend_unavailable",
    }


@pytest.mark.parametrize(
    ("category", "metadata", "expected"),
    [
        (
            "http_api",
            '{"route": " /orders ", "method": " POST ", "topic": "ignore"}',
            (("method", "POST"), ("route", "/orders")),
        ),
        (
            "rpc_service",
            '{"route": "/rpc", "method": "CALL", "event": "ignore"}',
            (("method", "CALL"), ("route", "/rpc")),
        ),
        (
            "scheduled",
            '{"schedule": " hourly ", "cron": " 0 * * * * "}',
            (("cron", "0 * * * *"), ("schedule", "hourly")),
        ),
        ("mq_consumer", '{"topic": " orders.created ", "route": "/ignore"}', (("topic", "orders.created"),)),
        ("event_handler", '{"event": " order.created ", "topic": "ignore"}', (("event", "order.created"),)),
    ],
)
def test_endpoint_from_metadata_accepts_only_category_allowlisted_fields(
    category: str, metadata: str, expected: tuple[tuple[str, str], ...]
) -> None:
    assert endpoint_from_metadata(category, metadata) == (expected, None)


@pytest.mark.parametrize("metadata", [None, "", "   ", "not json", "[]", "null", "42"])
def test_endpoint_from_metadata_rejects_missing_or_malformed_metadata(metadata: str | None) -> None:
    assert endpoint_from_metadata("http_api", metadata) == ((), LookupReason.MISSING_ENTRY_METADATA)


def test_endpoint_from_metadata_treats_blank_category_as_missing_only_when_metadata_bad() -> None:
    assert endpoint_from_metadata(None, None) == ((), LookupReason.MISSING_ENTRY_METADATA)
    assert endpoint_from_metadata("  ", '{"route": "/orders"}') == ((), LookupReason.UNSUPPORTED_ENTRY_CATEGORY)


@pytest.mark.parametrize("category", ["unknown", " HTTP_API "])
def test_endpoint_from_metadata_rejects_unknown_category_without_passthrough(category: str) -> None:
    assert endpoint_from_metadata(category, '{"route": "/orders", "topic": "orders"}') == (
        (),
        LookupReason.UNSUPPORTED_ENTRY_CATEGORY,
    )


@pytest.mark.parametrize(
    "metadata",
    [
        "{}",
        '{"route": ""}',
        '{"route": "   "}',
        '{"route": 42, "method": false}',
        '{"topic": "orders"}',
    ],
)
def test_endpoint_from_metadata_requires_allowed_nonblank_string_values(metadata: str) -> None:
    assert endpoint_from_metadata("http_api", metadata) == ((), LookupReason.MISSING_ENTRY_METADATA)


def test_evidence_normalizes_endpoint_and_defensively_copies_dicts() -> None:
    caller_endpoint = {"route": "/orders", "method": "POST"}
    evidence = _evidence(endpoint=caller_endpoint)
    caller_endpoint["route"] = "/changed"

    assert evidence.endpoint == (("method", "POST"), ("route", "/orders"))
    first_endpoint = evidence.endpoint_dict()
    first_endpoint["route"] = "/changed"
    serialized = evidence.to_dict()
    assert serialized["endpoint"] == {"method": "POST", "route": "/orders"}
    assert isinstance(serialized["endpoint"], dict)
    serialized["endpoint"]["route"] = "/changed"  # type: ignore[index]
    assert evidence.endpoint_dict() == {"method": "POST", "route": "/orders"}


def test_evidence_normalizes_endpoint_pairs_and_numeric_values() -> None:
    evidence = _evidence(
        endpoint=(("route", " /orders "), ("method", " POST ")),
        capability_score=1,
        extraction_confidence=0,
    )

    assert evidence.endpoint == (("method", "POST"), ("route", "/orders"))
    assert evidence.capability_score == 1.0
    assert evidence.extraction_confidence == 0.0


@pytest.mark.parametrize(
    "field", ["repo_id", "capability_id", "capability_name", "capability_domain", "code_entity_id", "entry_name"]
)
def test_evidence_rejects_blank_identity_or_name_fields(field: str) -> None:
    with pytest.raises(ValueError):
        _evidence(**{field: "  "})


@pytest.mark.parametrize(
    "field",
    [
        "capability_id",
        "capability_name",
        "capability_domain",
        "capability_repo_id",
        "code_entity_id",
        "code_entity_repo_id",
        "entry_name",
    ],
)
def test_raw_entry_rejects_blank_identity_or_name_fields(field: str) -> None:
    with pytest.raises(ValueError):
        _raw_entry(**{field: "  "})


@pytest.mark.parametrize("factory", [_evidence, _raw_entry])
@pytest.mark.parametrize("start_line", [0, -1, True, 1.0])
def test_records_reject_invalid_start_lines(factory: object, start_line: object) -> None:
    with pytest.raises(ValueError):
        factory(start_line=start_line)  # type: ignore[operator]


@pytest.mark.parametrize("factory", [_evidence, _raw_entry])
@pytest.mark.parametrize("end_line", [0, -1, False, 1.0])
def test_records_reject_invalid_end_lines(factory: object, end_line: object) -> None:
    with pytest.raises(ValueError):
        factory(end_line=end_line)  # type: ignore[operator]


@pytest.mark.parametrize("factory", [_evidence, _raw_entry])
def test_records_reject_reversed_line_ranges(factory: object) -> None:
    with pytest.raises(ValueError):
        factory(start_line=20, end_line=10)  # type: ignore[operator]


@pytest.mark.parametrize("field", ["capability_score", "extraction_confidence"])
@pytest.mark.parametrize("value", [True, -0.1, 1.1, math.inf, -math.inf, math.nan, "0.5"])
def test_evidence_rejects_invalid_scores_and_confidence(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _evidence(**{field: value})


@pytest.mark.parametrize("endpoint", [{"route": ""}, {"route": 1}, {1: "/orders"}, (("route", "/orders", "extra"),)])
def test_evidence_rejects_invalid_endpoint(endpoint: object) -> None:
    with pytest.raises(ValueError):
        _evidence(endpoint=endpoint)


@pytest.mark.parametrize(
    ("status", "evidences", "reasons"),
    [
        (LookupStatus.FOUND, (_evidence(),), ()),
        (LookupStatus.NOT_FOUND, (), (LookupReason.NO_CAPABILITY_MATCH,)),
        (LookupStatus.NOT_FOUND, (), (LookupReason.NO_REALIZATION, LookupReason.NO_CAPABILITY_MATCH)),
        (LookupStatus.DEGRADED, (), (LookupReason.CORRUPT_GRAPH_DATA,)),
        (LookupStatus.DEGRADED, (_evidence(),), (LookupReason.MISSING_ENTRY_METADATA,)),
        (LookupStatus.BACKEND_UNAVAILABLE, (), (LookupReason.BACKEND_UNAVAILABLE,)),
    ],
)
def test_lookup_result_accepts_every_legal_status_combination(
    status: LookupStatus, evidences: tuple[BusinessEntryEvidence, ...], reasons: tuple[LookupReason, ...]
) -> None:
    result = BusinessEntryLookupResult(status=status, evidences=list(evidences), reasons=list(reasons))  # type: ignore[arg-type]
    assert result.evidences == evidences
    assert result.reasons == reasons


@pytest.mark.parametrize(
    ("status", "evidences", "reasons"),
    [
        (LookupStatus.FOUND, (), ()),
        (LookupStatus.FOUND, (_evidence(),), (LookupReason.NO_REALIZATION,)),
        (LookupStatus.NOT_FOUND, (_evidence(),), (LookupReason.NO_REALIZATION,)),
        (LookupStatus.NOT_FOUND, (), ()),
        (LookupStatus.NOT_FOUND, (), (LookupReason.REPO_MISMATCH,)),
        (LookupStatus.DEGRADED, (), ()),
        (LookupStatus.DEGRADED, (), (LookupReason.BACKEND_UNAVAILABLE,)),
        (LookupStatus.BACKEND_UNAVAILABLE, (_evidence(),), (LookupReason.BACKEND_UNAVAILABLE,)),
        (LookupStatus.BACKEND_UNAVAILABLE, (), (LookupReason.NO_REALIZATION,)),
        (LookupStatus.BACKEND_UNAVAILABLE, (), (LookupReason.BACKEND_UNAVAILABLE, LookupReason.NO_REALIZATION)),
    ],
)
def test_lookup_result_rejects_every_illegal_status_combination(
    status: LookupStatus, evidences: tuple[BusinessEntryEvidence, ...], reasons: tuple[LookupReason, ...]
) -> None:
    with pytest.raises(ValueError):
        BusinessEntryLookupResult(status=status, evidences=evidences, reasons=reasons)


def test_lookup_result_rejects_non_enum_inputs() -> None:
    with pytest.raises(ValueError):
        BusinessEntryLookupResult(status="found", evidences=(_evidence(),), reasons=())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        BusinessEntryLookupResult(status=LookupStatus.FOUND, evidences=(_evidence(),), reasons=("no_realization",))  # type: ignore[arg-type]


def test_backend_unavailable_error_has_no_backend_specific_default_message() -> None:
    error = BusinessEntryBackendUnavailable()
    assert isinstance(error, OntoAgentError)
    assert str(error) == ""
