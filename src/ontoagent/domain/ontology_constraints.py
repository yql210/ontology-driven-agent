from __future__ import annotations

from dataclasses import dataclass, field

from ontoagent.domain.constraints import GuardLevel


@dataclass
class ConstraintFieldDescriptor:
    """描述实体字段上的约束语义。由 ONTOLOGY_CONSTRAINT_REGISTRY 注册。

    Attributes:
        field_name: 字段名 (e.g. "sensitivity")
        value_mapping: 字段值 → 约束级别的映射
    """
    field_name: str
    value_mapping: dict[str, GuardLevel] = field(default_factory=dict)
