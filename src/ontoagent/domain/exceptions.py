from __future__ import annotations


class OntoAgentError(Exception):
    """OntoAgent 基础异常。"""


class SchemaValidationError(OntoAgentError):
    """Schema 校验失败。"""


class ConstraintViolationError(OntoAgentError):
    """本体约束违反（domain/range 校验失败）。"""


class StoreError(OntoAgentError):
    """存储操作失败。"""


class EmbeddingError(OntoAgentError):
    """嵌入向量生成失败。"""


class ExtractionError(OntoAgentError):
    """语义关系提取失败。"""


class SchemaMigrationError(OntoAgentError):
    """Schema 迁移错误。"""
