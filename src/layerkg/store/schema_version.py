"""Schema 版本追踪模块。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from layerkg.store.graph_store import GraphStore

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = "1.0.0"


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


def register_schema_version(store: GraphStore) -> None:
    """在 Neo4j 注册当前 schema 版本。使用 MERGE 保证幂等。

    Args:
        store: 图数据库存储实例。
    """
    from datetime import UTC, datetime

    applied_at = datetime.now(UTC).isoformat()
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
    """查询 Neo4j 中最新 SchemaVersion 节点的 version。

    Args:
        store: 图数据库存储实例。

    Returns:
        版本字符串，如果无版本节点则返回 None。
    """
    cypher = """
    MATCH (sv:SchemaVersion)
    RETURN sv.version AS version
    ORDER BY sv.applied_at DESC
    LIMIT 1
    """
    results = store.query(cypher)
    if results:
        return results[0]["version"]
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

    # 简单字符串比较（对 semver 够用）
    if db_version == CURRENT_SCHEMA_VERSION:
        return SchemaStatus.MATCH
    elif db_version < CURRENT_SCHEMA_VERSION:
        return SchemaStatus.BEHIND
    else:
        return SchemaStatus.AHEAD
