"""v2.4.0 migration: add CapabilityEntity entry identity to existing Nebula schemas.

New schemas receive ``entryCodeEntityId`` through dataclass reflection. Existing
NebulaGraph tags require an explicit online schema change. Neo4j properties are
dynamic, so it needs no DDL or data backfill.
"""

from __future__ import annotations

import contextlib
import logging

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase, is_nebula

logger = logging.getLogger(__name__)


class CapabilityEntryIdentityMigration(MigrationBase):
    """Add the entry code entity identity column to existing CapabilityEntity tags."""

    version_from: str = "2.3.0"
    version_to: str = "2.4.0"
    description: str = "Add CapabilityEntity.entryCodeEntityId property for repo-scoped capability identity"

    def upgrade(self, store: GraphStore) -> None:
        """Add the Nebula column; Neo4j needs no DDL and no data is backfilled."""
        if not is_nebula(store):
            logger.info("v2.4.0 capability entry identity migration: no Neo4j DDL required")
            return
        with contextlib.suppress(Exception):
            store.query("ALTER TAG `CapabilityEntity` ADD (`entryCodeEntityId` string);")

    def downgrade(self, store: GraphStore) -> None:
        """Drop only the Nebula column; this cannot restore prior capability IDs or data."""
        if not is_nebula(store):
            logger.info("v2.4.0 capability entry identity migration: no Neo4j DDL required on downgrade")
            return
        with contextlib.suppress(Exception):
            store.query("ALTER TAG `CapabilityEntity` DROP (`entryCodeEntityId`);")
