from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

from ontoagent.domain.schema import RELATION_TYPE_TO_NEO4J
from ontoagent.store.cypher_adapter import CypherToNgqlAdapter
from ontoagent.store.graph_store import GraphStore
from ontoagent.store.nebula_schema import _escape_prop_name

logger = logging.getLogger(__name__)


def safe_error_msg(result: Any) -> str:
    """从 NebulaGraph ResultSet 取错误信息，兼容方法/属性两种形态。

    nebula3-python 不同版本里 ``error_msg`` 既可能是方法也可能是属性；
    MagicMock 测试里也可能直接赋为字符串。统一安全取值。
    """
    raw = getattr(result, "error_msg", "unknown error")
    if callable(raw):
        try:
            return str(raw())
        except Exception:
            return "unknown error"
    return str(raw)


if TYPE_CHECKING:
    from collections.abc import Iterator

    from nebula3.gclient.net.SessionPool import Session


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

    OntoAgent 的 NebulaGraph schema 所有字段定义为 ``string`` 类型，
    因此所有非 None 值都转为带引号的字符串字面量（bool/int/float 也如此），
    避免 "Invalid data, may be wrong value type" 错误。

    - None → ``null``（UPSERT 时跳过该字段）
    - 其他 → ``"..."``（转义反斜杠和双引号后用双引号包裹）
    """
    if value is None:
        return "null"
    # bool/int/float/str 统一按字符串处理（schema 字段全是 string 类型）
    s = str(value)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


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

        with self._session_scope() as session:
            if set_clause:
                stmt = f'UPSERT VERTEX ON `{label}` "{vid}" SET {set_clause};'
                result = session.execute(stmt)
                if not result.is_succeeded():
                    err = safe_error_msg(result)
                    # 容错：schema 字段不匹配（entity_to_dict 产出 schema 未定义的字段）
                    # 降级为 INSERT 占位节点，保证图结构完整，未知字段丢弃。
                    if "Tag prop not found" in err or "wrong value type" in err:
                        logger.warning(
                            "[NebulaStore] merge_node UPSERT failed (%s), "
                            "fallback to INSERT placeholder for %s:%s",
                            err, label, vid,
                        )
                        stmt = f'INSERT VERTEX `{label}`() VALUES "{vid}":();'
                        result = session.execute(stmt)
                        if not result.is_succeeded():
                            msg = f"NebulaGraph merge_node INSERT fallback failed: {safe_error_msg(result)} | stmt={stmt}"
                            raise RuntimeError(msg)
                    else:
                        msg = f"NebulaGraph merge_node failed: {err} | stmt={stmt}"
                        raise RuntimeError(msg)
            else:
                # 无属性可设 → 仅 INSERT 占位（保证点存在）
                stmt = f'INSERT VERTEX `{label}`() VALUES "{vid}":();'
                result = session.execute(stmt)
                if not result.is_succeeded():
                    msg = f"NebulaGraph merge_node failed: {safe_error_msg(result)} | stmt={stmt}"
                    raise RuntimeError(msg)

        logger.debug("[NebulaStore] merged node %s:%s", label, vid)
        return props

    def get_node(self, node_id: str) -> dict | None:
        """FETCH PROP ON * ``vid`` YIELD properties(vertex) — 返回 dict 或 None。"""
        with self._session_scope() as session:
            stmt = f'FETCH PROP ON * "{node_id}" YIELD id(vertex) AS id, properties(vertex) AS props;'
            result = session.execute(stmt)
            if not result.is_succeeded():
                logger.error("[NebulaStore] get_node failed: %s", safe_error_msg(result))
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
                    col_values = result.column_values(key)  # list of ValueWrapper
                    value_wrapper = col_values[0] if col_values else None
                    row[key] = _unwrap_value(value_wrapper) if value_wrapper is not None else None
                # 如果存在 props 列（map），合并展开（递归解包 ValueWrapper）
                if "props" in row and isinstance(row["props"], dict):
                    props_raw = row["props"]
                    props = {}
                    for k, v in props_raw.items():
                        props[k] = _unwrap_value(v) if hasattr(v, "as_string") else v
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
            # Edge 名加反引号：CONTAINS/IMPORTS 等是 nGQL 保留字，不加反引号会 EdgeNotFound
            delete_stmt = f'DELETE EDGE `{neo4j_rel_type}` "{source_id}"->"{target_id}"@0;'
            insert_stmt = f'INSERT EDGE `{neo4j_rel_type}`() VALUES "{source_id}"->"{target_id}"@0:();'
            session.execute(delete_stmt)
            result = session.execute(insert_stmt)
            if not result.is_succeeded():
                msg = f"NebulaGraph merge_relation failed: {safe_error_msg(result)}"
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
                logger.error("[NebulaStore] get_relations failed: %s", safe_error_msg(result))
                return []
            if result.is_empty():
                return []
            return _resultset_to_dicts(result)

    def query(self, ngql: str, params: dict | None = None) -> list[dict]:
        """执行原生 nGQL 查询。

        接受 Cypher 风格的查询（OntoAgent 内部各模块普遍使用的语法），先经
        :class:`CypherToNgqlAdapter` 自动转换为 nGQL，再交给 NebulaGraph 执行。
        转换是 best-effort：无法识别的查询原样下发。

        NebulaGraph 不支持参数化查询，``params`` 仅做简单字符串替换（``{key}`` → 值）。
        调用方需自行保证语句安全。

        Args:
            ngql: Cypher/nGQL 查询语句。
            params: 简单 ``{key}`` 模板替换字典（可选）。

        Returns:
            查询结果列表。

        Raises:
            RuntimeError: 当查询失败时。
        """
        adapter = CypherToNgqlAdapter()
        adapted = adapter.adapt(ngql, params)

        final_stmt = adapted
        if params:
            for key, value in params.items():
                final_stmt = final_stmt.replace("{" + key + "}", str(value))

        with self._session_scope() as session:
            result = session.execute(final_stmt)
            if not result.is_succeeded():
                logger.warning(
                    "[NebulaStore] query failed | original=%r | adapted=%r | error=%s",
                    ngql,
                    adapted,
                    safe_error_msg(result),
                )
                msg = f"NebulaGraph query failed: {safe_error_msg(result)} | stmt={final_stmt}"
                raise RuntimeError(msg)
            if result.is_empty():
                return []
            return _resultset_to_dicts(result)

    def cleanup_orphan_nodes(self) -> int:
        """MATCH (n) WHERE NOT (n)--() WITH n DELETE m — 清理无边孤立点。

        Returns:
            删除的节点数。
        """
        with self._session_scope() as session:
            stmt = "MATCH (n) WHERE NOT (n)--() WITH n LIMIT 1000 DELETE n RETURN count(*) AS deleted;"
            result = session.execute(stmt)
            if not result.is_succeeded() or result.is_empty():
                return 0
            try:
                col_values = result.column_values("deleted")  # list of ValueWrapper
                if not col_values:
                    return 0
                return _unwrap_int(col_values[0])
            except Exception:
                logger.exception("[NebulaStore] cleanup_orphan_nodes decode failed")
                return 0

    def update_node_property(self, node_id: str, key: str, value: Any) -> bool:
        """更新单个节点的单个属性。

        NebulaGraph 的 ``UPDATE VERTEX ON <tag>`` 必须指定 Tag，因此先 FETCH
        节点的 Tag 列表，取第一个 Tag 再 UPDATE。属性名自动从 snake_case
        转 camelCase，与 ``merge_node`` 命名约定一致；保留字自动加反引号。

        Args:
            node_id: 节点 VID。
            key: 属性名（snake_case 或 camelCase 均可）。
            value: 属性值。

        Returns:
            成功更新返回 True；节点不存在或更新失败返回 False。

        Raises:
            ValueError: 当 key 含非法字符（注入防护）。
        """
        camel_key = _snake_to_camel(key)
        if not re.match(r"^[A-Za-z_]\w*$", camel_key):
            msg = f"Invalid property key: {key}"
            raise ValueError(msg)
        escaped_key = _escape_prop_name(camel_key)
        formatted_value = _format_value(value)

        with self._session_scope() as session:
            # Step 1: FETCH 找节点的 Tag
            fetch_stmt = f'FETCH PROP ON * "{node_id}" YIELD tags(vertex) AS tags;'
            fetch_result = session.execute(fetch_stmt)
            if not fetch_result.is_succeeded() or fetch_result.is_empty():
                logger.warning("[NebulaStore] update_node_property: vertex not found vid=%s", node_id)
                return False

            try:
                col_values = fetch_result.column_values("tags")
                if not col_values:
                    return False
                tags = _unwrap_value(col_values[0])
                if isinstance(tags, str):
                    # 形如 "Tag1,Tag2" 或 "[Tag1,Tag2]" — 容错解析
                    tags = [t.strip().strip('"').strip("'") for t in tags.strip("[]").split(",") if t.strip()]
                if not isinstance(tags, list) or not tags:
                    return False
                tag = str(tags[0])
            except Exception:
                logger.exception("[NebulaStore] update_node_property decode tags failed vid=%s", node_id)
                return False

            # 防 Tag 名注入（虽然来自数据库，但稳健起见校验）
            if not re.match(r"^[A-Za-z_]\w*$", tag):
                logger.error("[NebulaStore] invalid tag from FETCH: %r", tag)
                return False

            # Step 2: UPDATE VERTEX ON tag SET tag.key = value
            update_stmt = f'UPDATE VERTEX ON {tag} "{node_id}" SET {tag}.{escaped_key} = {formatted_value};'
            update_result = session.execute(update_stmt)
            if not update_result.is_succeeded():
                logger.error(
                    "[NebulaStore] update_node_property failed: %s | stmt=%s",
                    safe_error_msg(update_result),
                    update_stmt,
                )
                return False

        logger.debug("[NebulaStore] updated vertex %s %s.%s = %s", node_id, tag, camel_key, value)
        return True


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
            raw_map = value_wrapper.as_map()
            return {k: _unwrap_value(v) if hasattr(v, "as_string") else v for k, v in raw_map.items()}
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
        for row_idx in range(row_size):
            row_dict: dict = {}
            for key in keys:
                try:
                    col_values = result.column_values(key)  # list of ValueWrapper
                    vw = col_values[row_idx] if row_idx < len(col_values) else None
                    row_dict[key] = _unwrap_value(vw) if vw is not None else None
                except Exception:
                    row_dict[key] = None
            rows.append(row_dict)
        return rows
    except Exception:
        logger.exception("[NebulaStore] resultset decode failed")
        return []
