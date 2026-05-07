from __future__ import annotations


class LayerKGError(Exception):
    """LayerKG 基础异常。"""


class SchemaValidationError(LayerKGError):
    """Schema 校验失败。"""


class StoreError(LayerKGError):
    """存储操作失败。"""


class EmbeddingError(LayerKGError):
    """嵌入向量生成失败。"""
