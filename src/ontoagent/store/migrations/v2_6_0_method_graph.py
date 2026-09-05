"""v2.6.0 migration: isolate method-fact graph identities by namespace."""

from __future__ import annotations

import logging

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase, is_nebula

logger = logging.getLogger(__name__)


class MethodGraphMigration(MigrationBase):
    """Additive Neo4j identities for the dedicated method graph writer only."""

    version_from = "2.5.0"
    version_to = "2.6.0"
    description = "Add method graph namespace identity constraints"

    _LABELS = (
        "ServiceOperation",
        "ImplementationMethod",
        "ConsumerMethodCall",
        "OperationBinding",
        "MethodEvidence",
        "MethodUnresolved",
        "MethodCallTarget",
    )

    def upgrade(self, store: GraphStore) -> None:
        if is_nebula(store):
            logger.info("v2.6.0 method graph migration: skipping Neo4j-only DDL for NebulaGraph")
            return
        for label in self._LABELS:
            store.query(
                f"CREATE CONSTRAINT ontoagent_method_{label.lower()}_identity IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE (n.namespace, n.id) IS UNIQUE"
            )

    def downgrade(self, store: GraphStore) -> None:
        logger.info("v2.6.0 method graph migration: retaining additive method graph identities on downgrade")
