"""ShapeEvaluator — 编译 path → 查 Neo4j → 评估 ConstraintExpr。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ontoagent.domain.shapes import ConstraintExpr, ConstraintShape, Operation, Severity
from ontoagent.execution.path_compiler import PathCompiler
from ontoagent.execution.shape_registry import ShapeRegistry

logger = logging.getLogger(__name__)

__all__ = ["ShapeEvaluator", "ShapeResult"]

_VALID_FIELD_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass
class ShapeResult:
    """单条 Shape 在某次 evaluate 中的判定结果。

    Attributes:
        shape: 被评估的 ConstraintShape。
        severity: 该 Shape 声明的处置级别（取自 shape.severity）。
        evidence: 判定证据：collected values / operator / expected / path / entity_id。
        triggered: 约束条件是否命中。
    """

    shape: ConstraintShape
    severity: Severity
    evidence: dict[str, Any]
    triggered: bool


class ShapeEvaluator:
    """对 entity × capabilities 执行所有匹配 Shape 的评估。

    流程：
        1. 对每个 (label, capability) 调 ``registry.get_shapes()`` 取匹配 Shape（按 priority 降序）。
        2. 对每条 Shape：编译 path → 在 graph_store 上查询 → 收集 field 值 → 评估 ConstraintExpr。
        3. 返回所有 ShapeResult（包含未触发的），供上层做 severity 聚合。
    """

    def __init__(self, shape_registry: ShapeRegistry, graph_store: Any) -> None:
        """初始化评估器。

        Args:
            shape_registry: 已加载 Shape 的注册表。
            graph_store: GraphStore 实现（或任何带 ``query(cypher, params)`` 方法的对象）。
        """
        self._registry = shape_registry
        self._graph_store = graph_store
        self._compiler = PathCompiler()

    def evaluate(self, entity: dict[str, Any], capabilities: list[Operation]) -> list[ShapeResult]:
        """对 entity 在指定 capabilities 下评估所有匹配的 Shape。

        Args:
            entity: 实体字典，需包含 ``id`` 与 ``labels``（Neo4j 标签列表）。
                ``unless_field`` 也从该字典读取。
            capabilities: 待评估的操作列表。

        Returns:
            ShapeResult 列表（包含未触发的）。顺序：capability → label → priority 降序。
        """
        results: list[ShapeResult] = []
        entity_id = str(entity.get("id", ""))
        labels: list[str] = entity.get("labels") or []

        for operation in capabilities:
            for label in labels:
                for shape in self._registry.get_shapes(label, operation):
                    results.append(self._evaluate_shape(shape, entity, entity_id))
        return results

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _evaluate_shape(
        self,
        shape: ConstraintShape,
        entity: dict[str, Any],
        entity_id: str,
    ) -> ShapeResult:
        cypher = self._build_query(shape)
        rows = self._graph_store.query(cypher, {"entity_id": entity_id})
        values: list[Any] = [row.get("val") for row in rows if row.get("val") is not None]

        triggered = self._should_trigger(shape.constraint, values, entity)

        return ShapeResult(
            shape=shape,
            severity=shape.severity,
            evidence={
                "field": shape.constraint.field,
                "values": values,
                "operator": shape.constraint.operator,
                "expected": shape.constraint.value,
                "path": shape.path.raw,
                "entity_id": entity_id,
            },
            triggered=triggered,
        )

    def _build_query(self, shape: ConstraintShape) -> str:
        """编译 PathExpression 并追加 WHERE + RETURN，组成完整 Cypher。

        Args:
            shape: 待查询的 Shape。

        Returns:
            形如 ``MATCH (n)-[:REL]->(collected:Label) WHERE n.id = $entity_id
            RETURN collected.field AS val`` 的查询语句。

        Raises:
            ValueError: 当 constraint.field 不是合法 Neo4j 标识符时。
        """
        field_name = shape.constraint.field
        if not _VALID_FIELD_RE.match(field_name):
            raise ValueError(f"非法 constraint.field: {field_name!r}")

        if shape.path.is_self():
            return f"MATCH (n) WHERE n.id = $entity_id RETURN n.{field_name} AS val"

        match_clause, _ = self._compiler.compile(shape.path)
        return f"{match_clause} WHERE n.id = $entity_id RETURN collected.{field_name} AS val"

    @staticmethod
    def _should_trigger(
        constraint: ConstraintExpr,
        values: list[Any],
        entity: dict[str, Any],
    ) -> bool:
        """评估 ConstraintExpr 是否命中。

        unless 优先级最高：命中豁免则直接返回 False。
        若 values 为空（路径无匹配或字段全为 None），约束视为未命中。
        """
        if ShapeEvaluator._unless_matches(constraint, entity):
            return False
        if not values:
            return False

        op = constraint.operator
        if op == "in":
            expected = constraint.value if isinstance(constraint.value, list) else [constraint.value]
            return any(v in expected for v in values)
        if op == "not_in":
            expected = constraint.value if isinstance(constraint.value, list) else [constraint.value]
            return all(v not in expected for v in values)
        if op == "equals":
            return any(v == constraint.value for v in values)
        if op == "not_equals":
            return all(v != constraint.value for v in values)
        if op == "exists":
            return len(values) > 0
        if op == ">":
            return any(v > constraint.value for v in values)
        if op == "<":
            return any(v < constraint.value for v in values)
        if op == ">=":
            return any(v >= constraint.value for v in values)
        if op == "<=":
            return any(v <= constraint.value for v in values)

        logger.warning("未知 operator %r (field=%r) — 视为未触发", op, constraint.field)
        return False

    @staticmethod
    def _unless_matches(constraint: ConstraintExpr, entity: dict[str, Any]) -> bool:
        """检查 unless_field/unless_value 是否命中豁免。

        - unless_value 为列表时，actual 命中其中任一值即豁免。
        - 否则按相等比较。
        - unless_field 为 None 或 entity 中无该字段时，不豁免。
        """
        if not constraint.unless_field:
            return False
        actual = entity.get(constraint.unless_field)
        if actual is None:
            return False
        expected = constraint.unless_value
        if isinstance(expected, list):
            return actual in expected
        return actual == expected
