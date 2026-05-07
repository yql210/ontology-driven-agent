from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from layerkg.change_detector import ChangedFile, ChangeType

if TYPE_CHECKING:
    from layerkg.graph_store import GraphStore


class PropagationDirection(Enum):
    """影响传播方向枚举。"""

    FORWARD = "FORWARD"  # 正向：谁依赖了我（我的变更影响谁）
    BACKWARD = "BACKWARD"  # 反向：我依赖了谁（我的变更可能受谁影响）


class ImpactSeverity(Enum):
    """影响严重程度枚举。"""

    CRITICAL = "CRITICAL"  # ≥ 0.8: 直接依赖，很可能受影响
    HIGH = "HIGH"  # ≥ 0.5: 较近依赖，需要审查
    MEDIUM = "MEDIUM"  # ≥ 0.2: 间接依赖，可能受影响
    LOW = "LOW"  # < 0.2: 远距离依赖，影响较小

    @classmethod
    def from_score(cls, score: float) -> ImpactSeverity:
        """根据影响分数推导严重程度。

        Args:
            score: 影响分数 [0, 1]。

        Returns:
            对应的 ImpactSeverity 级别。
        """
        if score >= 0.8:
            return cls.CRITICAL
        if score >= 0.5:
            return cls.HIGH
        if score >= 0.2:
            return cls.MEDIUM
        return cls.LOW


@dataclass
class ImpactedNode:
    """受影响节点。

    Attributes:
        node_id: 图谱节点 ID。
        node_label: 节点标签（CodeEntity/DocEntity 等）。
        name: 实体名称。
        file_path: 文件路径（可选）。
        impact_score: 影响分数 [0, 1]。
        severity: 影响严重程度分类。
        depth: 距离变更源的跳数。
        direction: 传播方向。
        relation_path: 从变更源到当前节点的完整关系类型路径。
        source_node_id: 变更源节点 ID。
    """

    node_id: str
    node_label: str
    name: str
    impact_score: float
    severity: ImpactSeverity
    depth: int
    direction: PropagationDirection
    relation_path: list[str]
    source_node_id: str
    file_path: str | None = None

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "node_id": self.node_id,
            "node_label": self.node_label,
            "name": self.name,
            "file_path": self.file_path,
            "impact_score": self.impact_score,
            "severity": self.severity.value,
            "depth": self.depth,
            "direction": self.direction.value,
            "relation_path": self.relation_path,
            "source_node_id": self.source_node_id,
        }


DEFAULT_WEIGHT_MATRIX: dict[str, dict[str, float]] = {
    # relation_type: {ADDED, DELETED, SIGNATURE, BODY, DOC_ONLY}
    "calls": {"ADDED": 0.9, "DELETED": 1.0, "SIGNATURE": 0.9, "BODY": 0.7, "DOC_ONLY": 0.1},
    "implements": {"ADDED": 0.8, "DELETED": 1.0, "SIGNATURE": 0.8, "BODY": 0.5, "DOC_ONLY": 0.1},
    "extends": {"ADDED": 0.8, "DELETED": 0.9, "SIGNATURE": 0.9, "BODY": 0.6, "DOC_ONLY": 0.1},
    "imports": {"ADDED": 0.5, "DELETED": 0.6, "SIGNATURE": 0.5, "BODY": 0.3, "DOC_ONLY": 0.0},
    "semantic_impact": {"ADDED": 0.4, "DELETED": 0.5, "SIGNATURE": 0.5, "BODY": 0.4, "DOC_ONLY": 0.2},
    "describes": {"ADDED": 0.3, "DELETED": 0.3, "SIGNATURE": 0.3, "BODY": 0.2, "DOC_ONLY": 0.3},
    # 占位：Phase 1+ 可能启用
    "derived_from": {"ADDED": 0.0, "DELETED": 0.0, "SIGNATURE": 0.0, "BODY": 0.0, "DOC_ONLY": 0.0},
    "affects": {"ADDED": 0.0, "DELETED": 0.0, "SIGNATURE": 0.0, "BODY": 0.0, "DOC_ONLY": 0.0},
}


DEFAULT_DECAY_SCHEDULE: dict[int, float] = {
    1: 1.0,
    2: 0.6,
    3: 0.3,
}


@dataclass
class ImpactReport:
    """影响传播结果报告。

    Attributes:
        changed_files: 变更文件路径列表。
        changed_node_ids: 变更节点 ID 列表。
        impacted_nodes: 受影响节点（按分数降序）。
        total_analyzed: BFS 遍历的总节点数。
        propagation_time_ms: 传播耗时（毫秒）。
    """

    changed_files: list[str]
    changed_node_ids: list[str]
    impacted_nodes: list[ImpactedNode]
    total_analyzed: int
    propagation_time_ms: float

    @property
    def critical_count(self) -> int:
        """CRITICAL 级别节点数量。"""
        return sum(1 for n in self.impacted_nodes if n.severity == ImpactSeverity.CRITICAL)

    @property
    def affected_files(self) -> set[str]:
        """受影响文件的唯一集合。"""
        return {n.file_path for n in self.impacted_nodes if n.file_path}

    @property
    def nodes_by_severity(self) -> dict[ImpactSeverity, list[ImpactedNode]]:
        """按严重程度分组的节点。"""
        result = {s: [] for s in ImpactSeverity}
        for node in self.impacted_nodes:
            result[node.severity].append(node)
        return result

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "changed_files": self.changed_files,
            "changed_node_ids": self.changed_node_ids,
            "impacted_nodes": [n.to_dict() for n in self.impacted_nodes],
            "total_analyzed": self.total_analyzed,
            "propagation_time_ms": self.propagation_time_ms,
            "critical_count": self.critical_count,
        }


class ImpactPropagator:
    """影响传播器。

    通过双向 BFS 在知识图谱中传播代码变更的影响，
    输出带评分的影响报告。

    Attributes:
        _graph_store: 图存储抽象层实例。
        _weight_matrix: 关系类型 × 变更类型的权重矩阵。
        _decay_schedule: 深度衰减表。
        _max_depth: 最大传播深度。
        _impact_threshold: 低于此分数的节点不收录。
    """

    def __init__(
        self,
        graph_store: GraphStore,
        weight_matrix: dict[str, dict[str, float]] | None = None,
        decay_schedule: dict[int, float] | None = None,
        max_depth: int = 3,
        impact_threshold: float = 0.05,
    ) -> None:
        """初始化影响传播器。

        Args:
            graph_store: GraphStore ABC 实例。
            weight_matrix: 关系×变更类型权重矩阵。
            decay_schedule: 深度衰减表。
            max_depth: 最大传播深度。
            impact_threshold: 低于此分数的节点不收录。

        Raises:
            ValueError: max_depth 不是正整数。
        """
        if max_depth < 1:
            raise ValueError("max_depth must be positive")
        self._graph_store = graph_store
        self._weight_matrix = weight_matrix or DEFAULT_WEIGHT_MATRIX.copy()
        self._decay_schedule = decay_schedule or DEFAULT_DECAY_SCHEDULE.copy()
        self._max_depth = max_depth
        self._impact_threshold = impact_threshold
        self._logger = logging.getLogger(__name__)

    def _compute_score(self, relation_type: str, change_type: ChangeType, depth: int) -> float:
        """计算单跳影响分数。

        Args:
            relation_type: 关系类型（如 calls）。
            change_type: 变更类型。
            depth: 当前深度。

        Returns:
            影响分数 [0, 1]，如果关系类型未知或深度超出范围则返回 0.0。
        """
        relation_weights = self._weight_matrix.get(relation_type)
        if relation_weights is None:
            return 0.0
        weight = relation_weights.get(change_type.name, 0.0)
        decay = self._decay_schedule.get(depth, 0.0)
        return weight * decay

    def _classify_severity(self, score: float) -> ImpactSeverity:
        """将影响分数分类为严重程度。

        Args:
            score: 影响分数 [0, 1]。

        Returns:
            对应的 ImpactSeverity 级别。
        """
        return ImpactSeverity.from_score(score)

    def map_files_to_nodes(self, changes: list[ChangedFile]) -> dict[str, list[str]]:
        """将 ChangedFile 映射为图谱节点。

        Args:
            changes: ChangedFile 列表。

        Returns:
            file_path → node_ids 映射字典。文件不在图谱中则不包含。
        """
        result: dict[str, list[str]] = {}

        for change in changes:
            nodes = self._graph_store.query(
                "MATCH (n {file_path: $fp}) RETURN n.id AS id, n.name AS name, labels(n) AS labels",
                {"fp": change.path}
            )
            node_ids = [n["id"] for n in nodes] if nodes else []
            if node_ids:
                result[change.path] = node_ids
            else:
                self._logger.debug("No graph nodes found for file: %s", change.path)

        skipped = len(changes) - len(result)
        if skipped > 0:
            self._logger.info("Skipped %d files (no graph nodes)", skipped)

        return result

    def _merge_impacts(self, *impact_lists: list[ImpactedNode]) -> list[ImpactedNode]:
        """合并多源影响，同节点同方向取 MAX score。

        Args:
            *impact_lists: 多个 ImpactedNode 列表。

        Returns:
            合并后按 score 降序排序的列表。
        """
        merged: dict[tuple[str, PropagationDirection], ImpactedNode] = {}

        for impacts in impact_lists:
            for imp in impacts:
                key = (imp.node_id, imp.direction)
                if key not in merged or imp.impact_score > merged[key].impact_score:
                    merged[key] = imp

        return sorted(merged.values(), key=lambda x: x.impact_score, reverse=True)

    def _bidirectional_bfs(
        self,
        source_id: str,
        change_type: ChangeType,
        direction: PropagationDirection,
    ) -> list[ImpactedNode]:
        """单方向 BFS，带深度衰减和权重矩阵。

        Args:
            source_id: 变更源节点 ID。
            change_type: 变更类型。
            direction: 传播方向。

        Returns:
            受影响节点列表。
        """
        # frontier: 当前层的 {node_id: (best_score, relation_path)}
        frontier: dict[str, tuple[float, list[str]]] = {source_id: (1.0, [])}
        # visited: node_id → best_score（允许高分路径更新）
        visited: dict[str, float] = {source_id: 1.0}
        impacts: list[ImpactedNode] = []

        for depth in range(1, self._max_depth + 1):
            decay = self._decay_schedule.get(depth, 0.0)
            if decay == 0.0:
                break

            next_frontier: dict[str, tuple[float, list[str]]] = {}

            for node_id, (_, path_so_far) in frontier.items():
                # 根据 direction 查询邻居
                if direction == PropagationDirection.FORWARD:
                    relations = self._graph_store.get_relations(target_id=node_id)
                else:
                    relations = self._graph_store.get_relations(source_id=node_id)

                for rel in relations:
                    # 确定邻居节点 ID
                    neighbor_id = rel["source_id"] if direction == PropagationDirection.FORWARD else rel["target_id"]

                    if neighbor_id == source_id:
                        continue  # 跳过变更源自身

                    rel_type = rel["rel_type"].lower()
                    score = self._compute_score(rel_type, change_type, depth)

                    if score < self._impact_threshold:
                        continue

                    # 允许高分路径更新已访问节点
                    if neighbor_id in visited and score <= visited[neighbor_id]:
                        continue

                    visited[neighbor_id] = score
                    new_path = [*path_so_far, rel_type]

                    node = self._graph_store.get_node(neighbor_id)
                    if node:
                        impacts.append(ImpactedNode(
                            node_id=neighbor_id,
                            node_label=node.get("label", "Unknown"),
                            name=node.get("name", ""),
                            file_path=node.get("file_path"),
                            impact_score=score,
                            severity=self._classify_severity(score),
                            depth=depth,
                            direction=direction,
                            relation_path=new_path,
                            source_node_id=source_id,
                        ))
                        next_frontier[neighbor_id] = (score, new_path)

            frontier = next_frontier

            # 早停：如果 frontier 为空
            if not frontier:
                break

            # 早停：检查下一层衰减
            next_decay = self._decay_schedule.get(depth + 1, 0.0)
            if next_decay == 0.0:
                break

        return impacts

    def compute_impact(self, node_ids: list[str], change_type: ChangeType) -> list[ImpactedNode]:
        """执行 BFS 影响传播。

        Args:
            node_ids: 变更节点 ID 列表。
            change_type: 变更类型。

        Returns:
            受影响节点列表（按分数降序）。
        """
        if not node_ids:
            return []

        all_impacts: list[ImpactedNode] = []

        for source_id in node_ids:
            # 正向：谁依赖了我
            forward_impacts = self._bidirectional_bfs(
                source_id, change_type, PropagationDirection.FORWARD
            )
            # 反向：我依赖了谁
            backward_impacts = self._bidirectional_bfs(
                source_id, change_type, PropagationDirection.BACKWARD
            )
            all_impacts.extend(forward_impacts)
            all_impacts.extend(backward_impacts)

        return self._merge_impacts(all_impacts)

    def propagate(self, changes: list[ChangedFile]) -> ImpactReport:
        """主入口，执行完整传播流程。

        Args:
            changes: ChangedFile 列表。

        Returns:
            ImpactReport 影响报告。
        """
        start_time = time.time()

        # 1. 映射文件到节点
        file_to_nodes = self.map_files_to_nodes(changes)

        # 2. 收集所有变更节点（按变更类型分组）
        node_change_types: dict[str, ChangeType] = {}
        for change in changes:
            if change.path not in file_to_nodes:
                continue
            for node_id in file_to_nodes[change.path]:
                node_change_types[node_id] = change.change_type

        changed_node_ids = list(node_change_types.keys())
        changed_files = list(file_to_nodes.keys())

        # 3. 对每个变更类型分组执行传播
        all_impacts: list[ImpactedNode] = []
        for change_type in ChangeType:
            nodes_of_type = [nid for nid, ct in node_change_types.items() if ct == change_type]
            if nodes_of_type:
                impacts = self.compute_impact(nodes_of_type, change_type)
                all_impacts.extend(impacts)

        # 4. 合并并排序
        merged_impacts = self._merge_impacts(all_impacts)

        propagation_time_ms = (time.time() - start_time) * 1000

        return ImpactReport(
            changed_files=changed_files,
            changed_node_ids=changed_node_ids,
            impacted_nodes=merged_impacts,
            total_analyzed=len(changed_node_ids),
            propagation_time_ms=propagation_time_ms,
        )
