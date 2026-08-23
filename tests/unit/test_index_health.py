from __future__ import annotations

import json

import pytest

from ontoagent.domain.index_health import (
    BusinessEntryIndexHealth,
    BusinessEntryIndexStatus,
    IndexHealthReason,
    VectorWriteOutcome,
)


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, -1, 1.5, "1"])
def test_vector_write_outcome_rejects_invalid_counts(value: object) -> None:
    with pytest.raises(ValueError):
        VectorWriteOutcome(value, 0, 0)  # type: ignore[arg-type]


@pytest.mark.unit
def test_vector_write_outcome_requires_additive_invariant() -> None:
    with pytest.raises(ValueError):
        VectorWriteOutcome(2, 1, 0)


@pytest.mark.unit
def test_index_health_factory_reports_healthy_json_safe_facts() -> None:
    health = BusinessEntryIndexHealth.from_build_facts(
        aborted=False,
        capability_extraction_failed=False,
        eligible_entries_seen=1,
        capabilities_merged=1,
        realized_by_submitted=1,
        capability_vector_outcome=VectorWriteOutcome(1, 1, 0),
    )

    assert health.status is BusinessEntryIndexStatus.HEALTHY
    assert health.reasons == ()
    assert health.to_dict() == {
        "eligible_entries_seen": 1,
        "capabilities_merged": 1,
        "realized_by_submitted": 1,
        "capability_vectors_submitted": 1,
        "capability_vectors_confirmed": 1,
        "capability_vectors_failed": 0,
        "status": "healthy",
        "reasons": [],
    }
    json.dumps(health.to_dict())


@pytest.mark.unit
def test_index_health_factory_applies_stable_reasons_and_statuses() -> None:
    unavailable = BusinessEntryIndexHealth.from_build_facts(
        aborted=False,
        capability_extraction_failed=False,
        eligible_entries_seen=0,
        capabilities_merged=0,
        realized_by_submitted=0,
        capability_vector_outcome=VectorWriteOutcome(0, 0, 0),
    )
    degraded = BusinessEntryIndexHealth.from_build_facts(
        aborted=False,
        capability_extraction_failed=True,
        eligible_entries_seen=1,
        capabilities_merged=0,
        realized_by_submitted=0,
        capability_vector_outcome=VectorWriteOutcome(2, 1, 1),
    )

    assert unavailable.status is BusinessEntryIndexStatus.UNAVAILABLE
    assert unavailable.reasons == (IndexHealthReason.NO_ELIGIBLE_ENTRIES,)
    assert degraded.status is BusinessEntryIndexStatus.DEGRADED
    assert degraded.reasons == (
        IndexHealthReason.CAPABILITY_EXTRACTION_FAILED,
        IndexHealthReason.CAPABILITY_VECTOR_WRITE_FAILED,
    )

    no_realization = BusinessEntryIndexHealth.from_build_facts(
        aborted=False,
        capability_extraction_failed=False,
        eligible_entries_seen=1,
        capabilities_merged=1,
        realized_by_submitted=0,
        capability_vector_outcome=VectorWriteOutcome(1, 0, 1),
    )
    assert no_realization.reasons == (
        IndexHealthReason.NO_REALIZATIONS_SUBMITTED,
        IndexHealthReason.CAPABILITY_VECTOR_WRITE_FAILED,
    )


@pytest.mark.unit
def test_index_health_aborted_overrides_all_other_reasons() -> None:
    health = BusinessEntryIndexHealth.from_build_facts(
        aborted=True,
        capability_extraction_failed=True,
        eligible_entries_seen=0,
        capabilities_merged=0,
        realized_by_submitted=0,
        capability_vector_outcome=VectorWriteOutcome(1, 0, 1),
    )

    assert health.status is BusinessEntryIndexStatus.UNAVAILABLE
    assert health.reasons == (IndexHealthReason.BUILD_ABORTED,)
