"""v1.2.0 迁移：添加跨服务桥接关系约束。"""

from __future__ import annotations

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase

MIGRATION_120 = {
    "version": "1.2.0",
    "description": "Add cross-service bridge relations",
    "depends_on": "1.1.0",
    "up": [
        "CREATE CONSTRAINT calls_service_unique IF NOT EXISTS FOR ()-[r:CALLS_SERVICE]-() REQUIRE r IS UNIQUE",
        "CREATE CONSTRAINT publishes_to_unique IF NOT EXISTS FOR ()-[r:PUBLISHES_TO]-() REQUIRE r IS UNIQUE",
        "CREATE CONSTRAINT consumed_by_unique IF NOT EXISTS FOR ()-[r:CONSUMED_BY]-() REQUIRE r IS UNIQUE",
    ],
    "down": [
        "DROP CONSTRAINT calls_service_unique IF EXISTS",
        "DROP CONSTRAINT publishes_to_unique IF EXISTS",
        "DROP CONSTRAINT consumed_by_unique IF EXISTS",
    ],
}


class CrossServiceRelationsMigration(MigrationBase):
    """添加 CALLS_SERVICE / PUBLISHES_TO / CONSUMED_BY 关系的存在性约束。"""

    version_from: str = "1.1.0"
    version_to: str = "1.2.0"
    description: str = "Add cross-service bridge relations"

    def upgrade(self, store: GraphStore) -> None:
        for statement in MIGRATION_120["up"]:
            store.query(statement)

    def downgrade(self, store: GraphStore) -> None:
        for statement in MIGRATION_120["down"]:
            store.query(statement)
