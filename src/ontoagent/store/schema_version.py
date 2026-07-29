"""Schema 版本追踪模块。

后端兼容：同时支持 Neo4j（Cypher MERGE）和 NebulaGraph（UPSERT VERTEX）。
通过 ``isinstance(store, NebulaGraphStore)`` 检测后端类型，选择合适的查询语法。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from ontoagent.store.graph_store import GraphStore

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = "2.1.0"


class SchemaStatus(Enum):
    """Schema 版本状态。"""

    EMPTY = "empty"
    MATCH = "match"
    BEHIND = "behind"
    AHEAD = "ahead"


@dataclass
class SchemaVersionInfo:
    """Schema 版本信息。"""

    version: str
    description: str
    applied_at: str


def _is_nebula(store: GraphStore) -> bool:
    """检测 store 是否为 NebulaGraphStore（避免硬 import 循环）。"""
    return type(store).__name__ == "NebulaGraphStore"


def register_schema_version(store: GraphStore) -> None:
    """在图数据库注册当前 schema 版本。幂等。

    - Neo4j: MERGE (sv:SchemaVersion {version: $version}) SET ...
    - NebulaGraph: UPSERT VERTEX ON SchemaVersion "schema_version" SET ...

    Args:
        store: 图数据库存储实例。
    """
    from datetime import UTC, datetime

    applied_at = datetime.now(UTC).isoformat()

    if _is_nebula(store):
        # NebulaGraph: UPSERT VERTEX（SchemaVersion Tag 已由 nebula_schema.py 创建）
        vid = f"schema_version_{CURRENT_SCHEMA_VERSION.replace('.', '_')}"
        ngql = (
            f'UPSERT VERTEX ON `SchemaVersion` "{vid}" '
            f'SET `version` = "{CURRENT_SCHEMA_VERSION}", '
            f'`description` = "初始本体：6实体11关系+语义约束+溯源", '
            f'`applied_at` = "{applied_at}";'
        )
        store.query(ngql)
    else:
        # Neo4j: Cypher MERGE
        cypher = """
        MERGE (sv:SchemaVersion {version: $version})
        SET sv.description = $description,
            sv.applied_at = $applied_at
        """
        store.query(
            cypher,
            {
                "version": CURRENT_SCHEMA_VERSION,
                "description": "初始本体：6实体11关系+语义约束+溯源",
                "applied_at": applied_at,
            },
        )
    logger.info("Registered schema version %s", CURRENT_SCHEMA_VERSION)


def get_current_db_version(store: GraphStore) -> str | None:
    """查询最新 SchemaVersion 节点的 version。

    Args:
        store: 图数据库存储实例。

    Returns:
        版本字符串，如果无版本节点则返回 None。
    """
    if _is_nebula(store):
        # NebulaGraph: FETCH 或 MATCH 查 SchemaVersion Tag
        ngql = (
            "MATCH (sv:`SchemaVersion`) "
            "RETURN sv.version AS version, sv.applied_at AS applied_at "
            "ORDER BY applied_at DESC LIMIT 1;"
        )
    else:
        ngql = """
        MATCH (sv:SchemaVersion)
        RETURN sv.version AS version
        ORDER BY sv.applied_at DESC
        LIMIT 1
        """
    results = store.query(ngql)
    if results:
        return results[0].get("version")
    return None


def check_schema_version(store: GraphStore) -> SchemaStatus:
    """检查 DB 版本与 CURRENT_SCHEMA_VERSION 的关系。

    Args:
        store: 图数据库存储实例。

    Returns:
        SchemaStatus 枚举值。
    """
    db_version = get_current_db_version(store)
    if db_version is None:
        return SchemaStatus.EMPTY

    if db_version == CURRENT_SCHEMA_VERSION:
        return SchemaStatus.MATCH
    elif db_version < CURRENT_SCHEMA_VERSION:
        return SchemaStatus.BEHIND
    else:
        return SchemaStatus.AHEAD
