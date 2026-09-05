"""Remote Neo4j coverage for workspace-scoped service graph publication."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.workspace.models import (
    Workspace,
    WorkspaceGenerationState,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import (
    Neo4jWorkspaceServiceGraphPublishComponentFactory,
    WorkspacePublishStatus,
    WorkspaceServiceGraphPublishInput,
    WorkspaceServiceGraphPublishOrchestrator,
)

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "fixtures/service_graph/neutral_three_repo"
REVISIONS = {
    "provider-orders": "fixture-provider-v1",
    "consumer-checkout": "fixture-consumer-v1",
    "isolated-catalog": "fixture-isolated-v1",
}


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("explicit ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


def _input(workspace: Workspace, generation_id: str, expected_active: str | None) -> WorkspaceServiceGraphPublishInput:
    frozen = tuple(
        WorkspaceRepositorySnapshot(
            workspace.workspace_id,
            repo_id,
            "main",
            revision,
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id, revision in REVISIONS.items()
    )
    runtime = tuple(
        RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
        for repo_id, revision in REVISIONS.items()
    )
    return WorkspaceServiceGraphPublishInput(
        workspace, frozen, runtime, f"request-{generation_id}", generation_id, expected_active
    )


def test_workspace_orchestrator_publishes_replaces_and_blocks_stale_generation_in_remote_neo4j() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-service-graph-{uuid4()}", "Workspace graph integration")
    generation_one = f"generation-one-{uuid4()}"
    generation_two = f"generation-two-{uuid4()}"
    generation_three = f"generation-three-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    namespaces = tuple(
        WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation)
        for generation in (generation_one, generation_two, generation_three)
    )
    try:
        first = orchestrator.publish(_input(workspace, generation_one, None))
        assert first.status is WorkspacePublishStatus.ACTIVE
        assert first.candidate_namespace == namespaces[0]

        repository = Neo4jWorkspaceRepository(driver)
        assert repository.get_active_binding(workspace.workspace_id).generation_id == generation_one  # type: ignore[union-attr]
        with driver.session() as session:
            count = session.run(
                "MATCH (n { _ontoagent_namespace: $namespace }) RETURN count(n) AS count", namespace=namespaces[0]
            ).single()["count"]
        assert count > 0

        second = orchestrator.publish(_input(workspace, generation_two, generation_one))
        assert second.status is WorkspacePublishStatus.ACTIVE
        assert repository.get_active_binding(workspace.workspace_id).generation_id == generation_two  # type: ignore[union-attr]
        assert (
            repository.get_generation(workspace.workspace_id, generation_one).state
            is WorkspaceGenerationState.SUPERSEDED
        )  # type: ignore[union-attr]

        stale = orchestrator.publish(_input(workspace, generation_three, generation_one))
        assert stale.status is WorkspacePublishStatus.BLOCKED
        assert repository.get_active_binding(workspace.workspace_id).generation_id == generation_two  # type: ignore[union-attr]
        assert (
            repository.get_generation(workspace.workspace_id, generation_three).state
            is WorkspaceGenerationState.BLOCKED
        )  # type: ignore[union-attr]
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (n) WHERE n._ontoagent_namespace IN $namespaces DETACH DELETE n", namespaces=list(namespaces)
            )
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace.workspace_id,
            )
        driver.close()
