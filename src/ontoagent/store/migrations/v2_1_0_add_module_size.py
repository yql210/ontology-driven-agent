"""v2.1.0 迁移：为 ModuleEntity 添加 size 字段。

后端兼容：
- Neo4j: 无 schema 变更（属性是动态的，无需 ALTER）
- NebulaGraph: ALTER TAG ModuleEntity ADD size string（幂等）
"""

from __future__ import annotations

import contextlib
import logging

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase, is_nebula

logger = logging.getLogger(__name__)


class ModuleEntitySizeMigration(MigrationBase):
    """为 ModuleEntity 添加 size 属性（模块内实体数量）。"""

    version_from: str = "2.0.0"
    version_to: str = "2.1.0"
    description: str = "Add ModuleEntity.size property for clustering metadata"

    def upgrade(self, store: GraphStore) -> None:
        if is_nebula(store):
            # NebulaGraph: ALTER TAG ADD size 字段（用 try/except 保证幂等）
            stmt = "ALTER TAG `ModuleEntity` ADD (`size` string);"
            with contextlib.suppress(Exception):
                store.query(stmt)
        # Neo4j: 无 schema 变更（属性是动态的）

    def downgrade(self, store: GraphStore) -> None:
        if is_nebula(store):
            stmt = "ALTER TAG `ModuleEntity` DROP (`size`);"
            with contextlib.suppress(Exception):
                store.query(stmt)
        # Neo4j: 无 schema 变更
