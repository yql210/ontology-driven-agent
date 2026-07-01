"""v2.0.0 迁移：添加业务能力与流程实体及关系。"""

from __future__ import annotations

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase

MIGRATION_200 = {
    "version": "2.0.0",
    "description": "Add CapabilityEntity, ProcessEntity, and business capability relations",
    "depends_on": "1.2.0",
    "up": [
        # 新实体标签的唯一性约束
        "CREATE CONSTRAINT capability_unique IF NOT EXISTS FOR (n:CapabilityEntity) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT process_unique IF NOT EXISTS FOR (n:ProcessEntity) REQUIRE n.id IS UNIQUE",
        # V5 业务能力关系约束
        "CREATE CONSTRAINT produces_unique IF NOT EXISTS FOR ()-[r:PRODUCES]-() REQUIRE r IS UNIQUE",
        "CREATE CONSTRAINT consumes_unique IF NOT EXISTS FOR ()-[r:CONSUMES]-() REQUIRE r IS UNIQUE",
        "CREATE CONSTRAINT composes_into_unique IF NOT EXISTS FOR ()-[r:COMPOSES_INTO]-() REQUIRE r IS UNIQUE",
        "CREATE CONSTRAINT realized_by_unique IF NOT EXISTS FOR ()-[r:REALIZED_BY]-() REQUIRE r IS UNIQUE",
        "CREATE CONSTRAINT precedes_unique IF NOT EXISTS FOR ()-[r:PRECEDES]-() REQUIRE r IS UNIQUE",
        "CREATE CONSTRAINT equivalent_to_unique IF NOT EXISTS FOR ()-[r:EQUIVALENT_TO]-() REQUIRE r IS UNIQUE",
    ],
    "down": [
        "DROP CONSTRAINT capability_unique IF EXISTS",
        "DROP CONSTRAINT process_unique IF EXISTS",
        "DROP CONSTRAINT produces_unique IF EXISTS",
        "DROP CONSTRAINT consumes_unique IF EXISTS",
        "DROP CONSTRAINT composes_into_unique IF EXISTS",
        "DROP CONSTRAINT realized_by_unique IF EXISTS",
        "DROP CONSTRAINT precedes_unique IF EXISTS",
        "DROP CONSTRAINT equivalent_to_unique IF EXISTS",
    ],
}


class CapabilityEntityMigration(MigrationBase):
    """添加 CapabilityEntity / ProcessEntity 及 V5 业务能力关系。"""

    version_from: str = "1.2.0"
    version_to: str = "2.0.0"
    description: str = "Add CapabilityEntity, ProcessEntity, and business capability relations"

    def upgrade(self, store: GraphStore) -> None:
        for statement in MIGRATION_200["up"]:
            store.query(statement)

    def downgrade(self, store: GraphStore) -> None:
        for statement in MIGRATION_200["down"]:
            store.query(statement)
