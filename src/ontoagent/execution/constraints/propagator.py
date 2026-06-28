from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class PropagationRule:
    """传播规则：沿关系链传播后收集属性并聚合"""

    name: str
    along: list[str]  # 关系类型列表 (e.g. ["CALLS"])
    collect_property: str  # 收集的属性名 (e.g. "risk_level")
    value_mapping: dict[str, str] = field(default_factory=dict)  # 属性值→约束级别 (e.g. {"P0": "block"})
    direction: Literal["forward", "backward"] = "forward"  # 方向
    max_depth: int = 5  # BFS 最大深度
    aggregation: Literal["max", "min", "exists"] = "max"


@dataclass
class PropagationResult:
    """传播结果"""

    reached_nodes: list[dict] = field(default_factory=list)  # 到达的节点
    aggregated_level: str = "allow"  # 聚合后的约束级别
    path_count: int = 0  # 传播路径数


class ConstraintPropagator:
    """BFS 约束属性传播器"""

    def __init__(self, graph_store):
        self._graph_store = graph_store

    def propagate(self, entity_id: str, rule: PropagationRule) -> PropagationResult:
        """BFS 沿关系链传播，收集属性值，返回聚合结果"""
        visited = {entity_id}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])  # (node_id, depth)
        reached: list[dict] = []
        collected_values: list[str] = []

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
                                collected_values.append(val)
                except Exception:
                    pass

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

        levels = [rule.value_mapping.get(v, "allow") for v in values]
        priority = {"block": 3, "warn": 2, "allow": 1}
        return max(levels, key=lambda level: priority.get(level, 0))

    def find_entry_points(self, entity_id: str, max_depth: int = 10) -> list[dict]:
        """反向查找入口点（有 entryCategory 的 CodeEntity）"""
        visited: set[str] = {entity_id}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])
        entry_points: list[dict] = []

        while queue:
            node_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            query = (
                "MATCH (a:CodeEntity)-[:CALLS]->(b:CodeEntity) "
                "WHERE b.id = $id AND a.entryCategory IS NOT NULL RETURN a"
            )
            rows = self._graph_store.query(query, {"id": node_id})
            for row in rows:
                neighbor = row.get("a")
                if neighbor:
                    nid = neighbor.get("id")
                    if nid and nid not in visited:
                        visited.add(nid)
                        entry_points.append(neighbor)

            # Also continue BFS for non-entry nodes
            query2 = "MATCH (a:CodeEntity)-[:CALLS]->(b:CodeEntity) WHERE b.id = $id RETURN a"
            rows2 = self._graph_store.query(query2, {"id": node_id})
            for row in rows2:
                neighbor = row.get("a")
                if neighbor:
                    nid = neighbor.get("id")
                    if nid and nid not in visited:
                        visited.add(nid)
                        queue.append((nid, depth + 1))

        return entry_points
