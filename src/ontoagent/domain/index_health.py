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

        if aborted:
            reasons = (IndexHealthReason.BUILD_ABORTED,)
            status = BusinessEntryIndexStatus.UNAVAILABLE
        else:
            reasons_list: list[IndexHealthReason] = []
            if capability_extraction_failed:
                reasons_list.append(IndexHealthReason.CAPABILITY_EXTRACTION_FAILED)
            if not capability_extraction_failed and eligible_entries_seen == 0:
                reasons_list.append(IndexHealthReason.NO_ELIGIBLE_ENTRIES)
            if not capability_extraction_failed and eligible_entries_seen > 0 and realized_by_submitted == 0:
                reasons_list.append(IndexHealthReason.NO_REALIZATIONS_SUBMITTED)
            if capability_vector_outcome.failed > 0:
                reasons_list.append(IndexHealthReason.CAPABILITY_VECTOR_WRITE_FAILED)
            reasons = tuple(reasons_list)
            status = (
                BusinessEntryIndexStatus.HEALTHY
                if not reasons
                else BusinessEntryIndexStatus.UNAVAILABLE
                if reasons == (IndexHealthReason.NO_ELIGIBLE_ENTRIES,)
                else BusinessEntryIndexStatus.DEGRADED
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
