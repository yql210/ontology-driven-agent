from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ontoagent.domain.exceptions import ConstraintViolationError
from ontoagent.domain.schema import (
    GuardDecision,
    GuardLevel,
    TraversalConstraint,
    validate_relation_constraint,
)

if TYPE_CHECKING:
    from ontoagent.store.graph_store import GraphStore

logger = logging.getLogger(__name__)


class ConstraintEngine:
    """执行遍历约束检查。加载时校验 relation_chain 合法性。"""

    def __init__(self, graph_store: GraphStore, constraints: list[TraversalConstraint]) -> None:
        # 加载时逐跳校验每个 constraint 的 relation_chain
        for c in constraints:
            for rel_type in c.relation_chain:
                try:
                    # validate_relation_constraint expects snake_case relation types, but
                    # TraversalConstraint uses UPPER_SNAKE. Convert for validation.
                    rel_snake = rel_type.lower()
                    validate_relation_constraint(rel_snake, c.source_label, c.target_label)
                except ConstraintViolationError as exc:
                    raise ConstraintViolationError(
                        f"Invalid constraint '{c.name}': relation '{rel_type}' "
                        f"not valid from {c.source_label} to {c.target_label}: {exc}"
                    ) from exc

        self._graph_store = graph_store
        self._constraints = {c.name: c for c in constraints}

    def evaluate(self, entity_id: str, constraint_name: str) -> GuardDecision:
        """执行单个约束检查。

        Args:
            entity_id: 源实体 ID。
            constraint_name: 约束名称。

        Returns:
            GuardDecision 决策结果。
        """
        constraint = self._constraints.get(constraint_name)
        if not constraint:
            return GuardDecision(level=GuardLevel.ALLOW, reason=f"Unknown constraint: {constraint_name}")

        # 1. Get source entity
        entity = self._graph_store.get_node(entity_id)
        if not entity:
            return GuardDecision(level=GuardLevel.BLOCK, reason=f"Entity not found: {entity_id}")

        # 2. Traverse relation chain to collect target properties
        results = self._traverse(entity_id, constraint)

        # 3. Map properties to decisions and aggregate
        return self._aggregate(results, constraint)

    def _traverse(self, start_id: str, constraint: TraversalConstraint) -> list[dict]:
        """沿关系链遍历，返回目标实体的属性列表。

        Args:
            start_id: 起始实体 ID。
            constraint: 遍历约束。

        Returns:
            目标实体属性字典列表。
        """
        current_ids = [start_id]
        for i, rel_type in enumerate(constraint.relation_chain):
            next_ids: list[str] = []
            is_final = i == len(constraint.relation_chain) - 1
            target_filter = f":{constraint.target_label}" if is_final else ""

            for nid in current_ids:
                query = (
                    f"MATCH (a)-[:{rel_type}]->(b{target_filter}) "
                    f"WHERE a.id = $id "
                    f"RETURN b.id AS id, b.{constraint.collect_property} AS val"
                )
                try:
                    rows = self._graph_store.query(query, {"id": nid})
                    for row in rows:
                        if row.get("id"):
                            next_ids.append(row["id"])
                except Exception:
                    logger.debug("Traversal query failed for node %s, rel %s", nid, rel_type, exc_info=True)

            current_ids = next_ids
            if not current_ids:
                break

        # Collect properties on final targets
        results: list[dict] = []
        for nid in current_ids:
            node = self._graph_store.get_node(nid)
            if node:
                results.append(node)
        return results

    def _aggregate(self, results: list[dict], constraint: TraversalConstraint) -> GuardDecision:
        """聚合多个目标实体的约束级别。

        Args:
            results: 目标实体属性字典列表。
            constraint: 遍历约束。

        Returns:
            GuardDecision 聚合后的决策。
        """
        if not results:
            return GuardDecision(
                level=GuardLevel.ALLOW,
                reason=f"No {constraint.target_label} found via {constraint.relation_chain}",
            )

        levels: list[GuardLevel] = []
        reasons: list[str] = []
        for node in results:
            val = node.get(constraint.collect_property)
            if val is None:
                continue
            level = constraint.value_mapping.get(str(val), GuardLevel.ALLOW)
            levels.append(level)
            reasons.append(f"{node.get('name', 'unknown')}.{constraint.collect_property}={val} → {level.value}")

        if not levels:
            return GuardDecision(
                level=GuardLevel.ALLOW,
                reason=f"No {constraint.collect_property} found",
            )

        if constraint.aggregation == "max":
            # block > warn > allow
            priority = {GuardLevel.BLOCK: 3, GuardLevel.WARN: 2, GuardLevel.ALLOW: 1}
            max_level = max(levels, key=lambda level_val: priority.get(level_val, 0))
            return GuardDecision(level=max_level, reason="; ".join(reasons))
        elif constraint.aggregation == "exists":
            has_issue = any(level_val != GuardLevel.ALLOW for level_val in levels)
            return GuardDecision(
                level=GuardLevel.WARN if has_issue else GuardLevel.ALLOW,
                reason="; ".join(reasons),
            )

        return GuardDecision(level=GuardLevel.ALLOW, reason="; ".join(reasons))
