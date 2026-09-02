"""Business-entry lookup domain records and normalization helpers."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias


class LookupStatus(StrEnum):
    """The outcome of a business-entry lookup."""

    FOUND = "found"
    NOT_FOUND = "not_found"
    DEGRADED = "degraded"
    BACKEND_UNAVAILABLE = "backend_unavailable"


class LookupReason(StrEnum):
    """A machine-readable explanation for a lookup outcome."""

    NO_CAPABILITY_MATCH = "no_capability_match"
    NO_REALIZATION = "no_realization"
    REPO_MISMATCH = "repo_mismatch"
    MISSING_ENTRY_METADATA = "missing_entry_metadata"
    UNSUPPORTED_ENTRY_CATEGORY = "unsupported_entry_category"
    CORRUPT_GRAPH_DATA = "corrupt_graph_data"
    BACKEND_UNAVAILABLE = "backend_unavailable"


EndpointPairs: TypeAlias = tuple[tuple[str, str], ...]  # noqa: UP040 - public B1 contract spelling

_ENDPOINT_FIELDS: dict[str, tuple[str, ...]] = {
    "http_api": ("route", "method"),
    "rpc_service": ("route", "method"),
    "scheduled": ("cron", "schedule"),
    "mq_consumer": ("topic",),
    "event_handler": ("event",),
}


def endpoint_from_metadata(category: str | None, raw_metadata: str | None) -> tuple[EndpointPairs, LookupReason | None]:
    """Extract a normalized endpoint only from fields valid for an entry category."""
    if not isinstance(raw_metadata, str) or not raw_metadata.strip():
        return (), LookupReason.MISSING_ENTRY_METADATA

    try:
        metadata = json.loads(raw_metadata)
    except json.JSONDecodeError:
        return (), LookupReason.MISSING_ENTRY_METADATA
    if not isinstance(metadata, dict):
        return (), LookupReason.MISSING_ENTRY_METADATA

    if not isinstance(category, str) or category not in _ENDPOINT_FIELDS:
        return (), LookupReason.UNSUPPORTED_ENTRY_CATEGORY

    pairs = tuple(
        sorted(
            (key, value.strip())
            for key in _ENDPOINT_FIELDS[category]
            if isinstance(value := metadata.get(key), str) and value.strip()
        )
    )
    if not pairs:
        return (), LookupReason.MISSING_ENTRY_METADATA
    return pairs, None


def _validate_required_strings(values: Mapping[str, object]) -> None:
    for field_name, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a nonblank string")


def _validate_optional_string(field_name: str, value: str | None) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or None")


def _validate_file_path(value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError("file_path must be a nonblank string or None")


def _validate_lines(start_line: int | None, end_line: int | None) -> None:
    for field_name, value in (("start_line", start_line), ("end_line", end_line)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            raise ValueError(f"{field_name} must be a positive integer or None")
    if start_line is not None and end_line is not None and end_line < start_line:
        raise ValueError("end_line must be greater than or equal to start_line")


def _normalize_score(field_name: str, value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number in [0.0, 1.0] or None")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be a finite number in [0.0, 1.0] or None")
    return normalized


def _normalize_endpoint(endpoint: EndpointPairs | Mapping[str, str]) -> EndpointPairs:
    if isinstance(endpoint, Mapping):
        pairs = tuple(endpoint.items())
    elif isinstance(endpoint, tuple):
        pairs = endpoint
    else:
        raise ValueError("endpoint must be a mapping or tuple of string pairs")

    normalized_pairs: list[tuple[str, str]] = []
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise ValueError("endpoint pairs must contain exactly a key and value")
        key, value = pair
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip():
            raise ValueError("endpoint keys and values must be nonblank strings")
        normalized_pairs.append((key.strip(), value.strip()))

    if len({key for key, _ in normalized_pairs}) != len(normalized_pairs):
        raise ValueError("endpoint keys must be unique")
    return tuple(sorted(normalized_pairs))


@dataclass(frozen=True)
class RawBusinessEntry:
    """Raw, repository-scoped realization data read from the graph."""

    capability_id: str
    capability_name: str
    capability_domain: str
    capability_repo_id: str
    code_entity_id: str
    code_entity_repo_id: str
    entry_name: str
    entry_category: str | None
    file_path: str | None
    start_line: int | None
    end_line: int | None
    entry_metadata: str | None

    def __post_init__(self) -> None:
        _validate_required_strings(
            {
                "capability_id": self.capability_id,
                "capability_name": self.capability_name,
                "capability_domain": self.capability_domain,
                "capability_repo_id": self.capability_repo_id,
                "code_entity_id": self.code_entity_id,
                "code_entity_repo_id": self.code_entity_repo_id,
                "entry_name": self.entry_name,
            }
        )
        _validate_optional_string("entry_category", self.entry_category)
        _validate_file_path(self.file_path)
        _validate_optional_string("entry_metadata", self.entry_metadata)
        _validate_lines(self.start_line, self.end_line)


@dataclass(frozen=True)
class BusinessEntryEvidence:
    """Validated, serializable entry evidence returned by business lookup."""

    repo_id: str
    capability_id: str
    capability_name: str
    capability_domain: str
    capability_score: float | None
    code_entity_id: str
    entry_name: str
    entry_category: str | None
    file_path: str | None
    start_line: int | None
    end_line: int | None
    endpoint: EndpointPairs | Mapping[str, str]
    extraction_confidence: float | None

    def __post_init__(self) -> None:
        _validate_required_strings(
            {
                "repo_id": self.repo_id,
                "capability_id": self.capability_id,
                "capability_name": self.capability_name,
                "capability_domain": self.capability_domain,
                "code_entity_id": self.code_entity_id,
                "entry_name": self.entry_name,
            }
        )
        _validate_optional_string("entry_category", self.entry_category)
        _validate_file_path(self.file_path)
        _validate_lines(self.start_line, self.end_line)
        object.__setattr__(self, "capability_score", _normalize_score("capability_score", self.capability_score))
        object.__setattr__(
            self, "extraction_confidence", _normalize_score("extraction_confidence", self.extraction_confidence)
        )
        object.__setattr__(self, "endpoint", _normalize_endpoint(self.endpoint))

    def endpoint_dict(self) -> dict[str, str]:
        """Return a fresh mutable endpoint mapping."""
        return dict(self.endpoint)

    def to_dict(self) -> dict[str, object]:
        """Return a fresh snake-case representation suitable for serialization."""
        return {
            "repo_id": self.repo_id,
            "capability_id": self.capability_id,
            "capability_name": self.capability_name,
            "capability_domain": self.capability_domain,
            "capability_score": self.capability_score,
            "code_entity_id": self.code_entity_id,
            "entry_name": self.entry_name,
            "entry_category": self.entry_category,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "endpoint": self.endpoint_dict(),
            "extraction_confidence": self.extraction_confidence,
        }


@dataclass(frozen=True)
class BusinessEntryLookupResult:
    """Mechanically valid public result for a business-entry lookup."""

    status: LookupStatus
    evidences: tuple[BusinessEntryEvidence, ...]
    reasons: tuple[LookupReason, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, LookupStatus):
            raise ValueError("status must be a LookupStatus")

        evidences = tuple(self.evidences)
        reasons = tuple(self.reasons)
        if any(not isinstance(evidence, BusinessEntryEvidence) for evidence in evidences):
            raise ValueError("evidences must contain only BusinessEntryEvidence values")
        if any(not isinstance(reason, LookupReason) for reason in reasons):
            raise ValueError("reasons must contain only LookupReason values")
        object.__setattr__(self, "evidences", evidences)
        object.__setattr__(self, "reasons", reasons)

        if self.status is LookupStatus.FOUND:
            valid = bool(evidences) and not reasons
        elif self.status is LookupStatus.NOT_FOUND:
            valid = (
                not evidences
                and bool(reasons)
                and all(reason in {LookupReason.NO_CAPABILITY_MATCH, LookupReason.NO_REALIZATION} for reason in reasons)
            )
        elif self.status is LookupStatus.DEGRADED:
            valid = bool(reasons) and LookupReason.BACKEND_UNAVAILABLE not in reasons
        else:
            valid = not evidences and reasons == (LookupReason.BACKEND_UNAVAILABLE,)
        if not valid:
            raise ValueError("status, evidences, and reasons violate lookup-result invariants")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation of the lookup result."""
        return {
            "status": self.status.value,
            "evidences": [evidence.to_dict() for evidence in self.evidences],
            "reasons": [reason.value for reason in self.reasons],
        }
