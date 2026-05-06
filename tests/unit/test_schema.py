from __future__ import annotations

import uuid

import pytest

from layerkg.exceptions import SchemaValidationError
from layerkg.schema import CodeEntity, ConceptEntity, DocEntity, ResourceEntity


@pytest.mark.unit
def test_code_entity_creation():
    """Test creating a CodeEntity with valid data."""
    entity = CodeEntity(name="foo", entity_type="function")
    assert entity.name == "foo"
    assert entity.entity_type == "function"
    assert entity.id is not None


@pytest.mark.unit
def test_code_entity_id_is_uuid():
    """Test that CodeEntity generates a valid UUID v4 id."""
    entity = CodeEntity(name="bar", entity_type="class")
    assert uuid.UUID(entity.id).version == 4


@pytest.mark.unit
@pytest.mark.parametrize(
    "entity_type",
    ["function", "class", "interface", "module", "file"],
)
def test_code_entity_valid_types(entity_type: str):
    """Test that CodeEntity accepts all valid entity types."""
    entity = CodeEntity(name="test", entity_type=entity_type)
    assert entity.entity_type == entity_type


@pytest.mark.unit
def test_code_entity_invalid_type_raises():
    """Test that CodeEntity rejects invalid entity types."""
    with pytest.raises(SchemaValidationError):
        CodeEntity(name="test", entity_type="invalid")


@pytest.mark.unit
def test_code_entity_empty_name_raises():
    """Test that CodeEntity rejects empty name."""
    with pytest.raises(SchemaValidationError):
        CodeEntity(name="", entity_type="function")


@pytest.mark.unit
def test_code_entity_optional_fields():
    """Test CodeEntity with all optional fields."""
    entity = CodeEntity(
        name="full_func",
        entity_type="function",
        file_path="/path/to/file.py",
        start_line=10,
        end_line=20,
        source="def full_func():\n    pass",
        language="python",
    )
    assert entity.file_path == "/path/to/file.py"
    assert entity.start_line == 10
    assert entity.end_line == 20
    assert entity.source == "def full_func():\n    pass"
    assert entity.language == "python"


@pytest.mark.unit
def test_code_entity_created_at_is_iso_string():
    """Test that CodeEntity created_at is a valid ISO format string."""
    entity = CodeEntity(name="test", entity_type="function")
    assert entity.created_at is not None
    # Check ISO format: 2024-01-01T12:00:00Z
    assert "T" in entity.created_at


# ConceptEntity Tests
@pytest.mark.unit
@pytest.mark.parametrize(
    "entity_type",
    ["business_concept", "design_pattern", "api_contract", "data_model", "process"],
)
def test_concept_entity_valid_types(entity_type: str):
    """Test that ConceptEntity accepts all valid entity types."""
    entity = ConceptEntity(name="test_concept", entity_type=entity_type)
    assert entity.entity_type == entity_type


@pytest.mark.unit
def test_concept_entity_invalid_type_raises():
    """Test that ConceptEntity rejects invalid entity types."""
    with pytest.raises(SchemaValidationError):
        ConceptEntity(name="test", entity_type="invalid")


@pytest.mark.unit
def test_concept_entity_empty_name_raises():
    """Test that ConceptEntity rejects empty name."""
    with pytest.raises(SchemaValidationError):
        ConceptEntity(name="", entity_type="business_concept")


@pytest.mark.unit
def test_concept_entity_optional_fields():
    """Test ConceptEntity with optional fields."""
    entity = ConceptEntity(
        name="User",
        entity_type="business_concept",
        description="A user in the system",
        aliases=["Account", "Member"],
    )
    assert entity.description == "A user in the system"
    assert entity.aliases == ["Account", "Member"]


# DocEntity Tests
@pytest.mark.unit
@pytest.mark.parametrize(
    "entity_type",
    ["readme", "module_doc", "api_doc", "comment", "wiki", "architecture_doc"],
)
def test_doc_entity_valid_types(entity_type: str):
    """Test that DocEntity accepts all valid entity types."""
    entity = DocEntity(name="test_doc", entity_type=entity_type)
    assert entity.entity_type == entity_type


@pytest.mark.unit
def test_doc_entity_invalid_type_raises():
    """Test that DocEntity rejects invalid entity types."""
    with pytest.raises(SchemaValidationError):
        DocEntity(name="test", entity_type="invalid")


@pytest.mark.unit
def test_doc_entity_empty_name_raises():
    """Test that DocEntity rejects empty name."""
    with pytest.raises(SchemaValidationError):
        DocEntity(name="", entity_type="readme")


@pytest.mark.unit
def test_doc_entity_optional_fields():
    """Test DocEntity with optional fields."""
    entity = DocEntity(
        name="API Reference",
        entity_type="api_doc",
        content="# API Documentation",
        file_path="/docs/api.md",
        language="markdown",
    )
    assert entity.content == "# API Documentation"
    assert entity.file_path == "/docs/api.md"
    assert entity.language == "markdown"


# ResourceEntity Tests
@pytest.mark.unit
@pytest.mark.parametrize(
    "entity_type",
    ["image", "diagram", "pdf", "config", "schema_file", "log"],
)
def test_resource_entity_valid_types(entity_type: str):
    """Test that ResourceEntity accepts all valid entity types."""
    entity = ResourceEntity(name="test_resource", entity_type=entity_type)
    assert entity.entity_type == entity_type


@pytest.mark.unit
def test_resource_entity_invalid_type_raises():
    """Test that ResourceEntity rejects invalid entity types."""
    with pytest.raises(SchemaValidationError):
        ResourceEntity(name="test", entity_type="invalid")


@pytest.mark.unit
def test_resource_entity_empty_name_raises():
    """Test that ResourceEntity rejects empty name."""
    with pytest.raises(SchemaValidationError):
        ResourceEntity(name="", entity_type="image")


@pytest.mark.unit
def test_resource_entity_optional_fields():
    """Test ResourceEntity with optional fields."""
    entity = ResourceEntity(
        name="architecture.png",
        entity_type="image",
        file_path="/docs/architecture.png",
        mime_type="image/png",
    )
    assert entity.file_path == "/docs/architecture.png"
    assert entity.mime_type == "image/png"
