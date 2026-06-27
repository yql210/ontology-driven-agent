"""数据溯源与可信度工具。"""

from __future__ import annotations

from datetime import UTC, datetime

from ontoagent.domain.exceptions import SchemaValidationError

# 来源类型
PROVENANCE_SOURCES = frozenset(
    {
        "ast_parser",  # AST 解析器（结构关系，置信度 1.0）
        "llm_extraction",  # LLM 提取（语义关系，置信度 0.7-0.95）
        "clustering",  # 模块聚类
        "manual",  # 手动添加
        "imported",  # 外部导入
    }
)


def validate_confidence(value: float) -> None:
    """校验置信度在 [0.0, 1.0] 范围。"""
    if not isinstance(value, (int, float)) or not (0.0 <= value <= 1.0):
        raise SchemaValidationError(f"confidence must be a float in [0.0, 1.0], got {value!r}")


def validate_provenance_source(source: str) -> None:
    """校验来源类型。"""
    if source not in PROVENANCE_SOURCES:
        raise SchemaValidationError(f"provenance_source must be one of {PROVENANCE_SOURCES}, got '{source}'")


def add_provenance(
    properties: dict | None = None,
    *,
    source: str = "ast_parser",
    confidence: float = 1.0,
    extracted_at: str | None = None,
) -> dict:
    """给 properties 添加溯源字段。

    Args:
        properties: 原始属性字典（可为 None）。
        source: 来源类型，见 PROVENANCE_SOURCES。
        confidence: 置信度 [0.0, 1.0]。
        extracted_at: 提取时间（ISO 8601），不传则生成当前时间。

    Returns:
        带溯源字段的新属性字典。
    """
    validate_provenance_source(source)
    # clamp confidence
    confidence = max(0.0, min(1.0, float(confidence)))
    props = dict(properties) if properties else {}
    props["provenance_source"] = source
    props["confidence"] = confidence
    props["extracted_at"] = extracted_at or datetime.now(UTC).isoformat()
    return props


def clamp_confidence(value: float | None, default: float = 0.8) -> float:
    """将值 clamp 到 [0.0, 1.0]，None 用默认值。"""
    if value is None:
        return default
    return max(0.0, min(1.0, float(value)))
