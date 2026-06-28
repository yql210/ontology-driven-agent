from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Backward-compatible re-exports: constraint types moved to domain/constraints.py
from ontoagent.domain.constraints import GuardDecision, GuardLevel, TraversalConstraint  # noqa: F401
from ontoagent.domain.exceptions import ConstraintViolationError, SchemaValidationError


@dataclass
class CodeEntity:
    """代码实体：函数、类、接口、模块或文件。

    Attributes:
        name: 实体名称（非空）。
        entity_type: 实体类型，必须是 function/class/interface/module/file/enum/record/field 之一。
        id: UUID v4 标识符，自动生成。
        file_path: 源文件路径（可选）。
        start_line: 起始行号（可选）。
        end_line: 结束行号（可选）。
        source: 源代码片段（可选）。
        language: 编程语言（可选）。
        docstring: 文档字符串（可选）。
        parameters: 参数列表，JSON 格式字符串如 '["self", "x: int"]'（可选）。
        created_at: ISO 8601 格式的时间戳，自动生成。
    """

    name: str
    entity_type: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    source: str | None = None
    language: str | None = None
    docstring: str | None = None
    parameters: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    entry_category: str | None = None
    entry_metadata: str | None = None
    business_process: str | None = None
    business_priority: str | None = None
    business_lifecycle: str | None = None
    business_owner: str | None = None

    VALID_ENTITY_TYPES = {"function", "class", "interface", "module", "file", "enum", "record", "field"}
    VALID_ENTRY_CATEGORIES = frozenset({"http_api", "rpc_service", "scheduled", "mq_consumer", "event_handler"})

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("CodeEntity.name cannot be empty")
        if self.entity_type not in self.VALID_ENTITY_TYPES:
            raise SchemaValidationError(
                f"CodeEntity.entity_type must be one of {self.VALID_ENTITY_TYPES}, got '{self.entity_type}'"
            )
        if self.entry_category is not None and self.entry_category not in self.VALID_ENTRY_CATEGORIES:
            raise SchemaValidationError(
                f"CodeEntity.entry_category must be one of {self.VALID_ENTRY_CATEGORIES}, got '{self.entry_category}'"
            )


@dataclass
class ConceptEntity:
    """概念实体：业务概念、设计模式、API契约、数据模型或流程。

    Attributes:
        name: 实体名称（非空）。
        entity_type: 实体类型，必须是 business_concept/design_pattern/api_contract/data_model/process 之一。
        id: UUID v4 标识符，自动生成。
        description: 概念描述（可选）。
        aliases: 别名列表（可选）。
        created_at: ISO 8601 格式的时间戳，自动生成。
    """

    name: str
    entity_type: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str | None = None
    aliases: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    VALID_ENTITY_TYPES = {
        "business_concept",
        "design_pattern",
        "api_contract",
        "data_model",
        "process",
        "message_topic",
    }

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("ConceptEntity.name cannot be empty")
        if self.entity_type not in self.VALID_ENTITY_TYPES:
            raise SchemaValidationError(
                f"ConceptEntity.entity_type must be one of {self.VALID_ENTITY_TYPES}, got '{self.entity_type}'"
            )


@dataclass
class DataAsset:
    """数据资产实体：敏感数据、合规数据等。

    Attributes:
        name: 资产名称（非空）。
        description: 资产描述。
        sensitivity: 敏感级别，必须是 public/internal/confidential/restricted 之一。
        data_type: 数据类型，必须是 pii/financial/operational/credentials 之一。
        aliases: 别名列表（可选）。
        id: UUID v4 标识符，自动生成。
        created_at: ISO 8601 格式的时间戳，自动生成。
    """

    name: str
    description: str
    sensitivity: str
    data_type: str
    aliases: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    VALID_SENSITIVITIES = {"public", "internal", "confidential", "restricted"}
    VALID_DATA_TYPES = {"pii", "financial", "operational", "credentials"}

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("DataAsset.name cannot be empty")
        if self.sensitivity not in self.VALID_SENSITIVITIES:
            raise SchemaValidationError(
                f"DataAsset.sensitivity must be one of {self.VALID_SENSITIVITIES}, got '{self.sensitivity}'"
            )
        if self.data_type not in self.VALID_DATA_TYPES:
            raise SchemaValidationError(
                f"DataAsset.data_type must be one of {self.VALID_DATA_TYPES}, got '{self.data_type}'"
            )


@dataclass
class DocEntity:
    """文档实体：README、模块文档、API文档、注释、Wiki或架构文档。

    Attributes:
        name: 实体名称（非空）。
        entity_type: 实体类型，必须是 readme/module_doc/api_doc/comment/wiki/architecture_doc 之一。
        id: UUID v4 标识符，自动生成。
        content: 文档内容（可选）。
        file_path: 文件路径（可选）。
        language: 文档语言/格式（可选）。
        created_at: ISO 8601 格式的时间戳，自动生成。
    """

    name: str
    entity_type: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content: str | None = None
    file_path: str | None = None
    language: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    VALID_ENTITY_TYPES = {
        "readme",
        "module_doc",
        "api_doc",
        "comment",
        "wiki",
        "architecture_doc",
    }

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("DocEntity.name cannot be empty")
        if self.entity_type not in self.VALID_ENTITY_TYPES:
            raise SchemaValidationError(
                f"DocEntity.entity_type must be one of {self.VALID_ENTITY_TYPES}, got '{self.entity_type}'"
            )


@dataclass
class ResourceEntity:
    """资源实体：图片、图表、PDF、配置、Schema文件或日志。

    Attributes:
        name: 实体名称（非空）。
        entity_type: 实体类型，必须是 image/diagram/pdf/config/schema_file/log 之一。
        id: UUID v4 标识符，自动生成。
        file_path: 文件路径（可选）。
        mime_type: MIME类型（可选）。
        created_at: ISO 8601 格式的时间戳，自动生成。
    """

    name: str
    entity_type: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str | None = None
    mime_type: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    VALID_ENTITY_TYPES = {"image", "diagram", "pdf", "config", "schema_file", "log"}

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("ResourceEntity.name cannot be empty")
        if self.entity_type not in self.VALID_ENTITY_TYPES:
            raise SchemaValidationError(
                f"ResourceEntity.entity_type must be one of {self.VALID_ENTITY_TYPES}, got '{self.entity_type}'"
            )


@dataclass
class ModuleEntity:
    """功能模块（聚类结果）。

    Attributes:
        name: 模块名称（非空）。
        id: UUID v4 标识符，自动生成。
        description: 模块描述（可选）。
        created_at: ISO 8601 格式的时间戳，自动生成。
    """

    name: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("ModuleEntity.name cannot be empty")


@dataclass
class ChangeSetEntity:
    """变更集。

    Attributes:
        commit_hash: Git commit 哈希值。
        message: 提交信息。
        author: 提交作者。
        id: UUID v4 标识符，自动生成。
        branch: 分支名称。
        files_changed: 变更文件列表。
        committed_at: 提交时间（ISO 8601）。
        created_at: 记录创建时间，自动生成。
    """

    commit_hash: str
    message: str
    author: str = "unknown"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    branch: str = "main"
    files_changed: list[str] = field(default_factory=list)
    committed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.commit_hash or not self.commit_hash.strip():
            raise SchemaValidationError("ChangeSetEntity.commit_hash cannot be empty")
        if not self.message or not self.message.strip():
            raise SchemaValidationError("ChangeSetEntity.message cannot be empty")


@dataclass
class LogEntity:
    """日志实体 — 来自外部日志系统。

    Attributes:
        name: 日志条目标识（非空）。
        level: 日志级别，必须是 ERROR/WARN/INFO/DEBUG 之一。
        message: 日志消息（非空）。
        source_service: 来源服务名称。
        id: UUID v4 标识符，自动生成。
        timestamp: 日志时间戳。
        pattern: 匹配的日志模式（可选）。
        stack_trace: 堆栈跟踪（可选）。
        created_at: 记录创建时间，自动生成。
    """

    name: str
    level: str
    message: str
    source_service: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    pattern: str | None = None
    stack_trace: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    VALID_LEVELS = {"ERROR", "WARN", "INFO", "DEBUG"}

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("LogEntity.name cannot be empty")
        if self.level not in self.VALID_LEVELS:
            raise SchemaValidationError(f"LogEntity.level must be one of {self.VALID_LEVELS}, got '{self.level}'")
        if not self.message or not self.message.strip():
            raise SchemaValidationError("LogEntity.message cannot be empty")


@dataclass
class AlertEntity:
    """告警实体 — 来自监控系统 Webhook。

    Attributes:
        name: 告警标识（非空）。
        alert_type: 告警类型，必须是 error_spike/latency/service_down/custom 之一。
        severity: 严重程度，必须是 CRITICAL/HIGH/MEDIUM/LOW 之一。
        description: 告警描述。
        source_service: 来源服务名称。
        id: UUID v4 标识符，自动生成。
        timestamp: 告警时间戳。
        resolved: 是否已解决。
        related_log_ids: 关联日志 ID 列表。
        created_at: 记录创建时间，自动生成。
    """

    name: str
    alert_type: str
    severity: str
    description: str
    source_service: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    resolved: bool = False
    related_log_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    VALID_ALERT_TYPES = {"error_spike", "latency", "service_down", "custom"}
    VALID_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("AlertEntity.name cannot be empty")
        if self.alert_type not in self.VALID_ALERT_TYPES:
            raise SchemaValidationError(
                f"AlertEntity.alert_type must be one of {self.VALID_ALERT_TYPES}, got '{self.alert_type}'"
            )
        if self.severity not in self.VALID_SEVERITIES:
            raise SchemaValidationError(
                f"AlertEntity.severity must be one of {self.VALID_SEVERITIES}, got '{self.severity}'"
            )


@dataclass
class ServiceEntity:
    """服务实体 — 运行时服务实例。

    Attributes:
        name: 服务名称（非空）。
        version: 服务版本。
        status: 服务状态，必须是 running/stopped/degraded 之一。
        id: UUID v4 标识符，自动生成。
        endpoint: 服务端点（可选）。
        code_entity_id: 关联的 CodeEntity ID（可选）。
        config: 服务配置。
        created_at: 记录创建时间，自动生成。
    """

    name: str
    version: str
    status: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    endpoint: str | None = None
    code_entity_id: str | None = None
    config: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    capability_label: str | None = None
    team: str | None = None

    VALID_STATUSES = {"running", "stopped", "degraded"}

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("ServiceEntity.name cannot be empty")
        if self.status not in self.VALID_STATUSES:
            raise SchemaValidationError(
                f"ServiceEntity.status must be one of {self.VALID_STATUSES}, got '{self.status}'"
            )


@dataclass
class ComplianceItem:
    """合规要求实体：GDPR、SOX、PCI-DSS 等法规要求。

    Attributes:
        name: 合规要求标识（非空）。
        description: 合规要求描述（非空）。
        regulation: 适用法规/标准。
        severity: 严重程度，必须是 critical/high/medium/low 之一。
        requirement: 具体要求内容。
        id: UUID v4 标识符，自动生成。
        created_at: ISO 8601 格式的时间戳，自动生成。
    """

    name: str
    description: str
    regulation: str
    severity: str
    requirement: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    VALID_SEVERITIES = {"critical", "high", "medium", "low"}

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("ComplianceItem.name cannot be empty")
        if self.severity not in self.VALID_SEVERITIES:
            raise SchemaValidationError(
                f"ComplianceItem.severity must be one of {self.VALID_SEVERITIES}, got '{self.severity}'"
            )


VALID_ENTITY_LABELS: frozenset[str] = frozenset(
    {
        "CodeEntity",
        "ConceptEntity",
        "DocEntity",
        "ResourceEntity",
        "ModuleEntity",
        "ChangeSetEntity",
        "LogEntity",
        "AlertEntity",
        "ServiceEntity",
        "DataAsset",
        "ComplianceItem",
    }
)


VALID_RELATION_TYPES = frozenset(
    {
        "calls",
        "extends",
        "implements",
        "imports",
        "contains",
        "semantic_impact",
        "describes",
        "illustrates",
        "derived_from",
        "changed_in",
        "affects",
        "triggered_by",
        "logs_from",
        "runs_as",
        "service_depends_on",
        "processes_data",
        "subject_to",
        "governed_by",
        "calls_service",
        "publishes_to",
        "consumed_by",
    }
)

RELATION_TYPE_TO_NEO4J: dict[str, str] = {
    "calls": "CALLS",
    "extends": "EXTENDS",
    "implements": "IMPLEMENTS",
    "imports": "IMPORTS",
    "contains": "CONTAINS",
    "semantic_impact": "SEMANTIC_IMPACT",
    "describes": "DESCRIBES",
    "illustrates": "ILLUSTRATES",
    "derived_from": "DERIVED_FROM",
    "changed_in": "CHANGED_IN",
    "affects": "AFFECTS",
    "triggered_by": "TRIGGERED_BY",
    "logs_from": "LOGS_FROM",
    "runs_as": "RUNS_AS",
    "service_depends_on": "SERVICE_DEPENDS_ON",
    "processes_data": "PROCESSES_DATA",
    "subject_to": "SUBJECT_TO",
    "governed_by": "GOVERNED_BY",
    "calls_service": "CALLS_SERVICE",
    "publishes_to": "PUBLISHES_TO",
    "consumed_by": "CONSUMED_BY",
}


@dataclass
class Relation:
    """实体间关系。

    Attributes:
        source_id: 源实体 ID。
        target_id: 目标实体 ID。
        relation_type: 关系类型，必须是 VALID_RELATION_TYPES 之一。
        id: UUID v4 标识符，自动生成。
        weight: 关系权重，范围 [0, 1]。
        metadata: 额外元数据。
        created_at: ISO 8601 格式的时间戳，自动生成。
    """

    source_id: str
    target_id: str
    relation_type: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    weight: float = 1.0
    metadata: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        """校验字段。"""
        if self.relation_type not in VALID_RELATION_TYPES:
            raise SchemaValidationError(
                f"Relation.relation_type must be one of {VALID_RELATION_TYPES}, got '{self.relation_type}'"
            )
        if not (0 <= self.weight <= 1):
            raise SchemaValidationError(f"Relation.weight must be in [0, 1], got {self.weight}")


@dataclass
class RelationConstraint:
    """单条关系的本体约束。"""

    domain: str | set[str]  # 源实体类型（如 "CodeEntity" 或 {"CodeEntity", "ConceptEntity"}）
    range: str | set[str]  # 目标实体类型
    description: str = ""  # 约束说明


RELATION_CONSTRAINTS: dict[str, RelationConstraint] = {
    # --- 结构关系 (AST) ---
    "calls": RelationConstraint(
        domain="CodeEntity",
        range="CodeEntity",
        description="函数/方法调用关系",
    ),
    "extends": RelationConstraint(
        domain="CodeEntity",
        range="CodeEntity",
        description="类继承关系（支持多继承）",
    ),
    "implements": RelationConstraint(
        domain="CodeEntity",
        range="CodeEntity",
        description="接口实现关系",
    ),
    "imports": RelationConstraint(
        domain="CodeEntity",
        range="CodeEntity",
        description="模块导入关系",
    ),
    "contains": RelationConstraint(
        domain={"CodeEntity", "ModuleEntity"},
        range={"CodeEntity", "DocEntity", "ResourceEntity"},
        description="包含关系（模块/文件包含子元素）",
    ),
    # --- 语义关系 (LLM) ---
    "semantic_impact": RelationConstraint(
        domain={"CodeEntity", "ConceptEntity"},
        range={"CodeEntity", "ConceptEntity"},
        description="语义影响关系",
    ),
    "describes": RelationConstraint(
        domain={"DocEntity", "ConceptEntity"},
        range={"CodeEntity", "ConceptEntity"},
        description="文档/概念描述代码实体",
    ),
    "illustrates": RelationConstraint(
        domain={"ResourceEntity", "DocEntity"},
        range={"CodeEntity", "ConceptEntity", "ModuleEntity"},
        description="资源/文档图示实体",
    ),
    "derived_from": RelationConstraint(
        domain={"ConceptEntity", "CodeEntity"},
        range="ConceptEntity",
        description="概念派生关系（代码实体可实现/派生自某概念）",
    ),
    # --- 变更关系 ---
    "changed_in": RelationConstraint(
        domain={"CodeEntity", "DocEntity", "ResourceEntity"},
        range="ChangeSetEntity",
        description="实体在变更集中被修改",
    ),
    "affects": RelationConstraint(
        domain="ChangeSetEntity",
        range={"CodeEntity", "DocEntity", "ResourceEntity", "ConceptEntity"},
        description="变更集影响的实体",
    ),
    # --- 运维关系 ---
    "triggered_by": RelationConstraint(
        domain="AlertEntity",
        range="LogEntity",
        description="告警由日志触发",
    ),
    "logs_from": RelationConstraint(
        domain="LogEntity",
        range="ServiceEntity",
        description="日志来源于服务",
    ),
    "runs_as": RelationConstraint(
        domain="CodeEntity",
        range="ServiceEntity",
        description="代码部署为服务",
    ),
    "service_depends_on": RelationConstraint(
        domain="ServiceEntity",
        range="ServiceEntity",
        description="服务间依赖",
    ),
    "processes_data": RelationConstraint(
        domain="CodeEntity",
        range="DataAsset",
        description="代码处理了数据资产",
    ),
    "subject_to": RelationConstraint(
        domain="CodeEntity",
        range="ComplianceItem",
        description="代码受合规要求约束",
    ),
    "governed_by": RelationConstraint(
        domain="DataAsset",
        range="ComplianceItem",
        description="数据资产受合规要求约束",
    ),
    # --- 跨服务通信关系 ---
    "calls_service": RelationConstraint(
        domain="CodeEntity",
        range="ServiceEntity",
        description="代码调用了外部服务",
    ),
    "publishes_to": RelationConstraint(
        domain="CodeEntity",
        range="ConceptEntity",
        description="代码发布消息到消息主题",
    ),
    "consumed_by": RelationConstraint(
        domain="ConceptEntity",
        range="CodeEntity",
        description="消息主题被代码消费",
    ),
}


def validate_relation_constraint(
    relation_type: str,
    source_label: str,
    target_label: str,
) -> None:
    """校验关系是否满足本体 domain/range 约束。

    未知关系类型（不在 RELATION_CONSTRAINTS 中）不校验，保证向后兼容。

    Args:
        relation_type: 关系类型名称（snake_case）。
        source_label: 源节点 Neo4j 标签（如 "CodeEntity"）。
        target_label: 目标节点 Neo4j 标签。

    Raises:
        ConstraintViolationError: domain 或 range 不满足约束。
    """
    constraint = RELATION_CONSTRAINTS.get(relation_type)
    if constraint is None:
        return  # 未知关系类型，不校验

    # domain 校验
    domain = constraint.domain
    allowed_domain = {domain} if isinstance(domain, str) else domain
    if source_label not in allowed_domain:
        raise ConstraintViolationError(
            f"关系 '{relation_type}' 的源实体必须是 {allowed_domain}，实际为 '{source_label}'"
        )

    # range 校验
    range_val = constraint.range
    allowed_range = {range_val} if isinstance(range_val, str) else range_val
    if target_label not in allowed_range:
        raise ConstraintViolationError(
            f"关系 '{relation_type}' 的目标实体必须是 {allowed_range}，实际为 '{target_label}'"
        )


# ============================================================
# Ontology Constraint Registry — Layer 1 auto-derivation source
# ============================================================
# 格式: "{EntityLabel}.{field_name}" → ConstraintFieldDescriptor

from ontoagent.domain.ontology_constraints import ConstraintFieldDescriptor  # noqa: E402

ONTOLOGY_CONSTRAINT_REGISTRY: dict[str, ConstraintFieldDescriptor] = {
    "DataAsset.sensitivity": ConstraintFieldDescriptor(
        field_name="sensitivity",
        value_mapping={
            "restricted": GuardLevel.BLOCK,
            "confidential": GuardLevel.WARN,
            "internal": GuardLevel.ALLOW,
            "public": GuardLevel.ALLOW,
        },
    ),
    "ComplianceItem.severity": ConstraintFieldDescriptor(
        field_name="severity",
        value_mapping={
            "critical": GuardLevel.BLOCK,
            "high": GuardLevel.WARN,
            "medium": GuardLevel.ALLOW,
            "low": GuardLevel.ALLOW,
        },
    ),
    "CodeEntity.entry_category": ConstraintFieldDescriptor(
        field_name="entry_category",
        value_mapping={
            "http_api": GuardLevel.WARN,
            "rpc_service": GuardLevel.WARN,
            "scheduled": GuardLevel.ALLOW,
            "mq_consumer": GuardLevel.ALLOW,
            "event_handler": GuardLevel.ALLOW,
        },
    ),
}
