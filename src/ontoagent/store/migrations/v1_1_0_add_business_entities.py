"""v1.1.0 迁移：添加 DataAsset 和 ComplianceItem 约束。"""

from __future__ import annotations

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase

MIGRATION_110 = {
    "version": "1.1.0",
    "description": "Add DataAsset and ComplianceItem entities with constraints",
    "depends_on": "1.0.0",
    "up": [
        "CREATE CONSTRAINT data_asset_id_unique IF NOT EXISTS FOR (d:DataAsset) REQUIRE d.id IS UNIQUE",
        "CREATE CONSTRAINT compliance_item_id_unique IF NOT EXISTS FOR (c:ComplianceItem) REQUIRE c.id IS UNIQUE",
    ],
    "down": [
        "DROP CONSTRAINT data_asset_id_unique IF EXISTS",
        "DROP CONSTRAINT compliance_item_id_unique IF EXISTS",
    ],
}


class DataAssetAndComplianceItemMigration(MigrationBase):
    """添加 DataAsset 和 ComplianceItem 实体的唯一性约束。"""

    version_from: str = "1.0.0"
    version_to: str = "1.1.0"
    description: str = "Add DataAsset and ComplianceItem entities with constraints"

    def upgrade(self, store: GraphStore) -> None:
        for statement in MIGRATION_110["up"]:
            store.query(statement)

    def downgrade(self, store: GraphStore) -> None:
        for statement in MIGRATION_110["down"]:
            store.query(statement)
