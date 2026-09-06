"""Remote Neo4j coverage for durable workspace ACL authorization."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.domain.workspace_authorization import PrincipalIdentity, WorkspaceAuthorizationFailure, WorkspaceGrant
from ontoagent.execution.workspace_query_authorization import (
    WorkspaceAuthorizationError,
    WorkspaceQueryAuthorizationService,
)
from ontoagent.parsing.service_graph.workspace.models import (
    Workspace,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository
from ontoagent.store.migrations.v2_7_0_workspace_acl import WorkspaceAclMigration
from ontoagent.store.neo4j_workspace_acl_repository import Neo4jWorkspaceAclRepository

pytestmark = pytest.mark.integration


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("explicit ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


class _MigrationStore:
    def __init__(self, driver: object) -> None:
        self._driver = driver

    def query(self, statement: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
        with self._driver.session() as session:  # type: ignore[union-attr]
            return [record.data() for record in session.run(statement, params or {})]  # type: ignore[union-attr]


def test_workspace_acl_grant_write_read_and_authorization_against_remote_neo4j() -> None:
    uri, user, password = _credentials()
    workspace_id = f"workspace-acl-{uuid4()}"
    generation_id = f"generation-acl-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    workspace_repository = Neo4jWorkspaceRepository(driver)
    acl_repository = Neo4jWorkspaceAclRepository(driver)
    generation = WorkspaceGeneration(
        workspace_id,
        generation_id,
        tuple(
            WorkspaceRepositorySnapshot(
                workspace_id,
                repo_id,
                "main",
                f"revision-{repo_id}",
                WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
            )
            for repo_id in ("repo-a", "repo-b")
        ),
    )
    alice = PrincipalIdentity(f"alice-{uuid4()}")
    bob = PrincipalIdentity(f"bob-{uuid4()}")
    try:
        WorkspaceAclMigration().upgrade(_MigrationStore(driver))  # type: ignore[arg-type]
        assert (
            workspace_repository.create_workspace(Workspace(workspace_id, "ACL integration workspace")).workspace_id
            == workspace_id
        )
        assert workspace_repository.create_generation(generation) == generation
        current = generation
        for state in (
            WorkspaceGenerationState.EXTRACTING,
            WorkspaceGenerationState.RESOLVING,
            WorkspaceGenerationState.WRITING,
            WorkspaceGenerationState.VERIFYING,
        ):
            current = workspace_repository.advance_generation_state(current, state)
        assert (
            workspace_repository.publish_generation(workspace_id, None, generation_id).active_generation_id
            == generation_id
        )

        full_grant = WorkspaceGrant.full(alice, workspace_id)
        filtered_grant = WorkspaceGrant.filtered(bob, workspace_id, ("repo-b",))
        assert acl_repository.upsert_grant(full_grant) == full_grant
        assert acl_repository.upsert_grant(filtered_grant) == filtered_grant
        assert acl_repository.get_grant(alice, workspace_id) == full_grant
        assert acl_repository.get_grant(bob, workspace_id) == filtered_grant

        service = WorkspaceQueryAuthorizationService(acl_repository)
        assert service.authorize(alice, workspace_id).repositories.repo_ids == frozenset({"repo-a", "repo-b"})
        assert service.authorize(bob, workspace_id, generation_id).repositories.repo_ids == frozenset({"repo-b"})
        with pytest.raises(WorkspaceAuthorizationError) as raised:
            service.authorize(PrincipalIdentity(f"ungranted-{uuid4()}"), workspace_id)
        assert raised.value.failure is WorkspaceAuthorizationFailure.FORBIDDEN
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (grant:OntoAgentWorkspaceAclGrant {workspaceId: $workspace_id}) DETACH DELETE grant",
                workspace_id=workspace_id,
            )
            session.run(
                "MATCH (node) WHERE node.workspaceId = $workspace_id "
                "AND (node:OntoAgentWorkspace OR node:OntoAgentWorkspaceBuildTask "
                "OR node:OntoAgentWorkspaceGeneration OR node:OntoAgentWorkspaceRepositorySnapshot "
                "OR node:OntoAgentWorkspaceActiveBinding) DETACH DELETE node",
                workspace_id=workspace_id,
            )
        driver.close()
