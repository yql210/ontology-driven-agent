"""Capability identity contract tests."""

from __future__ import annotations

import hashlib

import pytest

from ontoagent.domain import schema
from ontoagent.domain.schema import CapabilityEntity, CodeEntity
from ontoagent.pipeline.builder_utils import capability_entity_to_dict


@pytest.mark.unit
def test_stable_capability_id_is_stable_for_same_identity() -> None:
    """The same repo, entry, and normalized name derive the same ID."""
    first = schema.stable_capability_id("repo-a", "entry-1", "process_payment")
    second = schema.stable_capability_id("repo-a", "entry-1", "process_payment")

    assert first == second
    assert len(first) == 32


@pytest.mark.unit
@pytest.mark.parametrize(
    ("repo_id", "entry_code_entity_id", "normalized_name"),
    [
        ("repo-b", "entry-1", "process_payment"),
        ("repo-a", "entry-2", "process_payment"),
        ("repo-a", "entry-1", "process_refund"),
    ],
)
def test_stable_capability_id_changes_when_any_identity_component_changes(
    repo_id: str, entry_code_entity_id: str, normalized_name: str
) -> None:
    """Each identity component isolates capabilities from the others."""
    baseline = schema.stable_capability_id("repo-a", "entry-1", "process_payment")

    assert schema.stable_capability_id(repo_id, entry_code_entity_id, normalized_name) != baseline


@pytest.mark.unit
def test_capability_entity_derives_id_from_repo_scoped_entry_identity() -> None:
    """New capability construction uses the repo-scoped entry identity when present."""
    entity = CapabilityEntity(
        name="process_payment",
        business_domain="payment",
        description="Process a payment.",
        repo_id="repo-a",
        entry_code_entity_id="entry-1",
    )

    assert entity.id == schema.stable_capability_id("repo-a", "entry-1", "process_payment")


@pytest.mark.unit
def test_capability_entity_normalizes_name_before_deriving_scoped_id() -> None:
    """Case and surrounding whitespace do not split an entry-scoped capability."""
    first = CapabilityEntity(
        name="Process_Payment",
        business_domain="payment",
        description="Process a payment.",
        repo_id="repo-a",
        entry_code_entity_id="entry-1",
    )
    second = CapabilityEntity(
        name=" process_payment ",
        business_domain="payment",
        description="Process a payment.",
        repo_id="repo-a",
        entry_code_entity_id="entry-1",
    )

    assert first.id == second.id


@pytest.mark.unit
def test_capability_entity_allows_legacy_id_without_identity_fields() -> None:
    """Legacy records can still be read without repo or entry identity fields."""
    entity = CapabilityEntity(
        name="process_payment",
        business_domain="payment",
        description="Process a payment.",
        id="legacy-capability-id",
    )

    assert entity.id == "legacy-capability-id"
    assert entity.repo_id == ""
    assert entity.entry_code_entity_id == ""


@pytest.mark.unit
def test_capability_entity_to_dict_preserves_non_empty_identity_fields() -> None:
    """Identity metadata is persisted only when it is available."""
    scoped_entity = CapabilityEntity(
        name="process_payment",
        business_domain="payment",
        description="Process a payment.",
        repo_id="repo-a",
        entry_code_entity_id="entry-1",
    )
    legacy_entity = CapabilityEntity(
        name="process_payment",
        business_domain="payment",
        description="Process a payment.",
        id="legacy-capability-id",
    )

    scoped_data = capability_entity_to_dict(scoped_entity)
    legacy_data = capability_entity_to_dict(legacy_entity)

    assert scoped_data["repo_id"] == "repo-a"
    assert scoped_data["entry_code_entity_id"] == "entry-1"
    assert "repo_id" not in legacy_data
    assert "entry_code_entity_id" not in legacy_data


@pytest.mark.unit
def test_code_entity_id_derivation_is_unchanged() -> None:
    """Capability identity work does not alter CodeEntity's stable ID contract."""
    entity = CodeEntity(
        name="process_payment",
        entity_type="function",
        repo_id="repo-a",
        file_path="src/payments.py",
        start_line=10,
        end_line=20,
    )
    expected = hashlib.sha256(b"repo-a|process_payment|function|src/payments.py|10|20").hexdigest()[:32]

    assert entity.id == expected
