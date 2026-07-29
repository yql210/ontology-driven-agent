"""v2.2.0 迁移：实体 ID 从随机 UUID 改为内容派生稳定哈希。

无 schema 变更：
- 实体 Tag/Label 的 ``id`` 字段类型不变（仍是 string）
- VID 长度变化（UUID v4 36 字符 → SHA256 截断 32 字符）不影响 Tag schema
- 仅由 runner 自动调用 ``register_schema_version`` 注册新版本号
"""

from __future__ import annotations

import logging

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase

logger = logging.getLogger(__name__)


class StableEntityIdMigration(MigrationBase):
    """占位迁移：仅升级版本号，无 DDL 变更。"""

    version_from: str = "2.1.0"
    version_to: str = "2.2.0"
    description: str = "Switch entity id from random UUID v4 to content-derived stable hash (no DDL)"

    def upgrade(self, store: GraphStore) -> None:
        # 无 schema 变更；runner 会在迁移后自动调用 register_schema_version。
        logger.info("v2.2.0 stable-entity-id migration: no DDL required")

    def downgrade(self, store: GraphStore) -> None:
        # 无 schema 变更；回滚时也只需 register_schema_version（由 runner 调用）。
        logger.info("v2.2.0 stable-entity-id migration: no DDL required on downgrade")
