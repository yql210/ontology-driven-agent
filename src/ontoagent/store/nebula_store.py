from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

from ontoagent.domain.schema import RELATION_TYPE_TO_NEO4J
from ontoagent.store.graph_store import GraphStore

if TYPE_CHECKING:
    from collections.abc import Iterator

    from nebula3.gclient.net.SessionPool import Session

logger = logging.getLogger(__name__)


def _snake_to_camel(name: str) -> str:
    """snake_case → camelCase。"""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _keys_to_camel_case(d: dict) -> dict:
    """将 dict 的所有 key 从 snake_case 转 camelCase。

    ``id`` / ``source_id`` / ``target_id`` 作为保留 key 不转。
    """
    reserved = frozenset({"id", "source_id", "target_id"})
    result: dict = {}
    for key, value in d.items():
        new_key = key if key in reserved else _snake_to_camel(key)
        if isinstance(value, dict):
            result[new_key] = _keys_to_camel_case(value)
        else:
            result[new_key] = value
    return result


def _format_value(value: Any) -> str:
    """把 Python 值转成 nGQL 字面量。

    - str → ``"..."``（双引号；对内部双引号不做转义，调用方需保证不含双引号）
    - bool → ``true`` / ``false``
    - None → ``null``（NebulaGraph 用 NULL，UPSERT 时跳过该字段）
    - int/float → 数值字面量
    - 其他 → str(value)（best-effort）
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    # 默认按字符串处理（用双引号包裹）
    return f'"{value}"'


class NebulaGraphStore(GraphStore):
    """GraphStore 的 NebulaGraph 实现。

    使用 ConnectionPool + Session 管理。每次操作在 _session_scope 中获取 session、
    执行 USE SPACE、release。所有写入操作通过 INSERT/UPSERT 完成。

    NebulaGraph 不支持参数化查询，所有变量通过字符串拼接注入；调用方需保证
    标签、关系类型、ID 通过白名单校验。
    """

    def __init__(
        self,
        host: str,
        port: int = 9669,
        user: str = "root",
        password: str = "nebula",
        space: str = "ontoagent",
        max_connection_pool_size: int = 10,
    ) -> None:
        """初始化连接池（不在这里 USE SPACE）。

        Args:
            host: NebulaGraph 服务地址。
            port: NebulaGraph 服务端口，默认 9669。
            user: 用户名。
            password: 密码。
            space: 目标 Space 名称。
            max_connection_pool_size: 连接池上限，默认 10。
        """
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._space = space

        config = Config()
        config.max_connection_pool_size = max_connection_pool_size
        self._pool = ConnectionPool()
        ok = self._pool.init([(host, port)], config)
        if not ok:
            msg = f"Failed to init NebulaGraph connection pool to {host}:{port}"
            raise RuntimeError(msg)
        logger.debug("[NebulaStore] connection pool initialized to %s:%d", host, port)

    def close(self) -> None:
        """关闭连接池。"""
        self._pool.close()
        logger.debug("[NebulaStore] connection pool closed")

    def __enter__(self) -> NebulaGraphStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        """获取 session、执行 USE SPACE、yield、finally release。"""
        session = self._pool.get_session(self._user, self._password)
        try:
            session.execute(f"USE `{self._space}`;")
            yield session
        finally:
            session.release()

    # ---- GraphStore ABC 实现 ----

    def merge_node(self, label: str, properties: dict) -> dict:
        """UPSERT VERTEX ON ``label`` ``vid`` SET ... — 幂等写入。

        Args:
            label: 实体标签，必须为合法标识符（防注入）。
            properties: 必须含 ``id``，作为 VID。

        Returns:
            合并后的属性（key 已 camelCase）。

        Raises:
            ValueError: 当 label 含非法字符或 properties 缺 ``id``。
        """
        if not re.match(r"^[A-Za-z_]\w*$", label):
            msg = f"Invalid label: {label}"
            raise ValueError(msg)
        if "id" not in properties:
            msg = "properties must contain 'id'"
            raise ValueError(msg)

        props = _keys_to_camel_case(properties)
        vid = props["id"]

        # 排除 id（id 已作为 VID），剩余属性拼 SET 子句
        set_parts = [f"{k} = {_format_value(v)}" for k, v in props.items() if k != "id"]
        set_clause = ", ".join(set_parts) if set_parts else ""

        if set_clause:
            stmt = f'UPSERT VERTEX ON {label} "{vid}" SET {set_clause};'
        else:
            # 无属性可设 → 仅 INSERT 占位（保证点存在）
            stmt = f'INSERT VERTEX {label}() VALUES "{vid}":();'

        with self._session_scope() as session:
            result = session.execute(stmt)
            if not result.is_succeeded():
                msg = f"NebulaGraph merge_node failed: {result.error_msg} | stmt={stmt}"
                raise RuntimeError(msg)

        logger.debug("[NebulaStore] merged node %s:%s", label, vid)
        return props

    def get_node(self, node_id: str) -> dict | None:
        """FETCH PROP ON * ``vid`` YIELD properties(vertex) — 返回 dict 或 None。"""
        with self._session_scope() as session:
            stmt = f'FETCH PROP ON * "{node_id}" YIELD id(vertex) AS id, properties(vertex) AS props;'
            result = session.execute(stmt)
            if not result.is_succeeded():
                logger.error("[NebulaStore] get_node failed: %s", result.error_msg)
                return None
            if result.is_empty():
                return None

            # 简化：取第一行；column_values("props") 在 NebulaGraph 中返回 NMap ValueWrapper
            # 这里 best-effort 取出所有列。生产环境使用 ResultSet.as_primitive() 转换。
            try:
                keys = result.keys()
                if not keys:
                    return None
                row: dict = {}
                for key in keys:
                    value_wrapper = result.column_values(key)
                    row[key] = _unwrap_value(value_wrapper)
                # 如果存在 props 列（map），合并展开
                if "props" in row and isinstance(row["props"], dict):
                    props = dict(row["props"])
                    props.setdefault("id", node_id)
                    return props
                row.setdefault("id", node_id)
                return row
            except Exception:
                logger.exception("[NebulaStore] get_node decode failed for vid=%s", node_id)
                return None

    def delete_node(self, node_id: str) -> bool:
        """DELETE VERTEX ``vid`` WITH EDGE。"""
        with self._session_scope() as session:
            stmt = f'DELETE VERTEX "{node_id}" WITH EDGE;'
            result = session.execute(stmt)
            return result.is_succeeded()

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
        """幂等 upsert 关系：DELETE EDGE + INSERT EDGE（固定 rank=0）。

        Args:
            source_id: 源 VID。
            target_id: 目标 VID。
            rel_type: 关系类型（snake_case 或 UPPER_SNAKE 均可）。
            properties: 关系属性（可选， NebulaGraph Edge type 当前定义为空，属性被忽略）。
            source_label: 占位参数（与 Neo4j 实现签名对齐；不影响 NebulaGraph 写入）。
            target_label: 占位参数。

        Returns:
            传入的 properties（或空 dict）。

        Raises:
            ValueError: 当 rel_type 或 label 含非法字符。
        """
        if source_label and not re.match(r"^[A-Za-z_]\w*$", source_label):
            msg = f"Invalid source_label: {source_label}"
            raise ValueError(msg)
        if target_label and not re.match(r"^[A-Za-z_]\w*$", target_label):
            msg = f"Invalid target_label: {target_label}"
            raise ValueError(msg)

        neo4j_rel_type = RELATION_TYPE_TO_NEO4J.get(rel_type, rel_type.upper())
        if not re.match(r"^[A-Z_]+$", neo4j_rel_type):
            msg = f"Invalid relation type: {neo4j_rel_type}"
            raise ValueError(msg)

        # NebulaGraph Edge type 当前定义为空，properties 暂无法写入（DDL 简化策略）
        # 若后续 Edge type 加属性，可在此扩展 SET
        with self._session_scope() as session:
            # 先 DELETE 保证幂等（NebulaGraph 的 INSERT EDGE 是追加语义，rank 相同时旧值保留）
            delete_stmt = f'DELETE EDGE {neo4j_rel_type} "{source_id}"->"{target_id}"@0;'
            insert_stmt = f'INSERT EDGE {neo4j_rel_type}() VALUES "{source_id}"->"{target_id}"@0:();'
            session.execute(delete_stmt)
            result = session.execute(insert_stmt)
            if not result.is_succeeded():
                msg = f"NebulaGraph merge_relation failed: {result.error_msg}"
                raise RuntimeError(msg)

        logger.debug("[NebulaStore] merged edge %s-[%s]->%s", source_id, neo4j_rel_type, target_id)
        return properties or {}

    def delete_relation(self, source_id: str, target_id: str, rel_type: str) -> bool:
        """DELETE EDGE ``rel_type`` ``src``->``tgt``@0。"""
        neo4j_rel_type = RELATION_TYPE_TO_NEO4J.get(rel_type, rel_type.upper())
        with self._session_scope() as session:
            stmt = f'DELETE EDGE {neo4j_rel_type} "{source_id}"->"{target_id}"@0;'
            result = session.execute(stmt)
            return result.is_succeeded()

    def get_relations(
        self,
        source_id: str | None = None,
        target_id: str | None = None,
        rel_type: str | None = None,
    ) -> list[dict]:
        """MATCH (a)-[r]->(b) WHERE ... RETURN ...

        根据 source_id/target_id/rel_type 组合 WHERE 条件。rel_type 自动转大写。
        """
        match_clause = "MATCH (a)-[r]->(b)"
        where_parts: list[str] = []
        if source_id:
            where_parts.append(f'id(a) == "{source_id}"')
        if target_id:
            where_parts.append(f'id(b) == "{target_id}"')

        if rel_type:
            neo4j_rel_type = RELATION_TYPE_TO_NEO4J.get(rel_type, rel_type.upper())
            match_clause = f"MATCH (a)-[r:{neo4j_rel_type}]->(b)"

        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        return_clause = " RETURN id(a) AS source_id, id(b) AS target_id, type(r) AS rel_type;"
        stmt = match_clause + where_clause + return_clause

        with self._session_scope() as session:
            result = session.execute(stmt)
            if not result.is_succeeded():
                logger.error("[NebulaStore] get_relations failed: %s", result.error_msg)
                return []
            if result.is_empty():
                return []
            return _resultset_to_dicts(result)

    def query(self, ngql: str, params: dict | None = None) -> list[dict]:
        """执行原生 nGQL 查询。

        NebulaGraph 不支持参数化查询，``params`` 仅做简单字符串替换（``{key}`` → 值）。
        调用方需自行保证语句安全。

        Args:
            ngql: nGQL 查询语句。
            params: 简单 ``{key}`` 模板替换字典（可选）。

        Returns:
            查询结果列表。

        Raises:
            RuntimeError: 当查询失败时。
        """
        final_stmt = ngql
        if params:
            for key, value in params.items():
                final_stmt = final_stmt.replace("{" + key + "}", str(value))

        with self._session_scope() as session:
            result = session.execute(final_stmt)
            if not result.is_succeeded():
                msg = f"NebulaGraph query failed: {result.error_msg} | stmt={final_stmt}"
                raise RuntimeError(msg)
            if result.is_empty():
                return []
            return _resultset_to_dicts(result)

    def cleanup_orphan_nodes(self) -> int:
        """MATCH (n) WHERE NOT (n)--() WITH n DELETE n — 清理无边孤立点。

        Returns:
            删除的节点数。
        """
        with self._session_scope() as session:
            stmt = "MATCH (n) WHERE NOT (n)--() WITH n LIMIT 1000 DELETE n RETURN count(*) AS deleted;"
            result = session.execute(stmt)
            if not result.is_succeeded() or result.is_empty():
                return 0
            try:
                vw = result.column_values("deleted")
                return _unwrap_int(vw)
            except Exception:
                logger.exception("[NebulaStore] cleanup_orphan_nodes decode failed")
                return 0


def _unwrap_value(value_wrapper: Any) -> Any:
    """从 NebulaGraph ValueWrapper 取出 Python 原始值（best-effort）。"""
    # as_string 优先（NebulaGraph 对所有 string 类型可成功转换）
    for caster in ("as_string", "as_int", "as_double", "as_bool"):
        method = getattr(value_wrapper, caster, None)
        if method is None:
            continue
        try:
            return method()
        except Exception:
            continue
    # 列表 / map
    if hasattr(value_wrapper, "as_list"):
        try:
            return value_wrapper.as_list()
        except Exception:
            pass
    if hasattr(value_wrapper, "as_map"):
        try:
            return value_wrapper.as_map()
        except Exception:
            pass
    return None


def _unwrap_int(value_wrapper: Any) -> int:
    """从 ValueWrapper 提取 int 值。"""
    if hasattr(value_wrapper, "as_int"):
        try:
            return int(value_wrapper.as_int())
        except Exception:
            pass
    # 退到 as_string 再转
    try:
        return int(value_wrapper.as_string())
    except Exception:
        return 0


def _resultset_to_dicts(result: Any) -> list[dict]:
    """把 ResultSet 转为 list[dict]（与 Neo4jStore.query 返回格式一致）。"""
    try:
        keys = result.keys()
        rows: list[dict] = []
        row_size = result.row_size() if hasattr(result, "row_size") else 0
        for _i in range(row_size):
            row_dict: dict = {}
            for key in keys:
                try:
                    vw = result.column_values(key)
                    row_dict[key] = _unwrap_value(vw)
                except Exception:
                    row_dict[key] = None
            rows.append(row_dict)
        return rows
    except Exception:
        logger.exception("[NebulaStore] resultset decode failed")
        return []
