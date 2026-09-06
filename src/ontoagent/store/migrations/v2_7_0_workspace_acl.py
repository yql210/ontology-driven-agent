"""v2.7.0 migration: provision dedicated workspace ACL grant identity."""

from __future__ import annotations

import logging

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase, is_nebula

logger = logging.getLogger(__name__)


class WorkspaceAclMigration(MigrationBase):
    """Create the idempotent identity used by durable workspace ACL grants."""

    version_from: str = "2.6.0"
    version_to: str = "2.7.0"
    description: str = "Add dedicated Neo4j workspace ACL grant constraint"

    _CONSTRAINT = (
        "CREATE CONSTRAINT ontoagent_workspace_acl_grant_identity IF NOT EXISTS "
        "FOR (n:OntoAgentWorkspaceAclGrant) REQUIRE (n.principalId, n.workspaceId) IS UNIQUE"
    )

    def upgrade(self, store: GraphStore) -> None:
        """Create the Neo4j-only ACL identity without reading graph query metadata."""
        if is_nebula(store):
            logger.info("v2.7.0 workspace ACL migration: skipping Neo4j-only DDL for NebulaGraph")
            return
        store.query(self._CONSTRAINT)

    def downgrade(self, store: GraphStore) -> None:
        """Retain additive ACL identity when rolling back code."""
        logger.info("v2.7.0 workspace ACL migration: retaining additive workspace ACL schema on downgrade")
