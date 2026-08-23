"""Business-entry vector index health facts for a single build."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BusinessEntryIndexStatus(StrEnum):
    """Availability of the business-entry index produced by this build."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class IndexHealthReason(StrEnum):
    """Stable reasons explaining business-entry index health."""

    BUILD_ABORTED = "build_aborted"
    CAPABILITY_EXTRACTION_FAILED = "capability_extraction_failed"
    NO_ELIGIBLE_ENTRIES = "no_eligible_entries"
    NO_REALIZATIONS_SUBMITTED = "no_realizations_submitted"
    CAPABILITY_VECTOR_WRITE_FAILED = "capability_vector_write_failed"


def _validate_count(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-bool nonnegative int")


def _health_policy(
    *,
    aborted: bool,
    capability_extraction_failed: bool,
    eligible_entries_seen: int,
    realized_by_submitted: int,
    capability_vectors_failed: int,
) -> tuple[BusinessEntryIndexStatus, tuple[IndexHealthReason, ...]]:
    """Return the canonical health status and reasons for observable build facts."""
    if aborted:
        return BusinessEntryIndexStatus.UNAVAILABLE, (IndexHealthReason.BUILD_ABORTED,)

    reasons: list[IndexHealthReason] = []
    if capability_extraction_failed:
        reasons.append(IndexHealthReason.CAPABILITY_EXTRACTION_FAILED)
    if not capability_extraction_failed and eligible_entries_seen == 0:
        reasons.append(IndexHealthReason.NO_ELIGIBLE_ENTRIES)
    if not capability_extraction_failed and eligible_entries_seen > 0 and realized_by_submitted == 0:
        reasons.append(IndexHealthReason.NO_REALIZATIONS_SUBMITTED)
    if capability_vectors_failed > 0:
        reasons.append(IndexHealthReason.CAPABILITY_VECTOR_WRITE_FAILED)

    canonical_reasons = tuple(reasons)
    if not canonical_reasons:
        return BusinessEntryIndexStatus.HEALTHY, canonical_reasons
    if canonical_reasons == (IndexHealthReason.NO_ELIGIBLE_ENTRIES,):
        return BusinessEntryIndexStatus.UNAVAILABLE, canonical_reasons
    return BusinessEntryIndexStatus.DEGRADED, canonical_reasons


@dataclass(frozen=True)
class VectorWriteOutcome:
    """The submitted and observed outcome of one vector write operation."""

    submitted: int
    confirmed: int
    failed: int

    def __post_init__(self) -> None:
        _validate_count("submitted", self.submitted)
        _validate_count("confirmed", self.confirmed)
        _validate_count("failed", self.failed)
        if self.submitted != self.confirmed + self.failed:
            raise ValueError("submitted must equal confirmed + failed")


@dataclass(frozen=True)
class BusinessEntryIndexHealth:
    """Evidence-backed business-entry index health for one build."""

    eligible_entries_seen: int
    capabilities_merged: int
    realized_by_submitted: int
    capability_vectors_submitted: int
    capability_vectors_confirmed: int
    capability_vectors_failed: int
    status: BusinessEntryIndexStatus
    reasons: tuple[IndexHealthReason, ...]

    def __post_init__(self) -> None:
        for name in (
            "eligible_entries_seen",
            "capabilities_merged",
            "realized_by_submitted",
            "capability_vectors_submitted",
            "capability_vectors_confirmed",
            "capability_vectors_failed",
        ):
            _validate_count(name, getattr(self, name))
        VectorWriteOutcome(
            self.capability_vectors_submitted,
            self.capability_vectors_confirmed,
            self.capability_vectors_failed,
        )
        if not isinstance(self.status, BusinessEntryIndexStatus):
            raise ValueError("status must be a BusinessEntryIndexStatus")
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, IndexHealthReason) for reason in self.reasons
        ):
            raise ValueError("reasons must be a tuple of IndexHealthReason")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("reasons must not contain duplicates")
        ordered_reasons = tuple(reason for reason in IndexHealthReason if reason in self.reasons)
        if self.reasons != ordered_reasons:
            raise ValueError("reasons must use IndexHealthReason declaration order")

        expected_status, expected_reasons = _health_policy(
            aborted=IndexHealthReason.BUILD_ABORTED in self.reasons,
            capability_extraction_failed=IndexHealthReason.CAPABILITY_EXTRACTION_FAILED in self.reasons,
            eligible_entries_seen=self.eligible_entries_seen,
            realized_by_submitted=self.realized_by_submitted,
            capability_vectors_failed=self.capability_vectors_failed,
        )
        if (self.status, self.reasons) != (expected_status, expected_reasons):
            raise ValueError("status and reasons must match index health policy")

    @classmethod
    def from_build_facts(
        cls,
        *,
        aborted: bool,
        capability_extraction_failed: bool,
        eligible_entries_seen: int,
        capabilities_merged: int,
        realized_by_submitted: int,
        capability_vector_outcome: VectorWriteOutcome,
    ) -> BusinessEntryIndexHealth:
        """Build health from stage facts using the sole health-rule owner."""
        for name, value in (
            ("eligible_entries_seen", eligible_entries_seen),
            ("capabilities_merged", capabilities_merged),
            ("realized_by_submitted", realized_by_submitted),
        ):
            _validate_count(name, value)
        if not isinstance(capability_vector_outcome, VectorWriteOutcome):
            raise ValueError("capability_vector_outcome must be a VectorWriteOutcome")

        status, reasons = _health_policy(
            aborted=aborted,
            capability_extraction_failed=capability_extraction_failed,
            eligible_entries_seen=eligible_entries_seen,
            realized_by_submitted=realized_by_submitted,
            capability_vectors_failed=capability_vector_outcome.failed,
        )

        return cls(
            eligible_entries_seen=eligible_entries_seen,
            capabilities_merged=capabilities_merged,
            realized_by_submitted=realized_by_submitted,
            capability_vectors_submitted=capability_vector_outcome.submitted,
            capability_vectors_confirmed=capability_vector_outcome.confirmed,
            capability_vectors_failed=capability_vector_outcome.failed,
            status=status,
            reasons=reasons,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""
        return {
            "eligible_entries_seen": self.eligible_entries_seen,
            "capabilities_merged": self.capabilities_merged,
            "realized_by_submitted": self.realized_by_submitted,
            "capability_vectors_submitted": self.capability_vectors_submitted,
            "capability_vectors_confirmed": self.capability_vectors_confirmed,
            "capability_vectors_failed": self.capability_vectors_failed,
            "status": self.status.value,
            "reasons": [reason.value for reason in self.reasons],
        }
