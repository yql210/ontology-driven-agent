from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool
from tenacity import (
    Retrying,
    retry_if_result,
    stop_after_delay,
    wait_fixed,
)

from ontoagent.domain.schema import RELATION_TYPE_TO_NEO4J
from ontoagent.store.cypher_adapter import CypherToNgqlAdapter
from ontoagent.store.graph_store import GraphStore
from ontoagent.store.nebula_schema import NebulaSchemaInitializer, _escape_prop_name

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


def _strip_nebula_quotes(val: Any) -> Any:
    """去除 NebulaGraph ``as_string()`` 返回值中多余的包裹引号。

    NebulaGraph 的 ``ValueWrapper.as_string()`` 对 FIXED_STRING 返回带引号的值
    （如 ``'"CodeEntity"'`` 而非 ``'CodeEntity'``）。本函数递归去除这些引号。

    对于 list，递归处理每个元素。
    """
    if isinstance(val, str):
        if len(val) >= 2 and val.startswith('"') and val.endswith('"'):
            return val[1:-1]
        return val
    if isinstance(val, list):
        return [_strip_nebula_quotes(item) for item in val]
    return val


def _format_value(value: Any) -> str:
    """把 Python 值转成 nGQL 字面量。

    OntoAgent 的 NebulaGraph schema 所有字段定义为 ``string`` 类型，
    因此所有非 None 值都转为带引号的字符串字面量（bool/int/float 也如此），
    避免 "Invalid data, may be wrong value type" 错误。

    - None → ``null``（UPSERT 时跳过该字段）
    - bool → ``"true"`` / ``"false"``（小写，避免 Python ``True``/``False`` 留入数据）
    - list / dict → JSON 字符串（避免 Python ``repr`` 格式 ``['a', 'b']``）
    - set → 排序后 JSON list（确定性序列化）
    - 其他 → ``"..."``（转义反斜杠和双引号后用双引号包裹）
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return '"true"' if value else '"false"'
    if isinstance(value, (list, dict, set, tuple)):
        import json as _json

        serializable = sorted(value) if isinstance(value, (set, frozenset)) else value
        s = _json.dumps(serializable, ensure_ascii=False)
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'
    # int/float/str 统一按字符串处理（schema 字段全是 string 类型）
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

    #: SHOW TAGS 探针的最长等待时间（秒）。NebulaGraph DDL 异步生效，初次创建
    #: Space/Tag 后需要等待 ~20s；120s 给足裕量。测试时可被 patch 为更小值。
    _SCHEMA_PROBE_TIMEOUT_SECONDS: int = 120

    #: 探针重试间隔（秒）。
    _SCHEMA_PROBE_WAIT_SECONDS: int = 2

    #: session 操作自动重连次数（网络断开/session 过期时）。
    _SESSION_RETRY_MAX: int = 3

    #: session 重连间隔（秒）。
    _SESSION_RETRY_DELAY: float = 1.0

    def __init__(
        self,
        host: str,
        port: int = 9669,
        user: str = "root",
        password: str = "nebula",
        space: str = "ontoagent",
        max_connection_pool_size: int = 10,
    ) -> None:
        """初始化连接池并自动确保 schema 就绪（Space/Tag/Edge/Index 已创建且 DDL 生效）。

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

        # 自动初始化 schema + 探针等待 DDL 生效；失败仅 warning，不阻塞实例化
        self._schema_ready: bool = False
        self._ensure_schema_ready()

    def _ensure_schema_ready(self) -> None:
        """首次实例化时确保 NebulaGraph Space/Tag/Edge/Index 已创建且 DDL 生效。

        流程：
        1. 若 ``self._schema_ready`` 已为 True，直接返回（缓存避免重复初始化）。
        2. 用原始 session（不走 ``_session_scope``，因为 space 可能尚未创建）调用
           :class:`NebulaSchemaInitializer.initialize()` 创建 Space + Tag + Edge + Index。
        3. 用 SHOW TAGS 探针重试等待 DDL 异步生效：``stop_after_delay=120s``、
           ``wait_fixed=2s``；succeeded 且非 empty 才视为 ready。
        4. 任何失败（initialize 返回 False 或探针超时）只打 ``warning``，不抛异常。

        设计理由：实例化失败会导致 CLI/Web/MCP 入口整体崩溃，远比"schema 未就绪"
        的下游错误更难诊断。允许后续操作自行报错，由调用方决定是否重试。
        """
        if self._schema_ready:
            return

        session = self._pool.get_session(self._user, self._password)
        try:
            initializer = NebulaSchemaInitializer(session, space_name=self._space)
            ok = initializer.initialize()
            if not ok:
                logger.warning(
                    "[NebulaStore] schema initialization failed for space '%s' (continuing anyway)",
                    self._space,
                )
                return

            if self._wait_for_schema_probe(session):
                self._schema_ready = True
                logger.info("[NebulaStore] schema ready for space '%s'", self._space)
            else:
                logger.warning(
                    "[NebulaStore] schema probe did not become ready within %ds for space '%s' (continuing anyway)",
                    self._SCHEMA_PROBE_TIMEOUT_SECONDS,
                    self._space,
                )
        finally:
            session.release()

    def _wait_for_schema_probe(self, session: Any) -> bool:
        """SHOW TAGS 探针：succeeded 且非 empty 视为 schema 生效。

        使用 tenacity ``Retrying`` 重试，``retry_if_result`` 在结果为 True（未 ready）
        时继续重试。返回最终探针结果（True=ready，False=超时未 ready）。
        """
        # 探针：SHOW TAGS succeeded 且非 empty → schema 已生效
        def _probe_not_ready() -> bool:
            """返回 True 表示 schema 还没 ready（需重试），False 表示 ready。"""
            try:
                result = session.execute("SHOW TAGS;")
            except Exception as exc:
                logger.debug("[NebulaStore] SHOW TAGS probe exception: %s", exc)
                return True
            if not result.is_succeeded():
                return True
            try:
                return bool(result.is_empty())
            except Exception:
                return True

        retryer = Retrying(
            retry=retry_if_result(lambda not_ready: not_ready),
            stop=stop_after_delay(self._SCHEMA_PROBE_TIMEOUT_SECONDS),
            wait=wait_fixed(self._SCHEMA_PROBE_WAIT_SECONDS),
            reraise=False,
        )
        try:
            final_result = retryer(_probe_not_ready)
            # Retrying 在成功时返回最后一次调用结果；探针返回 False 表示 ready
            return final_result is False
        except Exception as exc:
            logger.debug("[NebulaStore] schema probe retry exhausted: %s", exc)
            return False

    def close(self) -> None:
        """关闭连接池。"""
        self._pool.close()
        logger.debug("[NebulaStore] connection pool closed")

    def health_check(self) -> dict:
        """检查 NebulaGraph 连接健康度。

        Returns:
            包含 ``connected`` / ``space`` / ``tag_count`` / ``edge_count`` 的 dict。
        """
        result: dict = {"connected": False, "space": self._space, "tag_count": 0, "edge_count": 0}
        try:
            with self._session_scope() as session:
                tags = session.execute("SHOW TAGS;")
                if tags.is_succeeded():
                    result["connected"] = True
                    try:
                        result["tag_count"] = tags.row_size()
                    except Exception:
                        pass
                edges = session.execute("SHOW EDGES;")
                if edges.is_succeeded():
                    try:
                        result["edge_count"] = edges.row_size()
                    except Exception:
                        pass
        except Exception as exc:
            result["error"] = str(exc)
            logger.warning("[NebulaStore] health_check failed: %s", exc)
        return result

    def __enter__(self) -> NebulaGraphStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        """获取 session、执行 USE SPACE、yield、finally release。

        包含自动重连：如果 ``get_session`` 或 ``session.execute(USE)`` 抛出
        网络异常（``IOError``/``OSError``），最多重试 ``_SESSION_RETRY_MAX`` 次。
        注意：query 级别的 ``RuntimeError``（如语法错误）不在重试范围内。
        """
        last_exc: Exception | None = None
        for attempt in range(self._SESSION_RETRY_MAX):
            session = None
            try:
                session = self._pool.get_session(self._user, self._password)
                session.execute(f"USE `{self._space}`;")
                break  # 连接成功，跳出重试循环
            except (IOError, OSError) as exc:
                last_exc = exc
                logger.warning(
                    "[NebulaStore] connect attempt %d/%d failed: %s",
                    attempt + 1,
                    self._SESSION_RETRY_MAX,
                    exc,
                )
                if session:
                    try:
                        session.release()
                    except Exception:
                        pass
                import time as _time

                _time.sleep(self._SESSION_RETRY_DELAY)
        else:
            msg = f"NebulaGraph connection failed after {self._SESSION_RETRY_MAX} retries: {last_exc}"
            raise RuntimeError(msg)

        try:
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
                    # schema 字段不匹配是编程错误（entity_to_dict 产出 schema 未声明的字段）
                    # 不再静默降级为空节点——那会把「数据丢失」隐藏为「图结构正常」
                    msg = (
                        f"NebulaGraph merge_node UPSERT failed: {err} | "
                        f"label={label} vid={vid} stmt={stmt}. "
                        f"If 'Tag prop not found', add the missing field to "
                        f"domain/schema.py::_EXTRA_FIELDS['{label}']."
                    )
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
        """FETCH PROP ON * ``vid`` YIELD id, tags, properties — 返回 dict 或 None。

        返回的 dict 包含 ``id``、``label``（第一个 Tag 名，对应 Neo4j label 语义）
        以及所有 Tag 属性的合并。若 vertex 有多个 Tag，属性按键合并（后者覆盖前者）。

        注意：NebulaGraph 的 ``as_string()`` 对 string 类型返回带引号的值（如 ``"CodeEntity"``），
        本方法自动去除 Tag 名和属性值的包裹引号。
        """
        with self._session_scope() as session:
            # tags(vertex) 返回该 vertex 的所有 Tag 名列表
            stmt = (
                f'FETCH PROP ON * "{node_id}" '
                f"YIELD id(vertex) AS id, tags(vertex) AS tag_names, "
                f"properties(vertex) AS props;"
            )
            result = session.execute(stmt)
            if not result.is_succeeded():
                logger.error("[NebulaStore] get_node failed: %s", safe_error_msg(result))
                return None
            if result.is_empty():
                return None

            try:
                keys = result.keys()
                if not keys:
                    return None
                row: dict = {}
                for key in keys:
                    col_values = result.column_values(key)
                    value_wrapper = col_values[0] if col_values else None
                    row[key] = _unwrap_value(value_wrapper) if value_wrapper is not None else None

                # 提取 label（第一个 Tag 名，与 Neo4j labels()[0] 对齐）
                # tags(vertex) 返回 [["CodeEntity"]]，as_string 返回 '"CodeEntity"'（带引号）
                tag_names_raw = row.pop("tag_names", None)
                # tag_names_raw 可能是 list[ValueWrapper] 或 list[str]
                if isinstance(tag_names_raw, list):
                    tag_list = []
                    for item in tag_names_raw:
                        unwrapped = _unwrap_value(item) if not isinstance(item, (str, int, float, bool)) else item
                        tag_list.append(_strip_nebula_quotes(unwrapped))
                    if tag_list:
                        row["label"] = tag_list[0]
                        row["_labels"] = tag_list
                elif isinstance(tag_names_raw, (str,)) and tag_names_raw:
                    row["label"] = _strip_nebula_quotes(tag_names_raw)

                # 合并 props map
                props_raw = row.pop("props", None)
                # 先保存 id(vertex) 的值（VID），防止被 props 中的 null id 覆盖
                vid_value = row.get("id")
                if isinstance(vid_value, str) and vid_value:
                    vid_value = _strip_nebula_quotes(vid_value)

                if isinstance(props_raw, dict):
                    for k, v in props_raw.items():
                        unwrapped = _unwrap_value(v) if hasattr(v, "as_string") else v
                        # 去除 string 值的包裹引号
                        if isinstance(unwrapped, str):
                            unwrapped = _strip_nebula_quotes(unwrapped)
                        row[k] = unwrapped

                # 恢复 VID（props map 中的 id 字段是 __NULL__，因为 id 不是 Tag 属性）
                row["id"] = vid_value or node_id
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
            properties: 关系属性（可选）。支持 weight、affectScore、provenanceSource、
                confidence、extractedAt（与 Edge DDL 定义的属性对齐）。
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

        # 序列化关系属性（camelCase + format）
        props = _keys_to_camel_case(properties) if properties else {}
        # 过滤掉未知字段（只保留 Edge DDL 声明的 5 个通用属性）
        _EDGE_PROP_NAMES = {"weight", "affectScore", "provenanceSource", "confidence", "extractedAt"}
        filtered_props = {k: v for k, v in props.items() if k in _EDGE_PROP_NAMES}

        with self._session_scope() as session:
            # 先 DELETE 保证幂等（NebulaGraph 的 INSERT EDGE 是追加语义，rank 相同时旧值保留）
            # Edge 名加反引号：CONTAINS/IMPORTS 等是 nGQL 保留字，不加反引号会 EdgeNotFound
            delete_stmt = f'DELETE EDGE `{neo4j_rel_type}` "{source_id}"->"{target_id}"@0;'
            session.execute(delete_stmt)

            if filtered_props:
                # 带属性的 INSERT EDGE
                from ontoagent.store.nebula_schema import _escape_prop_name

                prop_names = ", ".join(_escape_prop_name(k) for k in filtered_props)
                prop_values = ", ".join(_format_value(v) for v in filtered_props.values())
                insert_stmt = (
                    f'INSERT EDGE `{neo4j_rel_type}` ({prop_names}) '
                    f'VALUES "{source_id}"->"{target_id}"@0:({prop_values});'
                )
            else:
                insert_stmt = f'INSERT EDGE `{neo4j_rel_type}`() VALUES "{source_id}"->"{target_id}"@0:();'
            result = session.execute(insert_stmt)
            if not result.is_succeeded():
                msg = f"NebulaGraph merge_relation failed: {safe_error_msg(result)}"
                raise RuntimeError(msg)

        logger.debug(
            "[NebulaStore] merged edge %s-[%s]->%s (props=%s)",
            source_id,
            neo4j_rel_type,
            target_id,
            list(filtered_props.keys()),
        )
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
                # {key} 模板替换（已有逻辑）
                final_stmt = final_stmt.replace("{" + key + "}", str(value))
                # $key 参数化替换（兜底：NebulaGraph 不支持 $param 语法）
                dollar_key = "$" + key
                if dollar_key in final_stmt:
                    if isinstance(value, (list, dict, tuple, set)):
                        msg = (
                            f"NebulaGraph does not support list/dict $param: {dollar_key}. "
                            "Use semantic API (find_nodes/find_neighbors) instead."
                        )
                        raise TypeError(msg)
                    # 按 Python 类型序列化：str 带引号，数值/bool 不带
                    if isinstance(value, str):
                        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                        formatted = f'"{escaped}"'
                    elif isinstance(value, bool):
                        formatted = "true" if value else "false"
                    elif value is None:
                        formatted = "null"
                    else:
                        formatted = str(value)
                    final_stmt = final_stmt.replace(dollar_key, formatted)
                    logger.warning(
                        '[NebulaStore] $param fallback used for key="%s" in query. '
                        "This should be migrated to semantic API.",
                        key,
                    )

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

    # ---- 批量 / 维护方法（GraphStore ABC 之外的扩展，与 Neo4jGraphStore 接口对齐） ----

    def merge_nodes_batch(
        self,
        label: str,
        properties_list: list[dict],
        batch_size: int = 200,
    ) -> int:
        """批量 ``INSERT VERTEX`` 写入节点。

        NebulaGraph 的 INSERT VERTEX 对同一 VID 是覆盖语义，等效于 MERGE 的幂等性。
        每批生成一条多值 INSERT VERTEX 语句，属性列表只列名称（不带类型），
        值通过 :func:`_format_value` 序列化为 nGQL 字面量。

        Args:
            label: 实体标签，必须为合法标识符（防注入）。
            properties_list: 节点属性字典列表，每项必须包含 ``id``。
            batch_size: 每批处理数量，默认 200。

        Returns:
            写入的节点总数。

        Raises:
            ValueError: 当 ``label`` 含非法字符，或任一 dict 缺 ``id``。
            RuntimeError: 当 NebulaGraph 执行失败。
        """
        if not re.match(r"^[A-Za-z_]\w*$", label):
            msg = f"Invalid label: {label}"
            raise ValueError(msg)

        if not properties_list:
            return 0

        # 校验所有 dict 含 id（提前 fail-fast，避免半成功）
        for i, props in enumerate(properties_list):
            if "id" not in props:
                msg = f"properties_list[{i}] must contain 'id'"
                raise ValueError(msg)

        total = len(properties_list)
        written = 0

        with self._session_scope() as session:
            for i in range(0, total, batch_size):
                batch = properties_list[i : i + batch_size]
                batch_camel = [_keys_to_camel_case(p) for p in batch]

                # 收集本批所有属性名（除 id）的并集；缺失字段写 null。
                # NebulaGraph INSERT VERTEX 要求所有 VALUES 的属性列表一致，
                # 因此取并集而非首 dict 的 keys（避免丢字段）。
                all_keys: list[str] = []
                seen: set[str] = set()
                for d in batch_camel:
                    for k in d:
                        if k != "id" and k not in seen:
                            seen.add(k)
                            all_keys.append(k)

                # 构造 VALUES 子句：每个节点一行 "vid":(v1, v2, ...)
                values_parts: list[str] = []
                for d in batch_camel:
                    vid = d["id"]
                    values = [_format_value(d.get(k)) for k in all_keys]
                    values_str = ", ".join(values) if values else ""
                    values_parts.append(f'"{vid}":({values_str})')

                props_clause = f"({', '.join(all_keys)})" if all_keys else "()"
                values_clause = ", ".join(values_parts)
                stmt = f"INSERT VERTEX `{label}`{props_clause} VALUES {values_clause};"

                result = session.execute(stmt)
                if not result.is_succeeded():
                    err = safe_error_msg(result)
                    msg = f"NebulaGraph merge_nodes_batch failed: {err} | stmt={stmt}"
                    raise RuntimeError(msg)

                written += len(batch_camel)
                logger.info("[NebulaStore] batch wrote %d/%d %s", written, total, label)

        return written

    def merge_relations_batch(
        self,
        relations: list[dict],
        batch_size: int = 200,
    ) -> int:
        """批量 ``INSERT EDGE`` 写入关系。

        按 ``rel_type`` 分组（NebulaGraph INSERT EDGE 按 edge type 批量），
        每组按 ``batch_size`` 分批执行。Edge 属性（weight/affectScore/
        provenanceSource/confidence/extractedAt）在写入时序列化为 Edge 列。

        Args:
            relations: 关系数据列表，每项含 ``source_id``/``target_id``/``rel_type``，
                可选 ``source_label``/``target_label``/``properties``。
            batch_size: 每批处理数量，默认 200。

        Returns:
            写入的关系总数。

        Raises:
            ValueError: 当 ``rel_type`` 或 label 含非法字符。
            RuntimeError: 当 NebulaGraph 执行失败。
        """
        if not relations:
            return 0

        _EDGE_PROP_NAMES = {"weight", "affectScore", "provenanceSource", "confidence", "extractedAt"}
        from ontoagent.store.nebula_schema import _escape_prop_name

        # 按 rel_type 分组（NebulaGraph INSERT EDGE 按 edge type 批量）
        groups: dict[str, list[dict]] = {}
        for rel in relations:
            source_label: str = rel.get("source_label", "")
            target_label: str = rel.get("target_label", "")
            rel_type: str = rel["rel_type"]
            neo4j_rel_type = RELATION_TYPE_TO_NEO4J.get(rel_type, rel_type.upper())

            if source_label and not re.match(r"^[A-Za-z_]\w*$", source_label):
                msg = f"Invalid source_label: {source_label}"
                raise ValueError(msg)
            if target_label and not re.match(r"^[A-Za-z_]\w*$", target_label):
                msg = f"Invalid target_label: {target_label}"
                raise ValueError(msg)
            if not re.match(r"^[A-Z_]+$", neo4j_rel_type):
                msg = f"Invalid relation type: {neo4j_rel_type}"
                raise ValueError(msg)

            groups.setdefault(neo4j_rel_type, []).append(rel)

        total = len(relations)
        written = 0

        with self._session_scope() as session:
            for edge_type, group in groups.items():
                # 先收集本组所有属性 key 的并集（与 batch merge_nodes 同理）
                all_prop_keys: list[str] = []
                seen_keys: set[str] = set()
                for rel in group:
                    raw_props = rel.get("properties") or {}
                    camel_props = _keys_to_camel_case(raw_props)
                    filtered = {k: v for k, v in camel_props.items() if k in _EDGE_PROP_NAMES}
                    for k in filtered:
                        if k not in seen_keys:
                            seen_keys.add(k)
                            all_prop_keys.append(k)

                for i in range(0, len(group), batch_size):
                    batch = group[i : i + batch_size]
                    # 构造 VALUES 子句
                    if all_prop_keys:
                        prop_cols = ", ".join(_escape_prop_name(k) for k in all_prop_keys)
                        values_parts = []
                        for r in batch:
                            raw_props = r.get("properties") or {}
                            camel_props = _keys_to_camel_case(raw_props)
                            filtered = {k: v for k, v in camel_props.items() if k in _EDGE_PROP_NAMES}
                            vals = ", ".join(_format_value(filtered.get(k)) for k in all_prop_keys)
                            values_parts.append(f'"{r["source_id"]}"->"{r["target_id"]}"@0:({vals})')
                        values_clause = ", ".join(values_parts)
                        stmt = f"INSERT EDGE `{edge_type}`({prop_cols}) VALUES {values_clause};"
                    else:
                        values_parts = [f'"{r["source_id"]}"->"{r["target_id"]}"@0:()' for r in batch]
                        values_clause = ", ".join(values_parts)
                        stmt = f"INSERT EDGE `{edge_type}`() VALUES {values_clause};"

                    result = session.execute(stmt)
                    if not result.is_succeeded():
                        err = safe_error_msg(result)
                        msg = f"NebulaGraph merge_relations_batch failed: {err} | stmt={stmt}"
                        raise RuntimeError(msg)

                    written += len(batch)
                    logger.info(
                        "[NebulaStore] batch wrote %d/%d relations (%s)",
                        written,
                        total,
                        edge_type,
                    )

        return written

    def ensure_constraints(self) -> None:
        """确保 NebulaGraph schema 存在（Space + Tag + Edge + Index）。

        NebulaGraph 的 VID 天然全局唯一（不需要 SQL 唯一约束），
        本方法通过 :class:`NebulaSchemaInitializer` 创建 schema（全部幂等）。
        ``register_schema_version`` 暂未支持（schema_version.py 的 MERGE 还未改造，
        Phase 7b 处理）。

        Raises:
            RuntimeError: 当 schema 初始化失败。
        """
        # 不走 _session_scope（它会先 USE SPACE，但 space 可能尚未创建）。
        # NebulaSchemaInitializer.ensure_space() 会负责 CREATE SPACE IF NOT EXISTS。
        session = self._pool.get_session(self._user, self._password)
        try:
            initializer = NebulaSchemaInitializer(session, space_name=self._space)
            ok = initializer.initialize()
            if not ok:
                msg = "NebulaSchemaInitializer.initialize() failed during ensure_constraints"
                raise RuntimeError(msg)
        finally:
            session.release()
        logger.info("[NebulaStore] ensure_constraints: schema initialized for space '%s'", self._space)

    def clear_all(self) -> int:
        """清空当前 Space 内所有数据（保留 schema）。

        使用 ``CLEAR SPACE`` 语句，删除所有点/边但保留 Tag/Edge/Index 定义。
        CLEAR SPACE 是异步操作且不返回删除数量，固定返回 0。

        Returns:
            固定返回 0（CLEAR SPACE 不返回删除数量）。

        Raises:
            RuntimeError: 当 NebulaGraph 执行失败。
        """
        with self._session_scope() as session:
            stmt = f"CLEAR SPACE `{self._space}`;"
            result = session.execute(stmt)
            if not result.is_succeeded():
                err = safe_error_msg(result)
                msg = f"NebulaGraph clear_all failed: {err} | stmt={stmt}"
                raise RuntimeError(msg)
        logger.info("[NebulaStore] clear_all: space '%s' cleared", self._space)
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
