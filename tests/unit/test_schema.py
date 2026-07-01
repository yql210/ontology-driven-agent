from __future__ import annotations

import uuid

import pytest

from ontoagent.domain.exceptions import SchemaValidationError
from ontoagent.domain.schema import (
    RELATION_CONSTRAINTS,
    VALID_RELATION_TYPES,
    AlertEntity,
    CapabilityEntity,
    CodeEntity,
    ConceptEntity,
    DocEntity,
    LogEntity,
    ProcessEntity,
    ResourceEntity,
    ServiceEntity,
)


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
def test_code_entity_docstring_default_none():
    """Test CodeEntity docstring field defaults to None."""
    entity = CodeEntity(name="test", entity_type="function")
    assert entity.docstring is None


@pytest.mark.unit
def test_code_entity_parameters_default_none():
    """Test CodeEntity parameters field defaults to None."""
    entity = CodeEntity(name="test", entity_type="function")
    assert entity.parameters is None


@pytest.mark.unit
def test_code_entity_docstring_can_be_set():
    """Test CodeEntity docstring field can be set."""
    entity = CodeEntity(name="test", entity_type="function", docstring="A test function.")
    assert entity.docstring == "A test function."


@pytest.mark.unit
def test_code_entity_parameters_can_be_set():
    """Test CodeEntity parameters field can be set."""
    entity = CodeEntity(name="test", entity_type="function", parameters='["self", "x: int"]')
    assert entity.parameters == '["self", "x: int"]'


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


# --- LogEntity Tests ---


@pytest.mark.unit
def test_log_entity_valid():
    """Test creating a LogEntity with valid data."""
    entity = LogEntity(
        name="error-001",
        level="ERROR",
        message="Connection refused",
        source_service="api-gateway",
    )
    assert entity.name == "error-001"
    assert entity.level == "ERROR"
    assert entity.message == "Connection refused"
    assert entity.source_service == "api-gateway"
    assert entity.id is not None
    assert entity.pattern is None
    assert entity.stack_trace is None


@pytest.mark.unit
def test_log_entity_invalid_level():
    """Test that LogEntity rejects invalid level."""
    with pytest.raises(SchemaValidationError, match=r"LogEntity\.level"):
        LogEntity(name="log-1", level="FATAL", message="msg", source_service="svc")


@pytest.mark.unit
def test_log_entity_empty_message():
    """Test that LogEntity rejects empty message."""
    with pytest.raises(SchemaValidationError, match=r"LogEntity\.message"):
        LogEntity(name="log-1", level="ERROR", message="   ", source_service="svc")


# --- AlertEntity Tests ---


@pytest.mark.unit
def test_alert_entity_valid():
    """Test creating an AlertEntity with valid data."""
    entity = AlertEntity(
        name="alert-001",
        alert_type="error_spike",
        severity="HIGH",
        description="Error rate exceeded threshold",
        source_service="payment-service",
    )
    assert entity.name == "alert-001"
    assert entity.alert_type == "error_spike"
    assert entity.severity == "HIGH"
    assert entity.resolved is False
    assert entity.related_log_ids == []


@pytest.mark.unit
def test_alert_entity_invalid_severity():
    """Test that AlertEntity rejects invalid severity."""
    with pytest.raises(SchemaValidationError, match=r"AlertEntity\.severity"):
        AlertEntity(
            name="a1",
            alert_type="error_spike",
            severity="URGENT",
            description="d",
            source_service="svc",
        )


# --- ServiceEntity Tests ---


@pytest.mark.unit
def test_service_entity_valid():
    """Test creating a ServiceEntity with valid data."""
    entity = ServiceEntity(name="api-gateway", version="2.1.0", status="running")
    assert entity.name == "api-gateway"
    assert entity.version == "2.1.0"
    assert entity.status == "running"
    assert entity.endpoint is None
    assert entity.code_entity_id is None
    assert entity.config == {}


@pytest.mark.unit
def test_service_entity_invalid_status():
    """Test that ServiceEntity rejects invalid status."""
    with pytest.raises(SchemaValidationError, match=r"ServiceEntity\.status"):
        ServiceEntity(name="svc", version="1.0", status="crashed")


# --- New Relation Types Tests ---


@pytest.mark.unit
def test_new_relation_types_valid():
    """Test that new ops relation types are in VALID_RELATION_TYPES."""
    for rel_type in ("triggered_by", "logs_from", "runs_as", "service_depends_on"):
        assert rel_type in VALID_RELATION_TYPES, f"{rel_type} not in VALID_RELATION_TYPES"


@pytest.mark.unit
def test_new_relation_constraints_valid():
    """Test that new ops relations have constraints in RELATION_CONSTRAINTS."""
    for rel_type in ("triggered_by", "logs_from", "runs_as", "service_depends_on"):
        assert rel_type in RELATION_CONSTRAINTS, f"{rel_type} not in RELATION_CONSTRAINTS"

    assert RELATION_CONSTRAINTS["triggered_by"].domain == "AlertEntity"
    assert RELATION_CONSTRAINTS["triggered_by"].range == "LogEntity"
    assert RELATION_CONSTRAINTS["logs_from"].domain == "LogEntity"
    assert RELATION_CONSTRAINTS["logs_from"].range == "ServiceEntity"
    assert RELATION_CONSTRAINTS["runs_as"].domain == "CodeEntity"
    assert RELATION_CONSTRAINTS["runs_as"].range == "ServiceEntity"
    assert RELATION_CONSTRAINTS["service_depends_on"].domain == "ServiceEntity"
    assert RELATION_CONSTRAINTS["service_depends_on"].range == "ServiceEntity"


# =============================================================================
# V5 Phase 0 — CapabilityEntity & ProcessEntity (TDD RED)
# =============================================================================


@pytest.mark.unit
def test_capability_entity_creation():
    """Test creating a CapabilityEntity with required fields."""
    entity = CapabilityEntity(
        name="订单履约",
        business_domain="order",
        description="完成订单从创建到发货的全流程",
    )
    assert entity.name == "订单履约"
    assert entity.business_domain == "order"
    assert entity.id is not None


@pytest.mark.unit
def test_capability_entity_defaults():
    """CapabilityEntity optional fields have correct defaults."""
    entity = CapabilityEntity(name="test", business_domain="test", description="test")
    assert entity.input_contract == {}
    assert entity.output_contract == {}
    assert entity.preconditions == []
    assert entity.postconditions == []
    assert entity.effects == []
    assert entity.non_functional == {}
    assert entity.keywords == []
    assert entity.realized_by == []
    assert entity.version == "1"
    assert entity.enabled is True


@pytest.mark.unit
def test_capability_entity_name_required():
    """CapabilityEntity.name must not be empty."""
    with pytest.raises(SchemaValidationError):
        CapabilityEntity(name="", business_domain="test", description="test")


@pytest.mark.unit
def test_capability_entity_full_construction():
    """CapabilityEntity with all fields set."""
    entity = CapabilityEntity(
        name="库存校验",
        business_domain="inventory",
        description="校验商品库存是否满足订单数量",
        input_contract={"sku_id": "str", "quantity": "int"},
        output_contract={"available": "bool", "reason": "str"},
        preconditions=["sku_id 存在"],
        postconditions=["库存状态已查询"],
        effects=["返回库存可用性"],
        non_functional={"sync": True, "idempotent": True, "sla": "100ms"},
        keywords=["库存", "校验", "可用性"],
        version="1",
        enabled=True,
    )
    assert entity.input_contract == {"sku_id": "str", "quantity": "int"}
    assert entity.non_functional["sla"] == "100ms"
    assert "库存" in entity.keywords


@pytest.mark.unit
def test_process_entity_creation():
    """Test creating a ProcessEntity with required fields."""
    entity = ProcessEntity(
        name="订单履约流程",
        description="从下单到发货的完整业务流程",
    )
    assert entity.name == "订单履约流程"
    assert entity.id is not None


@pytest.mark.unit
def test_process_entity_defaults():
    """ProcessEntity optional fields have correct defaults."""
    entity = ProcessEntity(name="test", description="test")
    assert entity.steps == []
    assert entity.triggers == []
    assert entity.completion_criteria == []


@pytest.mark.unit
def test_process_entity_name_required():
    """ProcessEntity.name must not be empty."""
    with pytest.raises(SchemaValidationError):
        ProcessEntity(name="", description="test")


@pytest.mark.unit
def test_new_v5_relation_types_exist():
    """V5 新增的 6 条关系类型在 VALID_RELATION_TYPES + RELATION_CONSTRAINTS 中。"""
    from ontoagent.domain.schema import ONTOLOGY_RELATION_TYPES, RELATION_TYPE_TO_NEO4J

    v5_rels = ["produces", "consumes", "composes_into", "realized_by", "precedes", "equivalent_to"]
    for rel in v5_rels:
        assert rel in VALID_RELATION_TYPES, f"{rel} not in VALID_RELATION_TYPES"
        assert rel in RELATION_CONSTRAINTS, f"{rel} not in RELATION_CONSTRAINTS"
        assert rel in RELATION_TYPE_TO_NEO4J, f"{rel} not in RELATION_TYPE_TO_NEO4J"
        neo4j_name = RELATION_TYPE_TO_NEO4J[rel]
        assert neo4j_name in ONTOLOGY_RELATION_TYPES, f"{neo4j_name} not in ONTOLOGY_RELATION_TYPES"


@pytest.mark.unit
def test_capability_entity_in_valid_labels():
    """CapabilityEntity label is in VALID_ENTITY_LABELS."""
    from ontoagent.domain.schema import VALID_ENTITY_LABELS

    assert "CapabilityEntity" in VALID_ENTITY_LABELS
    assert "ProcessEntity" in VALID_ENTITY_LABELS
