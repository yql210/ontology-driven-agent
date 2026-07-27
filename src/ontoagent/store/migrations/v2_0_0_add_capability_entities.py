"""v2.0.0 迁移：添加业务能力与流程实体及关系。

后端兼容：
- Neo4j: CREATE CONSTRAINT ... REQUIRE ... IS UNIQUE
- NebulaGraph: CREATE TAG INDEX（节点）/ 无关系约束
"""

from __future__ import annotations

import logging

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import (
    MigrationBase,
    is_nebula,
    nebula_drop_index_stmt,
    nebula_unique_index_stmt,
)

logger = logging.getLogger(__name__)

MIGRATION_200 = {
    "version": "2.0.0",
    "description": "Add CapabilityEntity, ProcessEntity, and business capability relations",
    "depends_on": "1.2.0",
    "up": {
        "neo4j": [
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
        "nebula": [],  # 由 helper 函数动态生成
    },
    "down": {
        "neo4j": [
            "DROP CONSTRAINT capability_unique IF EXISTS",
            "DROP CONSTRAINT process_unique IF EXISTS",
            "DROP CONSTRAINT produces_unique IF EXISTS",
            "DROP CONSTRAINT consumes_unique IF EXISTS",
            "DROP CONSTRAINT composes_into_unique IF EXISTS",
            "DROP CONSTRAINT realized_by_unique IF EXISTS",
            "DROP CONSTRAINT precedes_unique IF EXISTS",
            "DROP CONSTRAINT equivalent_to_unique IF EXISTS",
        ],
        "nebula": [],
    },
}


class CapabilityEntityMigration(MigrationBase):
    """添加 CapabilityEntity / ProcessEntity 及 V5 业务能力关系。"""

    version_from: str = "1.2.0"
    version_to: str = "2.0.0"
    description: str = "Add CapabilityEntity, ProcessEntity, and business capability relations"

    def upgrade(self, store: GraphStore) -> None:
        if is_nebula(store):
            # NebulaGraph: 只建节点唯一索引，关系约束不需要
            statements = [
                nebula_unique_index_stmt("CapabilityEntity", "id"),
            ]
        else:
            statements = MIGRATION_200["up"]["neo4j"]
        for statement in statements:
            try:
                store.query(statement)
            except Exception:
                pass

    def downgrade(self, store: GraphStore) -> None:
        if is_nebula(store):
            statements = [
                nebula_drop_index_stmt("CapabilityEntity", "id"),
            ]
        else:
            statements = MIGRATION_200["down"]["neo4j"]
        for statement in statements:
            try:
                store.query(statement)
            except Exception:
                pass
