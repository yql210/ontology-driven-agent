from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass
from itertools import combinations

from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.schema import ModuleEntity


@dataclass
class ModuleCluster:
    """聚类结果中间数据结构。"""

    module: ModuleEntity
    entity_ids: list[str]
    cohesion: float
    entity_count: int

    def __post_init__(self) -> None:
        """校验字段。"""
        if self.entity_count != len(self.entity_ids):
            msg = f"entity_count ({self.entity_count}) must equal len(entity_ids) ({len(self.entity_ids)})"
            raise AssertionError(msg)


class ModuleClustering:
    """模块聚类器：基于图结构的社区发现。

    使用 Neo4j 图遍历，通过 Label Propagation 算法
    将代码实体聚类为功能模块。
    """

    _SUPPORTED_ALGORITHMS = {"label_propagation"}

    def __init__(
        self,
        neo4j_store: Neo4jGraphStore,
        algorithm: str = "label_propagation",
    ) -> None:
        """初始化。

        Args:
            neo4j_store: Neo4j 图存储实例。
            algorithm: 聚类算法，目前支持 'label_propagation'。

        Raises:
            ValueError: 当 algorithm 不支持时。
        """
        if algorithm not in self._SUPPORTED_ALGORITHMS:
            msg = f"Unsupported algorithm: {algorithm}. Supported: {self._SUPPORTED_ALGORITHMS}"
            raise ValueError(msg)
        self._neo4j_store = neo4j_store
        self._algorithm = algorithm
        self._module_name_counter = 0
        self._logger = logging.getLogger(self.__class__.__name__)

    def _load_graph(self) -> tuple[dict[str, set[str]], dict[str, dict]]:
        """从 Neo4j 加载邻接表和实体数据。

        使用 neo4j_store.query() 执行 Cypher。
        添加同文件虚拟边：同一文件内的实体两两互连，解决图稀疏问题。

        Returns:
            (adj, entity_data) 元组：
                adj: {entity_id: {neighbor_id, ...}} 邻接表
                entity_data: {entity_id: {name, file_path, ...}} 实体属性

        Cypher 查询：
            1. 获取所有 CodeEntity
            2. 获取 CodeEntity 之间的关系
        """
        # 获取所有 CodeEntity
        entity_cypher = """
            MATCH (c:CodeEntity)
            RETURN c.id AS id, c.name AS name, c.file_path AS file_path
        """
        entity_results = self._neo4j_store.query(entity_cypher)

        # 构建实体数据
        entity_data: dict[str, dict] = {}
        for record in entity_results:
            entity_id = record["id"]
            entity_data[entity_id] = {
                "name": record.get("name"),
                "file_path": record.get("file_path"),
            }

        # 获取关系
        relation_cypher = """
            MATCH (c1:CodeEntity)-[r]->(c2:CodeEntity)
            WHERE type(r) IN ['CALLS', 'IMPORTS', 'EXTENDS', 'IMPLEMENTS']
            RETURN c1.id AS source, c2.id AS target
        """
        relation_results = self._neo4j_store.query(relation_cypher)

        # 构建邻接表（无向图）
        adj: dict[str, set[str]] = {eid: set() for eid in entity_data}
        for record in relation_results:
            source = record["source"]
            target = record["target"]
            if source in adj and target in adj:
                adj[source].add(target)
                adj[target].add(source)

        # 添加同文件虚拟边：同一文件内的实体两两互连
        # 按 file_path 分组
        file_to_entities: dict[str, list[str]] = {}
        for entity_id, data in entity_data.items():
            file_path = data.get("file_path")
            if file_path:
                file_to_entities.setdefault(file_path, []).append(entity_id)

        # 为每个文件内的实体添加全连接虚拟边
        for _file_path, entities_in_file in file_to_entities.items():
            if len(entities_in_file) > 1:
                # 同文件内的实体两两互连
                for e1, e2 in combinations(entities_in_file, 2):
                    adj[e1].add(e2)
                    adj[e2].add(e1)

        return adj, entity_data

    def _label_propagation(
        self,
        adj: dict[str, set[str]],
        max_iterations: int = 100,
    ) -> dict[str, str]:
        """Label Propagation 算法（确定性版本）。

        关键：使用 sorted() 确保节点遍历顺序和邻居选择顺序确定。
        增加收敛检查：labels 不变时提前退出。

        Args:
            adj: 邻接表 {node_id: {neighbor_ids}}
            max_iterations: 最大迭代次数

        Returns:
            {entity_id: community_label} 社区分配结果

        Algorithm:
            1. 初始化：每个节点的标签 = 自身 id
            2. 每轮迭代：
               a. sorted(adj.keys()) 确定性遍历
               b. 对每个节点，sorted(neighbors) 统计邻居标签
               c. 选择出现次数最多的标签（平局选字典序最小的）
               d. labels == old_labels → 收敛退出
            3. 返回最终 labels
        """
        if not adj:
            return {}

        # 初始化：每个节点的标签 = 自身 id
        labels: dict[str, str] = {node: node for node in adj}

        for _ in range(max_iterations):
            old_labels = labels.copy()

            # 确定性遍历顺序
            for node in sorted(adj.keys()):
                neighbors = adj[node]

                if not neighbors:
                    # 孤立节点保持自身标签
                    continue

                # 统计邻居标签（确定性格式）
                neighbor_labels = [labels[n] for n in sorted(neighbors)]
                counter = Counter(neighbor_labels)

                # most_common(1) 返回 [(label, count)]
                # 由于 sorted() 输入，平局时字典序最小的标签先出现
                most_common_label = counter.most_common(1)[0][0]
                labels[node] = most_common_label

            # 收敛检查
            if labels == old_labels:
                break

        return labels

    def _compute_cohesion(
        self,
        entity_ids: list[str],
        adj: dict[str, set[str]],
    ) -> float:
        """计算模块内聚度。

        cohesion = 模块内边数 / 最大可能边数(n*(n-1)/2)

        Args:
            entity_ids: 模块内实体 ID 列表
            adj: 邻接表

        Returns:
            内聚度值 [0, 1]
        """
        n = len(entity_ids)
        if n <= 1:
            return 0.0

        entity_set = set(entity_ids)
        internal_edges = 0

        # 计算内部边数（无向图，每条边只计数一次）
        for entity_id in entity_ids:
            neighbors = adj.get(entity_id, set())
            # 只统计指向模块内其他实体的边
            for neighbor in neighbors:
                if neighbor in entity_set and entity_ids.index(entity_id) < entity_ids.index(neighbor):
                    internal_edges += 1

        max_edges = n * (n - 1) // 2
        return internal_edges / max_edges if max_edges > 0 else 0.0

    def _generate_module_name(
        self,
        entity_ids: list[str],
        entity_data: dict[str, dict],
    ) -> str:
        """根据聚类内容生成模块名称。

        策略：
        1. 收集所有 file_path（过滤 None）
        2. 计算 os.path.commonpath → 取最后一段作为名称
        3. 无 file_path → 降级为 "module_{N}"

        Args:
            entity_ids: 模块内实体 ID 列表
            entity_data: 实体属性字典

        Returns:
            模块名称
        """
        # 收集所有非 None 的 file_path
        file_paths = [
            entity_data[eid]["file_path"] for eid in entity_ids if entity_data.get(eid, {}).get("file_path") is not None
        ]

        if not file_paths:
            name = f"module_{self._module_name_counter}"
            self._module_name_counter += 1
            return name

        try:
            # 计算公共路径前缀
            common = os.path.commonpath(file_paths)
            # 取最后一段作为模块名
            module_name = os.path.basename(common)
            if module_name:
                return module_name
        except ValueError:
            # 无公共前缀时 commonpath 抛 ValueError
            pass

        # 降级：使用计数器
        name = f"module_{self._module_name_counter}"
        self._module_name_counter += 1
        return name

    def detect_modules(self) -> list[ModuleCluster]:
        """执行社区发现，返回模块聚类结果。

        步骤：
        1. 从 Neo4j 加载 CodeEntity 子图
        2. 在内存中构建邻接表
        3. 执行 Label Propagation 算法
        4. 为每个聚类生成 ModuleEntity + 计算 cohesion

        Returns:
            模块聚类列表
        """
        adj, entity_data = self._load_graph()

        # 诊断日志：图统计信息
        isolated = sum(1 for neighbors in adj.values() if not neighbors)
        total_nodes = len(adj)
        total_edges = sum(len(n) for n in adj.values()) // 2  # 无向图，边数是邻接表总和的一半
        isolated_pct = 100 * isolated / total_nodes if total_nodes > 0 else 0
        self._logger.info(
            "Module clustering graph: %d nodes, %d isolated (%.1f%%), %d edges",
            total_nodes,
            isolated,
            isolated_pct,
            total_edges,
        )

        labels = self._label_propagation(adj)

        # 按 label 分组
        communities: dict[str, list[str]] = {}
        for entity_id, label in labels.items():
            communities.setdefault(label, []).append(entity_id)

        # 为每个社区创建 ModuleCluster
        clusters: list[ModuleCluster] = []
        for _community_id, entity_ids in communities.items():
            module_name = self._generate_module_name(entity_ids, entity_data)
            module = ModuleEntity(name=module_name)
            cohesion = self._compute_cohesion(entity_ids, adj)

            cluster = ModuleCluster(
                module=module,
                entity_ids=entity_ids,
                cohesion=cohesion,
                entity_count=len(entity_ids),
            )
            clusters.append(cluster)

        self._logger.info("[Clustering] Detected %d modules", len(clusters))
        return clusters

    def save_modules(self, clusters: list[ModuleCluster]) -> int:
        """将聚类结果保存为 ModuleEntity + contains 关系。

        Args:
            clusters: 模块聚类列表

        Returns:
            保存的模块数量
        """
        saved = 0
        for cluster in clusters:
            # 保存 ModuleEntity
            self._neo4j_store.merge_node(
                "ModuleEntity",
                {
                    "id": cluster.module.id,
                    "name": cluster.module.name,
                    "description": cluster.module.description,
                    "created_at": cluster.module.created_at,
                },
            )

            # 创建 contains 关系
            for entity_id in cluster.entity_ids:
                self._neo4j_store.merge_relation(
                    source_id=cluster.module.id,
                    target_id=entity_id,
                    rel_type="contains",
                    source_label="ModuleEntity",
                    target_label="CodeEntity",
                )

            saved += 1

        self._logger.info("[Clustering] Saved %d modules to Neo4j", saved)
        return saved

    def get_module_tree(self) -> dict:
        """返回模块层次结构树。

        Returns:
            格式: {module_name: {"entities": [...], "cohesion": float, "entity_count": int}}
        """
        clusters = self.detect_modules()

        tree: dict = {}
        for cluster in clusters:
            tree[cluster.module.name] = {
                "entities": cluster.entity_ids,
                "cohesion": cluster.cohesion,
                "entity_count": cluster.entity_count,
            }

        return tree
