"""Pure assembly of repository facts into public business-entry lookup results."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ontoagent.domain.business_entry import (
    BusinessEntryEvidence,
    BusinessEntryLookupResult,
    LookupReason,
    LookupStatus,
    endpoint_from_metadata,
)
from ontoagent.pipeline.business_entry_repository import RepositoryLookup


@dataclass(frozen=True)
class CapabilityCandidate:
    """A capability retrieved by semantic search and its cosine distance."""

    capability_id: str
    cosine_distance: float

    def __post_init__(self) -> None:
        if not _is_nonblank_string(self.capability_id):
            raise ValueError("capability_id must be a nonblank string")
        object.__setattr__(self, "cosine_distance", _normalize_cosine_distance(self.cosine_distance))


def capability_score_from_cosine_distance(distance: float) -> float:
    """Map cosine distance to a non-probabilistic, clipped similarity score.

    This is an interpretable monotonic mapping, not a calibrated probability:
    ``max(0.0, min(1.0, 1.0 - distance))``.
    """
    normalized = _normalize_cosine_distance(distance)
    return max(0.0, min(1.0, 1.0 - normalized))


def assemble_business_entry_lookup(
    repo_id: str,
    candidates: Sequence[CapabilityCandidate],
    lookup: RepositoryLookup,
) -> BusinessEntryLookupResult:
    """Assemble validated graph facts and capability candidates into a public result."""
    if not _is_nonblank_string(repo_id):
        raise ValueError("repo_id must be a nonblank string")
    if type(lookup) is not RepositoryLookup:
        raise ValueError("lookup must be a RepositoryLookup")

    if lookup.entries == () and lookup.reasons == (LookupReason.BACKEND_UNAVAILABLE,):
        return BusinessEntryLookupResult(LookupStatus.BACKEND_UNAVAILABLE, (), lookup.reasons)
    if LookupReason.BACKEND_UNAVAILABLE in lookup.reasons:
        raise ValueError("BACKEND_UNAVAILABLE must be the only reason on an empty lookup")

    candidate_distances = _candidate_distances(candidates)
    reasons = _stable_reasons(lookup.reasons)
    evidence_by_candidate: dict[str, list[BusinessEntryEvidence]] = {
        candidate_id: [] for candidate_id in candidate_distances
    }
    seen_evidence_keys: set[tuple[str, str]] = set()

    for raw in lookup.entries:
        if raw.capability_repo_id != repo_id or raw.code_entity_repo_id != repo_id:
            _append_reason(reasons, LookupReason.REPO_MISMATCH)
            continue
        distance = candidate_distances.get(raw.capability_id)
        if distance is None:
            _append_reason(reasons, LookupReason.CORRUPT_GRAPH_DATA)
            continue

        evidence_key = (raw.capability_id, raw.code_entity_id)
        if evidence_key in seen_evidence_keys:
            continue
        seen_evidence_keys.add(evidence_key)

        endpoint, metadata_reason = endpoint_from_metadata(raw.entry_category, raw.entry_metadata)
        if metadata_reason is not None:
            _append_reason(reasons, metadata_reason)
        if raw.file_path is None:
            _append_reason(reasons, LookupReason.MISSING_ENTRY_METADATA)
        evidence_by_candidate[raw.capability_id].append(
            BusinessEntryEvidence(
                repo_id=repo_id,
                capability_id=raw.capability_id,
                capability_name=raw.capability_name,
                capability_domain=raw.capability_domain,
                capability_score=capability_score_from_cosine_distance(distance),
                code_entity_id=raw.code_entity_id,
                entry_name=raw.entry_name,
                entry_category=raw.entry_category,
                file_path=raw.file_path,
                start_line=raw.start_line,
                end_line=raw.end_line,
                endpoint=endpoint,
                extraction_confidence=None,
            )
        )

    evidences = tuple(evidence for group in evidence_by_candidate.values() for evidence in group)
    if not evidences and not reasons:
        _append_reason(
            reasons, LookupReason.NO_CAPABILITY_MATCH if not candidate_distances else LookupReason.NO_REALIZATION
        )

    status = _status_for(evidences, reasons)
    return BusinessEntryLookupResult(status, evidences, tuple(reasons))


def _candidate_distances(candidates: Sequence[CapabilityCandidate]) -> dict[str, float]:
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise ValueError("candidates must be a non-string Sequence of CapabilityCandidate values")

    distances: dict[str, float] = {}
    for candidate in candidates:
        if not isinstance(candidate, CapabilityCandidate):
            raise ValueError("candidates must contain only CapabilityCandidate values")
        previous = distances.get(candidate.capability_id)
        if previous is None or candidate.cosine_distance < previous:
            distances[candidate.capability_id] = candidate.cosine_distance
    return distances


def _status_for(evidences: tuple[BusinessEntryEvidence, ...], reasons: list[LookupReason]) -> LookupStatus:
    if evidences:
        return LookupStatus.FOUND if not reasons else LookupStatus.DEGRADED
    if all(reason in {LookupReason.NO_CAPABILITY_MATCH, LookupReason.NO_REALIZATION} for reason in reasons):
        return LookupStatus.NOT_FOUND
    return LookupStatus.DEGRADED


def _normalize_cosine_distance(distance: object) -> float:
    if isinstance(distance, bool) or not isinstance(distance, (int, float)):
        raise ValueError("cosine_distance must be a finite number in [0.0, 2.0]")
    normalized = float(distance)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 2.0:
        raise ValueError("cosine_distance must be a finite number in [0.0, 2.0]")
    return normalized


def _is_nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _stable_reasons(reasons: tuple[LookupReason, ...]) -> list[LookupReason]:
    result: list[LookupReason] = []
    for reason in reasons:
        _append_reason(result, reason)
    return result


def _append_reason(reasons: list[LookupReason], reason: LookupReason) -> None:
    if reason not in reasons:
        reasons.append(reason)
