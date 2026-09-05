from __future__ import annotations

import os
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.workspace.models import (
    BuildTask,
    Workspace,
    WorkspaceGeneration,
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
