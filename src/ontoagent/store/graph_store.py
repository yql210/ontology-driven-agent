from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class GraphStore(ABC):
    """图存储抽象基类。

    所有图数据库适配器（Neo4j、内存实现等）必须继承此类并实现全部抽象方法。
    """

    @abstractmethod
    def merge_node(self, label: str, properties: dict) -> dict:
        """合并（创建或更新）节点。

        Args:
            label: 节点标签。
            properties: 节点属性字典，必须包含 'id'。

        Returns:
            合并后的节点属性。
        """

    @abstractmethod
    def get_node(self, node_id: str) -> dict | None:
        """根据 ID 获取节点。

        Args:
            node_id: 节点 ID。

        Returns:
            节点属性字典，不存在则返回 None。
        """

    @abstractmethod
    def delete_node(self, node_id: str) -> bool:
        """删除节点。

        Args:
            node_id: 节点 ID。

        Returns:
            是否成功删除。
        """

    @abstractmethod
    def merge_relation(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict | None = None,
        *,
        source_label: str = "",
        target_label: str = "",
    ) -> dict:
        """合并（创建或更新）关系。

        Args:
            source_id: 源节点 ID。
            target_id: 目标节点 ID。
            rel_type: 关系类型。
            properties: 关系属性（可选）。
            source_label: 源节点标签（可选），用于优化 MERGE 性能。
            target_label: 目标节点标签（可选），用于优化 MERGE 性能。

        Returns:
            合并后的关系属性。
        """

    @abstractmethod
    def delete_relation(self, source_id: str, target_id: str, rel_type: str) -> bool:
        """删除关系。

        Args:
            source_id: 源节点 ID。
            target_id: 目标节点 ID。
            rel_type: 关系类型。

        Returns:
            是否成功删除。
        """

    @abstractmethod
    def get_relations(
        self,
        source_id: str | None = None,
        target_id: str | None = None,
        rel_type: str | None = None,
    ) -> list[dict]:
        """查询关系。

        Args:
            source_id: 源节点 ID（可选）。
            target_id: 目标节点 ID（可选）。
            rel_type: 关系类型（可选）。

        Returns:
            匹配的关系列表。
        """

    @abstractmethod
    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """执行 Cypher 查询。

        Args:
            cypher: Cypher 查询语句。
            params: 查询参数（可选）。

        Returns:
            查询结果列表。
        """

    @abstractmethod
    def cleanup_orphan_nodes(self) -> int:
        """清理无标签的孤立节点。

        这些节点通常是因为 MERGE 操作时未指定 label 而创建的。

        Returns:
            删除的节点数量。
        """

    @abstractmethod
    def update_node_property(self, node_id: str, key: str, value: Any) -> bool:
        """更新单个节点的单个属性。

        Args:
            node_id: 节点 ID。
            key: 属性名（snake_case 自动转 camelCase 由实现处理）。
            value: 属性值。

        Returns:
            是否成功更新。
        """

    def merge_nodes_batch(
        self,
        label: str,
        properties_list: list[dict],
        batch_size: int = 200,
    ) -> int:
        """批量合并（创建或更新）节点。

        默认实现：循环调用 :meth:`merge_node`。子类可覆写以提供真正的批量优化
        （如 Neo4j 的 ``UNWIND + MERGE`` 或 NebulaGraph 的 ``INSERT VERTEX``）。

        Args:
            label: 节点标签。
            properties_list: 节点属性字典列表，每项必须包含 ``id``。
            batch_size: 每批处理数量，默认 200（默认实现忽略此参数）。

        Returns:
            合并的节点总数。
        """
        for props in properties_list:
            self.merge_node(label, props)
        return len(properties_list)

    def merge_relations_batch(
        self,
        relations: list[dict],
        batch_size: int = 200,
    ) -> int:
        """批量合并（创建或更新）关系。

        默认实现：循环调用 :meth:`merge_relation`。子类可覆写以提供真正的批量优化。

        Args:
            relations: 关系数据列表，每项 dict 含 ``source_id``/``target_id``/
                ``rel_type``，可选 ``properties``/``source_label``/``target_label``。
            batch_size: 每批处理数量，默认 200（默认实现忽略此参数）。

        Returns:
            合并的关系总数。
        """
        for rel in relations:
            self.merge_relation(
                source_id=rel["source_id"],
                target_id=rel["target_id"],
                rel_type=rel["rel_type"],
                properties=rel.get("properties"),
                source_label=rel.get("source_label", ""),
                target_label=rel.get("target_label", ""),
            )
        return len(relations)
