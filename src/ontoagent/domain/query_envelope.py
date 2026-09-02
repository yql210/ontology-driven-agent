"""Pre-query identity and blocking envelope contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ontoagent.domain.business_entry import BusinessEntryLookupResult


class QueryEnvelopeStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class QueryBlockReason(StrEnum):
    MISSING_BINDING = "missing_binding"
    BINDING_INVALID = "binding_invalid"
    REPO_MISMATCH = "repo_mismatch"
    GENERATION_MISMATCH = "generation_mismatch"
    SOURCE_REVISION_MISMATCH = "source_revision_mismatch"
    GRAPH_NAMESPACE_MISMATCH = "graph_namespace_mismatch"
    VECTOR_NAMESPACE_MISMATCH = "vector_namespace_mismatch"
    SCHEMA_VERSION_MISMATCH = "schema_version_mismatch"
    INDEX_UNAVAILABLE = "index_unavailable"
    INDEX_DEGRADED = "index_degraded"
    MANIFEST_UNAVAILABLE = "manifest_unavailable"
    MANIFEST_INTEGRITY_FAILURE = "manifest_integrity_failure"


def _optional_text(name: str, value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a nonblank string or None")
    return value.strip()


@dataclass(frozen=True)
class QueryEnvelope:
    status: QueryEnvelopeStatus
    repo_id: str
    build_id: str | None
    generation_id: str | None
    source_revision: str | None
    reasons: tuple[QueryBlockReason, ...]
    result: BusinessEntryLookupResult | None

    def __post_init__(self) -> None:
        if type(self.status) is not QueryEnvelopeStatus:
            raise ValueError("status must be a QueryEnvelopeStatus")
        if type(self.repo_id) is not str or not self.repo_id.strip():
            raise ValueError("repo_id must be a nonblank string")
        object.__setattr__(self, "repo_id", self.repo_id.strip())
        for name in ("build_id", "generation_id", "source_revision"):
            object.__setattr__(self, name, _optional_text(name, getattr(self, name)))
        if type(self.reasons) is not tuple or any(type(reason) is not QueryBlockReason for reason in self.reasons):
            raise ValueError("reasons must be a tuple of QueryBlockReason")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("reasons must not contain duplicates")
        if self.reasons != tuple(reason for reason in QueryBlockReason if reason in self.reasons):
            raise ValueError("reasons must use QueryBlockReason declaration order")
        if self.status is QueryEnvelopeStatus.BLOCKED:
            if self.result is not None or not self.reasons:
                raise ValueError("blocked envelope requires reasons and no result")
        elif (
            type(self.result) is not BusinessEntryLookupResult
            or self.reasons
            or self.build_id is None
            or self.generation_id is None
            or self.source_revision is None
        ):
            raise ValueError("ready envelope requires result, identities, and no reasons")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "repo_id": self.repo_id,
            "build_id": self.build_id,
            "generation_id": self.generation_id,
            "source_revision": self.source_revision,
            "reasons": [reason.value for reason in self.reasons],
            "result": self.result.to_dict() if self.result is not None else None,
        }
