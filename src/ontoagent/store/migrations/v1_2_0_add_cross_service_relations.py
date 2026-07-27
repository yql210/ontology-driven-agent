"""v1.2.0 迁移：添加跨服务桥接关系约束。

后端兼容：
- Neo4j: CREATE CONSTRAINT ... REQUIRE r IS UNIQUE（关系约束）
- NebulaGraph: 无关系级约束（Edge rank 机制天然去重），跳过即可
"""

from __future__ import annotations

import contextlib
import logging

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase, is_nebula

logger = logging.getLogger(__name__)

MIGRATION_120 = {
    "version": "1.2.0",
    "description": "Add cross-service bridge relations",
    "depends_on": "1.1.0",
    "up": {
        "neo4j": [
            "CREATE CONSTRAINT calls_service_unique IF NOT EXISTS FOR ()-[r:CALLS_SERVICE]-() REQUIRE r IS UNIQUE",
            "CREATE CONSTRAINT publishes_to_unique IF NOT EXISTS FOR ()-[r:PUBLISHES_TO]-() REQUIRE r IS UNIQUE",
            "CREATE CONSTRAINT consumed_by_unique IF NOT EXISTS FOR ()-[r:CONSUMED_BY]-() REQUIRE r IS UNIQUE",
        ],
        "nebula": [],  # NebulaGraph 无关系级约束
    },
    "down": {
        "neo4j": [
            "DROP CONSTRAINT calls_service_unique IF EXISTS",
            "DROP CONSTRAINT publishes_to_unique IF EXISTS",
            "DROP CONSTRAINT consumed_by_unique IF EXISTS",
        ],
        "nebula": [],
    },
}


class CrossServiceRelationsMigration(MigrationBase):
    """添加 CALLS_SERVICE / PUBLISHES_TO / CONSUMED_BY 关系的存在性约束。"""

    version_from: str = "1.1.0"
    version_to: str = "1.2.0"
    description: str = "Add cross-service bridge relations"

    def upgrade(self, store: GraphStore) -> None:
        if is_nebula(store):
            # NebulaGraph 无关系级约束，Edge rank=0 的 DELETE+INSERT 语义已保证幂等
            logger.info("[Migration v1.2.0] NebulaGraph: skipping relationship constraints (not needed)")
            return
        for statement in MIGRATION_120["up"]["neo4j"]:
            with contextlib.suppress(Exception):
                # Neo4j < 5.7 doesn't support relationship-level constraints.
                store.query(statement)

    def downgrade(self, store: GraphStore) -> None:
        if is_nebula(store):
            return
        for statement in MIGRATION_120["down"]["neo4j"]:
            with contextlib.suppress(Exception):
                store.query(statement)
