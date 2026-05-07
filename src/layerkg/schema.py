from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from layerkg.exceptions import SchemaValidationError


@dataclass
class CodeEntity:
    """代码实体：函数、类、接口、模块或文件。

    Attributes:
        name: 实体名称（非空）。
        entity_type: 实体类型，必须是 function/class/interface/module/file 之一。
        id: UUID v4 标识符，自动生成。
        file_path: 源文件路径（可选）。
        start_line: 起始行号（可选）。
        end_line: 结束行号（可选）。
        source: 源代码片段（可选）。
        language: 编程语言（可选）。
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
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    VALID_ENTITY_TYPES = {"function", "class", "interface", "module", "file"}

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.name or not self.name.strip():
            raise SchemaValidationError("CodeEntity.name cannot be empty")
        if self.entity_type not in self.VALID_ENTITY_TYPES:
            raise SchemaValidationError(
                f"CodeEntity.entity_type must be one of {self.VALID_ENTITY_TYPES}, got '{self.entity_type}'"
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
