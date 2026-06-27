from __future__ import annotations

import uuid

import pytest

from layerkg.domain.exceptions import SchemaValidationError
from layerkg.domain.schema import (
    RELATION_TYPE_TO_NEO4J,
    VALID_RELATION_TYPES,
    ChangeSetEntity,
    CodeEntity,
    ModuleEntity,
    Relation,
)

# ── ModuleEntity ──────────────────────────────────────────


@pytest.mark.unit
def test_module_entity_creation():
    """Test creating a ModuleEntity."""
    entity = ModuleEntity(name="auth")
    assert entity.name == "auth"
    assert entity.id is not None
    assert uuid.UUID(entity.id).version == 4


@pytest.mark.unit
def test_module_entity_empty_name_raises():
    """Test that ModuleEntity rejects empty name."""
    with pytest.raises(SchemaValidationError):
        ModuleEntity(name="")


@pytest.mark.unit
def test_module_entity_with_description():
    """Test ModuleEntity with optional description."""
    entity = ModuleEntity(name="auth", description="Authentication module")
    assert entity.description == "Authentication module"


# ── ChangeSetEntity ───────────────────────────────────────


@pytest.mark.unit
def test_changeset_entity_creation():
    """Test creating a ChangeSetEntity with defaults."""
    cs = ChangeSetEntity(commit_hash="abc1234", message="init commit")
    assert cs.commit_hash == "abc1234"
    assert cs.message == "init commit"
    assert cs.author == "unknown"
    assert cs.branch == "main"
    assert cs.files_changed == []
    assert uuid.UUID(cs.id).version == 4


@pytest.mark.unit
def test_changeset_entity_empty_hash_raises():
    """Test that ChangeSetEntity rejects empty commit_hash."""
    with pytest.raises(SchemaValidationError):
        ChangeSetEntity(commit_hash="", message="msg")


@pytest.mark.unit
def test_changeset_entity_empty_message_raises():
    """Test that ChangeSetEntity rejects empty message."""
    with pytest.raises(SchemaValidationError):
        ChangeSetEntity(commit_hash="abc", message="")


@pytest.mark.unit
def test_changeset_entity_full_fields():
    """Test ChangeSetEntity with all fields."""
    cs = ChangeSetEntity(
        commit_hash="deadbeef",
        message="fix bug",
        author="alice",
        branch="develop",
        files_changed=["a.py", "b.py"],
    )
    assert cs.author == "alice"
    assert cs.branch == "develop"
    assert cs.files_changed == ["a.py", "b.py"]


# ── Relation ──────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "relation_type",
    sorted(VALID_RELATION_TYPES),
)
def test_relation_valid_types(relation_type: str):
    """Test that Relation accepts all 11 valid relation types."""
    r = Relation(source_id="a", target_id="b", relation_type=relation_type)
    assert r.relation_type == relation_type


@pytest.mark.unit
def test_relation_invalid_type_raises():
    """Test that Relation rejects invalid relation_type."""
    with pytest.raises(SchemaValidationError):
        Relation(source_id="a", target_id="b", relation_type="invalid")


@pytest.mark.unit
def test_relation_weight_valid():
    """Test Relation accepts weight in [0, 1]."""
    r0 = Relation(source_id="a", target_id="b", relation_type="calls", weight=0.0)
    assert r0.weight == 0.0
    r1 = Relation(source_id="a", target_id="b", relation_type="calls", weight=1.0)
    assert r1.weight == 1.0
    r05 = Relation(source_id="a", target_id="b", relation_type="calls", weight=0.5)
    assert r05.weight == 0.5


@pytest.mark.unit
def test_relation_weight_out_of_range():
    """Test that Relation rejects weight outside [0, 1]."""
    with pytest.raises(SchemaValidationError):
        Relation(source_id="a", target_id="b", relation_type="calls", weight=1.5)
    with pytest.raises(SchemaValidationError):
        Relation(source_id="a", target_id="b", relation_type="calls", weight=-0.1)


@pytest.mark.unit
def test_relation_defaults():
    """Test Relation default values."""
    r = Relation(source_id="a", target_id="b", relation_type="calls")
    assert r.weight == 1.0
    assert r.metadata == {}
    assert uuid.UUID(r.id).version == 4


# ── RELATION_TYPE_TO_NEO4J 映射 ───────────────────────────


@pytest.mark.unit
def test_relation_type_to_neo4j_completeness():
    """Test that every VALID_RELATION_TYPE has a Neo4j mapping."""
    for rt in VALID_RELATION_TYPES:
        assert rt in RELATION_TYPE_TO_NEO4J, f"Missing Neo4j mapping for '{rt}'"


@pytest.mark.unit
def test_relation_type_to_neo4j_values():
    """Test that Neo4j mapping values are UPPER_CASE."""
    for key, value in RELATION_TYPE_TO_NEO4J.items():
        assert value == value.upper(), f"Mapping '{key}': '{value}' is not upper case"


# ── CodeEntity: Java 类型支持 ─────────────────────────────────


@pytest.mark.unit
def test_code_entity_accepts_enum():
    """Test that CodeEntity accepts 'enum' entity_type (Java support)."""
    e = CodeEntity(name="Color", entity_type="enum")
    assert e.entity_type == "enum"


@pytest.mark.unit
def test_code_entity_accepts_record():
    """Test that CodeEntity accepts 'record' entity_type (Java support)."""
    e = CodeEntity(name="Point", entity_type="record")
    assert e.entity_type == "record"


@pytest.mark.unit
def test_code_entity_accepts_field():
    """Test that CodeEntity accepts 'field' entity_type (Java support)."""
    e = CodeEntity(name="x", entity_type="field")
    assert e.entity_type == "field"
