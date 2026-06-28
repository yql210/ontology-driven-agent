"""Constraint framework types — extracted from schema.py to stay under 800-line limit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class GuardLevel(StrEnum):
    """约束级别"""

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


@dataclass
class GuardDecision:
    """Guard 决策结果"""

    level: GuardLevel
    reason: str
    details: dict | None = None


@dataclass
class TraversalConstraint:
    """通用遍历约束 — 不绑定任何业务域。沿关系链收集属性值并映射为约束级别。"""

    name: str  # 约束名称 (e.g. "data_sensitivity")
    source_label: str  # 起始实体标签 (e.g. "CodeEntity")
    relation_chain: list[str]  # 关系链 (e.g. ["PROCESSES_DATA"])
    target_label: str  # 目标实体标签 (e.g. "DataAsset")
    collect_property: str  # 收集的属性名 (e.g. "sensitivity")
    value_mapping: dict[str, GuardLevel]  # 属性值→约束级别映射
    aggregation: Literal["max", "min", "exists"] = "max"  # 多路径聚合
    ontology_source: str = ""  # 溯源信息: "DataAsset.sensitivity"
