from __future__ import annotations

import logging
import re
from typing import Any

from neo4j import GraphDatabase
from neo4j import Result as Neo4jResult
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from layerkg.graph_store import GraphStore
from layerkg.schema import RELATION_TYPE_TO_NEO4J, validate_relation_constraint
from layerkg.schema_version import register_schema_version

logger = logging.getLogger(__name__)

# 实体标签列表，用于约束创建
ENTITY_LABELS = [
    "CodeEntity",
    "ConceptEntity",
    "DocEntity",
    "ResourceEntity",
    "ModuleEntity",
    "ChangeSetEntity",
]


class Neo4jGraphStore(GraphStore):
    """Neo4j 图数据库实现。

    使用 neo4j-python-driver 连接 Neo4j 数据库，实现节点和关系的 CRUD 操作。

    Attributes:
        _driver: Neo4j Driver 实例，内部使用。
        _uri: 数据库连接 URI。
        _user: 数据库用户名。
        _password: 数据库密码。
    """

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        *,
        max_connection_lifetime: int = 3600,
        connection_timeout: int = 60,
        max_transaction_retry_time: int = 60,
    ) -> None:
        """初始化 Neo4j 连接。

        Args:
            uri: Neo4j 连接 URI，如 bolt://localhost:7687。
            user: 数据库用户名。
            password: 数据库密码。
            max_connection_lifetime: 连接最大存活时间（秒）。
            connection_timeout: 连接超时时间（秒）。
            max_transaction_retry_time: 事务重试最大时间（秒）。
        """
        self._uri = uri
        self._user = user
        self._password = password
        self._driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            max_connection_lifetime=max_connection_lifetime,
            connection_timeout=connection_timeout,
            max_transaction_retry_time=max_transaction_retry_time,
        )

    def close(self) -> None:
        """关闭数据库连接。"""
        self._driver.close()
        logger.debug("Neo4j driver closed")

    def __enter__(self) -> Neo4jGraphStore:
        """进入 context manager。

        Returns:
            self。
        """
        return self

    def __exit__(self, *args: Any) -> None:
        """退出 context manager，关闭连接。"""
        self.close()

    def merge_node(self, label: str, properties: dict) -> dict:
        """合并（创建或更新）节点。

        使用 MERGE 语句根据 id 查找节点，存在则更新属性，不存在则创建。

        Args:
            label: 节点标签，如 CodeEntity、ConceptEntity。
            properties: 节点属性字典，必须包含 'id'。

        Returns:
            合并后的节点属性。

        Raises:
            ValueError: 当 properties 缺少 'id' 时。
        """
        if "id" not in properties:
            msg = "properties must contain 'id'"
            raise ValueError(msg)

        # 构建 SET 子句，排除 id（id 已在 MERGE 中使用）
        set_clauses = []
        for key in properties:
            if key != "id":
                set_clauses.append(f"n.{key} = ${key}")

        set_statement = ", ".join(set_clauses) if set_clauses else ""

        cypher = f"MERGE (n:{label} {{id: $id}})"
        if set_statement:
            cypher += f" SET {set_statement}"

        with self._driver.session() as session:
            session.run(cypher, **properties)
            logger.debug(f"Merged node {label}:{properties['id']}")

        return properties

    @retry(
        retry=retry_if_exception_type((OSError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_fixed(5),
        reraise=True,
    )
    def _execute_batch_nodes(self, label: str, batch: list[dict]) -> None:
        """Execute a single batch of node MERGE operations with retry.

        Args:
            label: 节点标签。
            batch: 属性字典列表。
        """
        cypher = f"UNWIND $batch AS props MERGE (n:{label} {{id: props.id}}) SET n += props"
        with self._driver.session() as session:
            session.run(cypher, batch=batch)

    def merge_nodes_batch(
        self,
        label: str,
        properties_list: list[dict],
        batch_size: int = 200,
    ) -> int:
        """批量合并（创建或更新）节点。

        使用 UNWIND + MERGE 批量写入，按 batch_size 分批执行。

        Args:
            label: 节点标签，如 CodeEntity、ConceptEntity。
            properties_list: 节点属性字典列表，每项必须包含 'id'。
            batch_size: 每批处理数量，默认 200。

        Returns:
            合并的节点总数。

        Raises:
            ValueError: 当 label 包含非法字符时（Cypher 注入防护）。
        """
        if not re.match(r"^[A-Za-z_]\w*$", label):
            msg = f"Invalid label: {label}"
            raise ValueError(msg)

        total = len(properties_list)
        merged = 0
        for i in range(0, total, batch_size):
            batch = properties_list[i : i + batch_size]
            self._execute_batch_nodes(label, batch)
            merged += len(batch)
            logger.info("[Neo4j] Batch merged %d/%d %s", merged, total, label)

        return merged

    @retry(
        retry=retry_if_exception_type((OSError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_fixed(5),
        reraise=True,
    )
    def _execute_batch_relations(self, cypher: str, batch: list[dict]) -> None:
        """Execute a single batch of relation MERGE operations with retry.

        Args:
            cypher: 参数化 Cypher 语句。
            batch: 关系数据列表。
        """
        with self._driver.session() as session:
            session.run(cypher, batch=batch)

    def merge_relations_batch(
        self,
        relations: list[dict],
        batch_size: int = 200,
    ) -> int:
        """批量合并（创建或更新）关系。

        使用 UNWIND + MERGE 批量写入，按 batch_size 分批执行。

        Args:
            relations: 关系数据列表，每项包含:
                source_id, target_id, rel_type, source_label, target_label,
                properties (可选)。
            batch_size: 每批处理数量，默认 200。

        Returns:
            合并的关系总数。

        Raises:
            ValueError: 当 label 或 rel_type 包含非法字符时（Cypher 注入防护）。
        """
        total = len(relations)
        merged = 0

        # Group relations by (source_label, target_label, rel_type) to share Cypher
        groups: dict[tuple[str, str, str], list[dict]] = {}
        for rel in relations:
            source_label = rel.get("source_label", "")
            target_label = rel.get("target_label", "")
            neo4j_rel_type = RELATION_TYPE_TO_NEO4J.get(rel["rel_type"], rel["rel_type"].upper())

            if source_label and not re.match(r"^[A-Za-z_]\w*$", source_label):
                msg = f"Invalid source_label: {source_label}"
                raise ValueError(msg)
            if target_label and not re.match(r"^[A-Za-z_]\w*$", target_label):
                msg = f"Invalid target_label: {target_label}"
                raise ValueError(msg)
            if not re.match(r"^[A-Z_]+$", neo4j_rel_type):
                msg = f"Invalid relation type: {neo4j_rel_type}"
                raise ValueError(msg)

            key = (source_label, target_label, neo4j_rel_type)
            groups.setdefault(key, []).append(rel)

        for (source_label, target_label, neo4j_rel_type), group_rels in groups.items():
            source_part = f"source:{source_label}" if source_label else "source"
            target_part = f"target:{target_label}" if target_label else "target"
            cypher = (
                f"UNWIND $batch AS item "
                f"MATCH ({source_part} {{id: item.source_id}}) "
                f"MATCH ({target_part} {{id: item.target_id}}) "
                f"MERGE (source)-[r:{neo4j_rel_type}]->(target) "
                f"SET r += item.properties"
            )

            batch_data = []
            for rel in group_rels:
                batch_data.append(
                    {
                        "source_id": rel["source_id"],
                        "target_id": rel["target_id"],
                        "properties": rel.get("properties", {}),
                    }
                )

            for i in range(0, len(batch_data), batch_size):
                batch = batch_data[i : i + batch_size]
                self._execute_batch_relations(cypher, batch)
                merged += len(batch)
                logger.info("[Neo4j] Batch merged %d/%d relations", merged, total)

        return merged

    def get_node(self, node_id: str) -> dict | None:
        """根据 ID 获取节点。

        Args:
            node_id: 节点 ID。

        Returns:
            节点属性字典，不存在则返回 None。
        """
        cypher = "MATCH (n {id: $id}) RETURN n"

        with self._driver.session() as session:
            result: Neo4jResult = session.run(cypher, id=node_id)
            record = result.single()

            if record is None:
                return None

            node = record.get("n")
            return dict(node)

    def delete_node(self, node_id: str) -> bool:
        """删除节点。

        使用 DETACH DELETE 同时删除节点及其所有关系。

        Args:
            node_id: 节点 ID。

        Returns:
            是否成功删除（节点不存在返回 False）。
        """
        cypher = "MATCH (n {id: $id}) DETACH DELETE n"

        with self._driver.session() as session:
            result: Neo4jResult = session.run(cypher, id=node_id)
            summary = result.consume()
            # counters.nodes_deleted 返回删除的节点数
            deleted_count = summary.counters.nodes_deleted

            return deleted_count > 0

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
            rel_type: 关系类型，支持 snake_case 或 UPPER_SNAKE。
            properties: 关系属性（可选）。
            source_label: 源节点标签（可选），用于优化 MERGE 性能。
            target_label: 目标节点标签（可选），用于优化 MERGE 性能。

        Returns:
            合并后的关系属性。

        Raises:
            ValueError: 当 label 或 rel_type 包含非法字符时（Cypher 注入防护）。
        """
        # Cypher 注入防护：验证 label 格式
        if source_label and not re.match(r"^[A-Za-z_]\w*$", source_label):
            msg = f"Invalid source_label: {source_label}"
            raise ValueError(msg)
        if target_label and not re.match(r"^[A-Za-z_]\w*$", target_label):
            msg = f"Invalid target_label: {target_label}"
            raise ValueError(msg)

        # 转换关系类型为 Neo4j 格式（UPPER_SNAKE）
        neo4j_rel_type = RELATION_TYPE_TO_NEO4J.get(rel_type, rel_type.upper())

        # Cypher 注入防护：验证关系类型格式
        if not re.match(r"^[A-Z_]+$", neo4j_rel_type):
            msg = f"Invalid relation type: {neo4j_rel_type}"
            raise ValueError(msg)

        # --- 本体约束校验（domain/range）---
        if source_label and target_label:
            validate_relation_constraint(rel_type, source_label, target_label)

        # 动态构建 MATCH + MERGE 语句（拆分避免 UNIQUE 约束冲突）
        source_part = f"source:{source_label}" if source_label else "source"
        target_part = f"target:{target_label}" if target_label else "target"
        cypher = f"MATCH ({source_part} {{id: $source_id}})"
        cypher += f" MATCH ({target_part} {{id: $target_id}})"
        cypher += f" MERGE (source)-[r:{neo4j_rel_type}]->(target)"

        # 准备参数
        params: dict[str, Any] = {"source_id": source_id, "target_id": target_id}

        # 如果有属性，添加 SET 子句
        if properties:
            set_clauses = []
            for key, value in properties.items():
                set_clauses.append(f"r.{key} = ${key}")
                params[key] = value
            cypher += " SET " + ", ".join(set_clauses)

        with self._driver.session() as session:
            session.run(cypher, **params)
            logger.debug(f"Merged relation {source_id}-[{neo4j_rel_type}]->{target_id}")

        return properties or {}

    def delete_relation(self, source_id: str, target_id: str, rel_type: str) -> bool:
        """删除关系。

        Args:
            source_id: 源节点 ID。
            target_id: 目标节点 ID。
            rel_type: 关系类型，支持 snake_case 或 UPPER_SNAKE。

        Returns:
            是否成功删除。
        """
        # 转换关系类型为 Neo4j 格式（UPPER_SNAKE）
        neo4j_rel_type = RELATION_TYPE_TO_NEO4J.get(rel_type, rel_type.upper())

        cypher = f"MATCH (source {{id: $source_id}})-[r:{neo4j_rel_type}]->(target {{id: $target_id}}) DELETE r"

        with self._driver.session() as session:
            result: Neo4jResult = session.run(cypher, source_id=source_id, target_id=target_id)
            summary = result.consume()
            deleted_count = summary.counters.relationships_deleted

            return deleted_count > 0

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
            rel_type: 关系类型，支持 snake_case 或 UPPER_SNAKE（可选）。

        Returns:
            匹配的关系列表，每项包含 source_id, target_id, rel_type, properties。
        """
        # 构建 MATCH 子句
        match_clause = "MATCH (source)-[r]->(target)"

        # 构建动态 WHERE 条件
        where_conditions = []
        params: dict[str, Any] = {}

        if source_id:
            where_conditions.append("source.id = $source_id")
            params["source_id"] = source_id

        if target_id:
            where_conditions.append("target.id = $target_id")
            params["target_id"] = target_id

        if rel_type:
            # 转换关系类型为 Neo4j 格式（UPPER_SNAKE）
            neo4j_rel_type = RELATION_TYPE_TO_NEO4J.get(rel_type, rel_type.upper())
            # 需要重新构建 MATCH 子句来过滤关系类型
            match_clause = f"MATCH (source)-[r:{neo4j_rel_type}]->(target)"

        # 组装完整查询
        cypher = match_clause
        if where_conditions:
            cypher += " WHERE " + " AND ".join(where_conditions)

        cypher += (
            " RETURN source.id AS source_id, target.id AS target_id, type(r) AS rel_type, properties(r) AS properties"
        )

        with self._driver.session() as session:
            result: Neo4jResult = session.run(cypher, **params)
            return [record.data() for record in result]

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """执行 Cypher 查询。

        Args:
            cypher: Cypher 查询语句。
            params: 查询参数（可选）。

        Returns:
            查询结果列表。
        """
        params = params or {}

        with self._driver.session() as session:
            result: Neo4jResult = session.run(cypher, **params)
            return [record.data() for record in result]

    def ensure_constraints(self) -> None:
        """确保数据库存在必要的唯一约束。

        为 6 个实体标签创建 id 字段的唯一约束。
        如果约束已存在则忽略（IF NOT EXISTS）。
        """
        for label in ENTITY_LABELS:
            cypher = f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            with self._driver.session() as session:
                session.run(cypher)
            logger.debug(f"Ensured constraint for {label}")

        # Schema 版本约束
        schema_version_cypher = "CREATE CONSTRAINT IF NOT EXISTS FOR (n:SchemaVersion) REQUIRE n.version IS UNIQUE"
        with self._driver.session() as session:
            session.run(schema_version_cypher)
        logger.debug("Ensured constraint for SchemaVersion")

        # 注册当前 schema 版本
        register_schema_version(self)

    def cleanup_orphan_nodes(self) -> int:
        """清理无标签的孤立节点。

        这些节点通常是因为 MERGE 操作时未指定 label 而创建的。

        Returns:
            删除的节点数量。
        """
        cypher = "MATCH (n) WHERE labels(n) = [] DETACH DELETE n RETURN count(*) as deleted"

        with self._driver.session() as session:
            result: Neo4jResult = session.run(cypher)
            record = result.single()
            deleted_count = record["deleted"] if record else 0
            logger.info(f"Cleaned up {deleted_count} orphan nodes")

        return deleted_count

    def clear_all(self) -> int:
        """清空所有节点和关系。

        Returns:
            删除的节点数量。
        """
        batch_size = 10000
        total = 0
        cypher = "MATCH (n) WITH n LIMIT $batch_size DETACH DELETE n RETURN count(*) AS c"

        with self._driver.session() as session:
            while True:
                result: Neo4jResult = session.run(cypher, batch_size=batch_size)
                record = result.single()
                deleted = record["c"] if record else 0
                total += deleted
                if deleted == 0:
                    break

        logger.info("Cleared %d nodes from Neo4j", total)
        return total
