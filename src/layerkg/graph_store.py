from __future__ import annotations

from abc import ABC, abstractmethod


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
    ) -> dict:
        """合并（创建或更新）关系。

        Args:
            source_id: 源节点 ID。
            target_id: 目标节点 ID。
            rel_type: 关系类型。
            properties: 关系属性（可选）。

        Returns:
            合并后的关系属性。
        """

    @abstractmethod
    def delete_relation(
        self, source_id: str, target_id: str, rel_type: str
    ) -> bool:
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
