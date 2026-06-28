from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ontoagent.domain.schema import (
    RELATION_CONSTRAINTS,
    GuardDecision,
    GuardLevel,
    TraversalConstraint,
    validate_relation_constraint,
)

if TYPE_CHECKING:
    from ontoagent.store.graph_store import GraphStore

logger = logging.getLogger(__name__)

_VALID_REL_TYPE_RE = re.compile(r"^[A-Za-z_]+$")
_VALID_PROPERTY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_collect_property(collect_property: str) -> None:
    """Validate that collect_property passes an identifier allowlist to prevent Cypher injection."""
    if not _VALID_PROPERTY_RE.match(collect_property):
        raise ValueError(f"Invalid collect_property: {collect_property!r} — must match {_VALID_PROPERTY_RE.pattern}")


def _validate_rel_domain(rel_type_snake: str, expected_domain: str) -> None:
    """Validate that the relation's domain includes expected_domain."""
    from ontoagent.domain.exceptions import ConstraintViolationError

    rc = RELATION_CONSTRAINTS[rel_type_snake]
    domain = rc.domain
    allowed = {domain} if isinstance(domain, str) else domain
    if expected_domain not in allowed:
        raise ConstraintViolationError(f"关系 '{rel_type_snake}' 的源实体必须是 {allowed}，实际为 '{expected_domain}'")


def _validate_rel_range(rel_type_snake: str, expected_range: str) -> None:
    """Validate that the relation's range includes expected_range."""
    from ontoagent.domain.exceptions import ConstraintViolationError

    rc = RELATION_CONSTRAINTS[rel_type_snake]
    range_val = rc.range
    allowed = {range_val} if isinstance(range_val, str) else range_val
    if expected_range not in allowed:
        raise ConstraintViolationError(f"关系 '{rel_type_snake}' 的目标实体必须是 {allowed}，实际为 '{expected_range}'")


class ConstraintEngine:
    """执行遍历约束检查。加载时校验 relation_chain 合法性。"""

    def __init__(self, graph_store: GraphStore, constraints: list[TraversalConstraint]) -> None:
        for c in constraints:
            _validate_collect_property(c.collect_property)
            chain_len = len(c.relation_chain)
            for i, rel_type in enumerate(c.relation_chain):
                if not _VALID_REL_TYPE_RE.match(rel_type):
                    raise ValueError(f"Invalid relation type: {rel_type!r} — must match {_VALID_REL_TYPE_RE.pattern}")
                rel_snake = rel_type.lower()
                if rel_snake in RELATION_CONSTRAINTS:
                    # Validate domain for first hop, range for last hop
                    if chain_len == 1:
                        validate_relation_constraint(rel_snake, c.source_label, c.target_label)
                    elif i == 0:
                        # First hop: only validate domain
                        _validate_rel_domain(rel_snake, c.source_label)
                    elif i == chain_len - 1:
                        # Last hop: only validate range
                        _validate_rel_range(rel_snake, c.target_label)
                    # Intermediate hops: skip domain/range validation (unknown intermediate labels)

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

    def _traverse(self, start_id: str, constraint: TraversalConstraint) -> list[tuple[str, object]]:
        """沿关系链遍历，返回 (id, collect_property_value) 元组列表。

        Args:
            start_id: 起始实体 ID。
            constraint: 遍历约束。

        Returns:
            (target_id, property_value) 元组列表，可以直接传给 _aggregate。
        """
        current_ids = [start_id]
        for i, rel_type in enumerate(constraint.relation_chain):
            next_pairs: list[tuple[str, object]] = []
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
                        nid_out = row.get("id")
                        if nid_out:
                            next_pairs.append((nid_out, row.get("val")))
                except Exception:
                    logger.warning("Traversal query failed for node %s, rel %s", nid, rel_type, exc_info=True)

            current_ids = [nid for nid, _ in next_pairs]
            if not current_ids:
                break

        return next_pairs

    def _aggregate(self, results: list[tuple[str, object]], constraint: TraversalConstraint) -> GuardDecision:
        """聚合多个目标实体的约束级别。

        Args:
            results: (target_id, property_value) 元组列表。
            constraint: 遍历约束。

        Returns:
            GuardDecision 聚合后的决策，details 中包含 collected_values 和 ontology_source。
        """
        if not results:
            reason = f"No {constraint.target_label} found via {constraint.relation_chain}"
            return self._finalize_decision(GuardLevel.ALLOW, reason, constraint, {})

        levels: list[GuardLevel] = []
        reasons: list[str] = []
        collected_values: list[str] = []
        for nid, val in results:
            if val is None:
                continue
            val_str = str(val)
            level = constraint.value_mapping.get(val_str, GuardLevel.ALLOW)
            levels.append(level)
            reasons.append(f"{nid}.{constraint.collect_property}={val_str} → {level.value}")
            collected_values.append(val_str)

        if not levels:
            reason = f"No {constraint.collect_property} found"
            return self._finalize_decision(GuardLevel.ALLOW, reason, constraint, {})

        # block > warn > allow
        priority = {GuardLevel.BLOCK: 3, GuardLevel.WARN: 2, GuardLevel.ALLOW: 1}

        if constraint.aggregation == "max":
            final_level = max(levels, key=lambda lv: priority.get(lv, 0))
        elif constraint.aggregation == "min":
            final_level = min(levels, key=lambda lv: priority.get(lv, 3))
        elif constraint.aggregation == "exists":
            has_issue = any(lv != GuardLevel.ALLOW for lv in levels)
            final_level = GuardLevel.WARN if has_issue else GuardLevel.ALLOW
        else:
            final_level = GuardLevel.ALLOW

        reason = "; ".join(reasons)
        details: dict = {"collected_values": collected_values}
        return self._finalize_decision(final_level, reason, constraint, details)

    @staticmethod
    def _finalize_decision(
        level: GuardLevel,
        reason: str,
        constraint: TraversalConstraint,
        details: dict,
    ) -> GuardDecision:
        """Append ontology_source to reason and details if present."""
        if constraint.ontology_source:
            reason = f"{reason} [来源: {constraint.ontology_source}]"
            details["ontology_source"] = constraint.ontology_source
        return GuardDecision(level=level, reason=reason, details=details if details else None)
