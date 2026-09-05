from __future__ import annotations

import os
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.workspace.models import (
    BuildTask,
    Workspace,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspacePublishStatus,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository

pytestmark = pytest.mark.integration


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not uri or not user or not password:
        pytest.skip("ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


def test_workspace_generation_and_task_round_trip_against_neo4j() -> None:
    uri, user, password = _credentials()
    workspace_id = f"integration-workspace-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jWorkspaceRepository(driver)
    workspace = Workspace(workspace_id, "Integration workspace")
    task = BuildTask(f"task-{uuid4()}", workspace_id, f"request-{uuid4()}")
    snapshots = tuple(
        WorkspaceRepositorySnapshot(
            workspace_id,
            repo_id,
            "main",
            revision,
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id, revision in (("provider-orders", "p1"), ("consumer-checkout", "c1"), ("isolated-catalog", "i1"))
    )
    generation = WorkspaceGeneration(workspace_id, f"generation-{uuid4()}", snapshots)
    try:
        assert repository.create_workspace(workspace) == workspace
        assert repository.get_workspace(workspace_id) == workspace
        assert repository.create_build_task(task) == task
        assert repository.create_generation(generation) == generation
        assert repository.get_generation(workspace_id, generation.generation_id) == generation
        assert repository.get_active_binding(workspace_id) is None
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace_id,
            )
        driver.close()


def test_workspace_generation_publication_cas_against_neo4j() -> None:
    uri, user, password = _credentials()
    workspace_id = f"integration-workspace-publish-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jWorkspaceRepository(driver)
    workspace = Workspace(workspace_id, "Workspace publication integration")
    generations = tuple(
        WorkspaceGeneration(workspace_id, f"generation-{uuid4()}", _snapshots(workspace_id, revision))
        for revision in ("first", "second", "stale")
    )
    other_workspace = Workspace(f"integration-workspace-other-{uuid4()}", "Other workspace")
    other_generation = WorkspaceGeneration(
        other_workspace.workspace_id, f"generation-{uuid4()}", _snapshots(other_workspace.workspace_id, "other")
    )
    try:
        assert repository.create_workspace(workspace) == workspace
        assert repository.create_workspace(other_workspace) == other_workspace
        for generation in (*generations, other_generation):
            assert repository.create_generation(generation) == generation

        initial_not_ready = repository.publish_generation(workspace_id, None, generations[0].generation_id)
        assert initial_not_ready.status is WorkspacePublishStatus.CANDIDATE_NOT_READY
        assert repository.get_active_binding(workspace_id) is None

        first = _advance_to_verifying(repository, generations[0])
        first_publication = repository.publish_generation(workspace_id, None, first.generation_id)
        assert first_publication.status is WorkspacePublishStatus.PUBLISHED

        second = _advance_to_verifying(repository, generations[1])
        second_publication = repository.publish_generation(workspace_id, first.generation_id, second.generation_id)
        assert second_publication.status is WorkspacePublishStatus.PUBLISHED
        assert repository.get_active_binding(workspace_id) is not None
        assert repository.get_active_binding(workspace_id).generation_id == second.generation_id  # type: ignore[union-attr]
        assert repository.get_generation(workspace_id, first.generation_id).state is WorkspaceGenerationState.SUPERSEDED  # type: ignore[union-attr]
        assert repository.get_generation(workspace_id, second.generation_id).state is WorkspaceGenerationState.ACTIVE  # type: ignore[union-attr]

        stale = _advance_to_verifying(repository, generations[2])
        stale_publication = repository.publish_generation(workspace_id, first.generation_id, stale.generation_id)
        assert stale_publication.status is WorkspacePublishStatus.STALE_ACTIVE
        assert repository.get_active_binding(workspace_id).generation_id == second.generation_id  # type: ignore[union-attr]
        assert repository.get_generation(workspace_id, stale.generation_id).state is WorkspaceGenerationState.BLOCKED  # type: ignore[union-attr]

        other = _advance_to_verifying(repository, other_generation)
        assert (
            repository.publish_generation(other_workspace.workspace_id, None, other.generation_id).status
            is WorkspacePublishStatus.PUBLISHED
        )
        assert repository.get_active_binding(other_workspace.workspace_id).generation_id == other.generation_id  # type: ignore[union-attr]
    finally:
        for target_workspace_id in (workspace_id, other_workspace.workspace_id):
            with driver.session() as session:
                session.run(
                    "MATCH (n) WHERE n.workspaceId = $workspace_id "
                    "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                    "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                    "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                    workspace_id=target_workspace_id,
                )
        driver.close()


def _snapshots(workspace_id: str, revision_prefix: str) -> tuple[WorkspaceRepositorySnapshot, ...]:
    return tuple(
        WorkspaceRepositorySnapshot(
            workspace_id,
            repo_id,
            "main",
            f"{revision_prefix}-{revision}",
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id, revision in (("provider-orders", "p"), ("consumer-checkout", "c"), ("isolated-catalog", "i"))
    )


def _advance_to_verifying(repository: Neo4jWorkspaceRepository, generation: WorkspaceGeneration) -> WorkspaceGeneration:
    current = generation
    for state in (
        WorkspaceGenerationState.EXTRACTING,
        WorkspaceGenerationState.RESOLVING,
        WorkspaceGenerationState.WRITING,
        WorkspaceGenerationState.VERIFYING,
    ):
        current = repository.advance_generation_state(current, state)
    return current
