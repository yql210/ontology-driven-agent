"""v2.3.0 迁移：多仓库支持。

变更内容：
- 所有实体 Tag 新增 ``repoId`` 属性（string），用于多仓库归属打标
- 由 ``nebula_schema.create_tags()`` 的 ``common_fields`` 统一注入，迁移负责对
  已有 NebulaGraph 集群做在线 ALTER TAG ADD（幂等）

后端兼容：
- Neo4j: 无 schema 变更（属性是动态的，无需 ALTER）
- NebulaGraph: 对每个 VALID_ENTITY_LABELS 执行 ALTER TAG ADD repoId string
"""

from __future__ import annotations

import contextlib
import logging

from ontoagent.domain.schema import VALID_ENTITY_LABELS
from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase, is_nebula

logger = logging.getLogger(__name__)


class MultiRepoMigration(MigrationBase):
    """为所有实体 Tag 添加 repoId 属性，引入 RepositoryEntity 多仓库本体。"""

    version_from: str = "2.2.0"
    version_to: str = "2.3.0"
    description: str = "Add repoId property on all entity tags for multi-repo support"

    def upgrade(self, store: GraphStore) -> None:
        """对所有实体 Tag 执行 ALTER TAG ADD repoId string（幂等）。

        NebulaGraph ALTER TAG 已存在字段时返回语义错误，用 ``contextlib.suppress``
        保证迁移可重复执行。Neo4j 属性动态，无 schema 变更。
        """
        if not is_nebula(store):
            logger.info("v2.3.0 multi-repo migration: skip non-nebula store")
            return
        for label in VALID_ENTITY_LABELS:
            stmt = f"ALTER TAG `{label}` ADD (`repoId` string);"
            with contextlib.suppress(Exception):
                store.query(stmt)
        logger.info("v2.3.0 multi-repo migration: applied repoId on %d tags", len(VALID_ENTITY_LABELS))

    def downgrade(self, store: GraphStore) -> None:
        """对所有实体 Tag 执行 ALTER TAG DROP repoId（幂等）。"""
        if not is_nebula(store):
            logger.info("v2.3.0 multi-repo migration: skip non-nebula store on downgrade")
            return
        for label in VALID_ENTITY_LABELS:
            stmt = f"ALTER TAG `{label}` DROP (`repoId`);"
            with contextlib.suppress(Exception):
                store.query(stmt)
        logger.info("v2.3.0 multi-repo migration: dropped repoId on %d tags", len(VALID_ENTITY_LABELS))
