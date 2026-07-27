"""v1.1.0 迁移：添加 DataAsset 和 ComplianceItem 约束。

后端兼容：
- Neo4j: CREATE CONSTRAINT ... REQUIRE ... IS UNIQUE
- NebulaGraph: CREATE TAG INDEX（NebulaGraph 无 CONSTRAINT 语法）
"""

from __future__ import annotations

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase, is_nebula, nebula_drop_index_stmt, nebula_unique_index_stmt

MIGRATION_110 = {
    "version": "1.1.0",
    "description": "Add DataAsset and ComplianceItem entities with constraints",
    "depends_on": "1.0.0",
    "up": {
        "neo4j": [
            "CREATE CONSTRAINT data_asset_id_unique IF NOT EXISTS FOR (d:DataAsset) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT compliance_item_id_unique IF NOT EXISTS FOR (c:ComplianceItem) REQUIRE c.id IS UNIQUE",
        ],
        "nebula": [
            # NebulaGraph 无 CONSTRAINT 语法，用 TAG INDEX 替代
        ],
    },
    "down": {
        "neo4j": [
            "DROP CONSTRAINT data_asset_id_unique IF EXISTS",
            "DROP CONSTRAINT compliance_item_id_unique IF EXISTS",
        ],
        "nebula": [],
    },
}


class DataAssetAndComplianceItemMigration(MigrationBase):
    """添加 DataAsset 和 ComplianceItem 实体的唯一性约束。"""

    version_from: str = "1.0.0"
    version_to: str = "1.1.0"
    description: str = "Add DataAsset and ComplianceItem entities with constraints"

    def upgrade(self, store: GraphStore) -> None:
        if is_nebula(store):
            # NebulaGraph: 创建 TAG INDEX（DDL 由 schema initializer 已建好 Tag，这里补唯一索引）
            statements = [
                nebula_unique_index_stmt("DataAsset", "id"),
                nebula_unique_index_stmt("ComplianceItem", "id"),
            ]
        else:
            statements = MIGRATION_110["up"]["neo4j"]
        for statement in statements:
            try:
                store.query(statement)
            except Exception:
                pass  # 索引/约束可能已存在，忽略

    def downgrade(self, store: GraphStore) -> None:
        if is_nebula(store):
            statements = [
                nebula_drop_index_stmt("DataAsset", "id"),
                nebula_drop_index_stmt("ComplianceItem", "id"),
            ]
        else:
            statements = MIGRATION_110["down"]["neo4j"]
        for statement in statements:
            try:
                store.query(statement)
            except Exception:
                pass
