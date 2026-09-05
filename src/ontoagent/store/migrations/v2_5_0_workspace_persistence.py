"""v2.5.0 migration: provision dedicated Neo4j workspace persistence identities.

The workspace repository owns these labels independently from the per-repository
service graph manifest and the legacy GraphStore ontology labels. This migration
only adds Neo4j constraints, so existing graph records are neither read nor
rewritten.
"""

from __future__ import annotations

import logging

from ontoagent.store.graph_store import GraphStore
from ontoagent.store.migrations import MigrationBase, is_nebula

logger = logging.getLogger(__name__)


class WorkspacePersistenceMigration(MigrationBase):
    """Create idempotent Neo4j identities for dedicated workspace records."""

    version_from: str = "2.4.0"
    version_to: str = "2.5.0"
    description: str = "Add dedicated Neo4j workspace persistence constraints"

    _CONSTRAINTS: tuple[str, ...] = (
        "CREATE CONSTRAINT ontoagent_workspace_identity IF NOT EXISTS "
        "FOR (n:OntoAgentWorkspace) REQUIRE n.workspaceId IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_task_identity IF NOT EXISTS "
        "FOR (n:OntoAgentWorkspaceBuildTask) REQUIRE n.taskId IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_task_idempotency IF NOT EXISTS "
        "FOR (n:OntoAgentWorkspaceBuildTask) REQUIRE (n.workspaceId, n.idempotencyKey) IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_generation_identity IF NOT EXISTS "
        "FOR (n:OntoAgentWorkspaceGeneration) REQUIRE (n.workspaceId, n.generationId) IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_snapshot_identity IF NOT EXISTS "
        "FOR (n:OntoAgentWorkspaceRepositorySnapshot) "
        "REQUIRE (n.workspaceId, n.generationId, n.repoId) IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_active_identity IF NOT EXISTS "
        "FOR (n:OntoAgentWorkspaceActiveBinding) REQUIRE n.workspaceId IS UNIQUE",
    )

    def upgrade(self, store: GraphStore) -> None:
        """Create Neo4j workspace constraints without touching existing graph data."""
        if is_nebula(store):
            logger.info("v2.5.0 workspace persistence migration: skipping Neo4j-only DDL for NebulaGraph")
            return
        for statement in self._CONSTRAINTS:
            store.query(statement)

    def downgrade(self, store: GraphStore) -> None:
        """Leave additive workspace identities in place when rolling back code."""
        logger.info("v2.5.0 workspace persistence migration: retaining additive workspace schema on downgrade")
