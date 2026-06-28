from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ontoagent.store.graph_store import GraphStore

logger = __import__("logging").getLogger(__name__)

_VALID_REL_TYPE_RE = re.compile(r"^[A-Za-z_]+$")
_VALID_PROPERTY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass
class PropagationRule:
    """传播规则：沿关系链传播后收集属性并聚合"""

    name: str
    along: list[str]  # 关系类型列表 (e.g. ["CALLS"])
    collect_property: str  # 收集的属性名 (e.g. "risk_level")
    value_mapping: dict[str, str] = field(default_factory=dict)  # 属性值→约束级别 (e.g. {"P0": "block"})
    direction: Literal["forward", "backward"] = "forward"  # 方向
    max_depth: int = 5  # BFS 最大深度 (0 means only check self)
    aggregation: Literal["max", "min", "exists"] = "max"


@dataclass
class PropagationResult:
    """传播结果"""

    reached_nodes: list[dict] = field(default_factory=list)  # 到达的节点
    aggregated_level: str = "allow"  # 聚合后的约束级别
    path_count: int = 0  # 传播路径数


class ConstraintPropagator:
    """BFS 约束属性传播器"""

    def __init__(self, graph_store: GraphStore) -> None:
        self._graph_store = graph_store

    def propagate(self, entity_id: str, rule: PropagationRule) -> PropagationResult:
        """BFS 沿关系链传播，收集属性值，返回聚合结果。

        max_depth=0 means only check the starting entity itself (depth 0 BFS).
        """
        # Validate rel types and collect_property to prevent Cypher injection
        for rel_type in rule.along:
            if not _VALID_REL_TYPE_RE.match(rel_type):
                raise ValueError(f"Invalid relation type: {rel_type!r} — must match {_VALID_REL_TYPE_RE.pattern}")
        if not _VALID_PROPERTY_RE.match(rule.collect_property):
            raise ValueError(f"Invalid collect_property: {rule.collect_property!r} — must match {_VALID_PROPERTY_RE.pattern}")

        visited = {entity_id}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])  # (node_id, depth)
        reached: list[dict] = []
        collected_values: list[str] = []

        # max_depth=0: only check self
        if rule.max_depth == 0:
            val = self._graph_store.get_node(entity_id)
            if val and val.get(rule.collect_property) is not None:
                reached.append(val)
                collected_values.append(val[rule.collect_property])
            level = self._aggregate(collected_values, rule)
            return PropagationResult(
                reached_nodes=reached,
                aggregated_level=level,
                path_count=len(reached),
            )

        while queue:
            node_id, depth = queue.popleft()
            if depth >= rule.max_depth:
                continue

            # Get relations for current node
            for rel_type in rule.along:
                if rule.direction == "forward":
                    query = f"MATCH (a)-[:{rel_type}]->(b) WHERE a.id = $id RETURN b"
                else:
                    query = f"MATCH (a)-[:{rel_type}]->(b) WHERE b.id = $id RETURN a"

                try:
                    rows = self._graph_store.query(query, {"id": node_id})
                    for row in rows:
                        neighbor = row.get("b") if rule.direction == "forward" else row.get("a")
                        if not neighbor:
                            continue
                        nid = neighbor.get("id")
                        if nid and nid not in visited:
                            visited.add(nid)
                            queue.append((nid, depth + 1))

                            # Collect property value
                            val = neighbor.get(rule.collect_property)
                            if val is not None:
                                reached.append(neighbor)
                                collected_values.append(str(val))
                except Exception:
                    logger.warning("Propagation query failed for node %s, rel %s", node_id, rel_type, exc_info=True)

        # Aggregate
        level = self._aggregate(collected_values, rule)
        return PropagationResult(
            reached_nodes=reached,
            aggregated_level=level,
            path_count=len(reached),
        )

    def _aggregate(self, values: list[str], rule: PropagationRule) -> str:
        """聚合收集到的属性值为约束级别"""
        if not values:
            return "allow"

        levels = [rule.value_mapping.get(str(v), "allow") for v in values]
        priority = {"block": 3, "warn": 2, "allow": 1}

        if rule.aggregation == "min":
            return min(levels, key=lambda level: priority.get(level, 0))
        return max(levels, key=lambda level: priority.get(level, 0))

    def find_entry_points(self, entity_id: str, max_depth: int = 10) -> list[dict]:
        """反向查找入口点（有 entryCategory 的 CodeEntity）。

        Checks the starting node first, then BFS backwards via CALLS.
        """
        visited: set[str] = {entity_id}
        entry_points: list[dict] = []

        # Check starting node first
        start_node = self._graph_store.get_node(entity_id)
        if start_node and start_node.get("entryCategory"):
            entry_points.append(start_node)

        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])

        while queue:
            node_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            # Single backward CALLS query — filter entryCategory in Python
            query = "MATCH (a:CodeEntity)-[:CALLS]->(b:CodeEntity) WHERE b.id = $id RETURN a"
            try:
                rows = self._graph_store.query(query, {"id": node_id})
                for row in rows:
                    neighbor = row.get("a")
                    if neighbor:
                        nid = neighbor.get("id")
                        if nid and nid not in visited:
                            visited.add(nid)
                            queue.append((nid, depth + 1))
                            if neighbor.get("entryCategory"):
                                entry_points.append(neighbor)
            except Exception:
                logger.warning("find_entry_points query failed for node %s", node_id, exc_info=True)

        return entry_points
