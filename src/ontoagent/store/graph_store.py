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

    def get_nodes_by_label(self, label: str, properties: list[str] | None = None) -> list[dict]:
        """批量读取指定 label 的全部节点。

        默认实现用 Cypher MATCH（Neo4j 高效）。NebulaGraph 子类应覆写为 LOOKUP ON，
        避免大图上的 MATCH 全 tag 扫描。

        统一返回业务 id（属性 ``n.id``），而非 Neo4j 内部节点 id（``id(n)``），
        避免调用方拿到内部 id 后定位/删除失效。properties 会去重保序，
        properties 含 ``id`` 时也不会生成重复的 RETURN alias。

        Args:
            label: 节点标签名。
            properties: 需要读取的属性名列表（camelCase）。``None`` 表示只读 id 和 name。

        Returns:
            节点属性字典列表，每项至少含 ``id``。
        """
        props = list(dict.fromkeys(properties)) if properties else ["id", "name"]
        if "id" in props:
            rest = [p for p in props if p != "id"]
            prop_clause = ", ".join(f"n.{p} AS {p}" for p in rest)
            return self.query(f"MATCH (n:{label}) RETURN n.id AS id{', ' + prop_clause if prop_clause else ''}")
        prop_clause = ", ".join(f"n.{p} AS {p}" for p in props)
        return self.query(f"MATCH (n:{label}) RETURN n.id AS id, {prop_clause}")

    def get_edges_by_types(self, rel_types: list[str], node_label: str = "") -> list[dict]:
        """批量读取指定类型的全部关系。

        默认实现用 Cypher MATCH（Neo4j 高效）。NebulaGraph 子类应覆写为 LOOKUP ON edge_type，
        利用 edge 自带索引。

        Args:
            rel_types: 关系类型列表（如 ``['CALLS', 'IMPORTS']``）。
            node_label: 可选，限制端点节点 label（默认实现会下推到 MATCH）。

        Returns:
            关系字典列表，每项含 ``source_id`` 和 ``target_id``。
        """
        type_filter = "|".join(rel_types)
        label_part = f":{node_label}" if node_label else ""
        cypher = (
            f"MATCH (a{label_part})-[r:{type_filter}]->(b{label_part}) RETURN id(a) AS source_id, id(b) AS target_id"
        )
        return self.query(cypher)
