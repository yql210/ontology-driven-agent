from __future__ import annotations

import os
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.workspace.models import (
    Workspace,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository
from ontoagent.store.migrations.v2_5_0_workspace_persistence import WorkspacePersistenceMigration

pytestmark = pytest.mark.integration


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not uri or not user or not password:
        pytest.skip("ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


class _Neo4jMigrationStore:
    def __init__(self, driver: object) -> None:
        self._driver = driver

    def query(self, statement: str, params: dict | None = None) -> list[dict]:
        with self._driver.session() as session:  # type: ignore[union-attr]
            return [record.data() for record in session.run(statement, params or {})]  # type: ignore[union-attr]


def test_workspace_persistence_migration_is_additive_idempotent_and_preserves_legacy_data() -> None:
    uri, user, password = _credentials()
    token = str(uuid4())
    workspace_id = f"migration-workspace-{token}"
    new_workspace_id = f"migration-workspace-new-{token}"
    generation_id = f"migration-generation-{token}"
    repo_id = f"migration-repository-{token}"
    namespace = f"migration-legacy-{token}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    migration = WorkspacePersistenceMigration()
    store = _Neo4jMigrationStore(driver)
    try:
        with driver.session() as session:
            session.run(
                "CREATE (workspace:OntoAgentWorkspace {workspaceId: $workspace_id, name: 'Existing workspace'}) "
                "CREATE (generation:OntoAgentWorkspaceGeneration {workspaceId: $workspace_id, "
                "generationId: $generation_id, state: 'pending', snapshotFingerprint: 'seed'}) "
                "CREATE (snapshot:OntoAgentWorkspaceRepositorySnapshot {workspaceId: $workspace_id, "
                "generationId: $generation_id, repoId: $repo_id, branch: 'main', sourceRevision: 'seed-revision', "
                "sourceKind: 'git', sourceDescriptor: 'https://example.test/seed.git'}) "
                "CREATE (generation)-[:HAS_FROZEN_SNAPSHOT]->(snapshot) "
                "CREATE (:OntoAgentServiceGraphManifest {namespace: $namespace, repoId: $repo_id})",
                workspace_id=workspace_id,
                generation_id=generation_id,
                repo_id=repo_id,
                namespace=namespace,
            )

        with driver.session() as session:
            legacy_count_before = session.run(
                "MATCH (manifest:OntoAgentServiceGraphManifest {namespace: $namespace}) RETURN count(manifest) AS count",
                namespace=namespace,
            ).single()["count"]

        migration.upgrade(store)  # type: ignore[arg-type]
        migration.upgrade(store)  # Neo4j 5 IF NOT EXISTS rerun
        repository = Neo4jWorkspaceRepository(driver)
        existing = Workspace(workspace_id, "Existing workspace")
        existing_generation = WorkspaceGeneration(
            workspace_id,
            generation_id,
            (
                WorkspaceRepositorySnapshot(
                    workspace_id,
                    repo_id,
                    "main",
                    "seed-revision",
                    WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, "https://example.test/seed.git"),
                ),
            ),
            WorkspaceGenerationState.PENDING,
        )
        created = Workspace(new_workspace_id, "Created after migration")

        assert repository.get_workspace(workspace_id) == existing
        assert repository.get_generation(workspace_id, generation_id) == existing_generation
        assert repository.create_workspace(created) == created
        assert repository.get_workspace(new_workspace_id) == created

        with driver.session() as session:
            legacy_count_after = session.run(
                "MATCH (manifest:OntoAgentServiceGraphManifest {namespace: $namespace}) RETURN count(manifest) AS count",
                namespace=namespace,
            ).single()["count"]
        assert legacy_count_after == legacy_count_before == 1
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.workspaceId IN $workspace_ids "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_ids=[workspace_id, new_workspace_id],
            )
            session.run(
                "MATCH (manifest:OntoAgentServiceGraphManifest {namespace: $namespace}) DETACH DELETE manifest",
                namespace=namespace,
            )
        driver.close()
