"""Remote Neo4j end-to-end coverage for the durable workspace build API."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from neo4j import GraphDatabase

from ontoagent.api.web.app import create_app
from ontoagent.parsing.service_graph.workspace.models import (
    WorkspaceActiveBinding,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import WorkspaceServiceGraphPublishOrchestrator

pytestmark = pytest.mark.integration

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures/service_graph/neutral_three_repo"
REPOSITORIES = ("provider-orders", "consumer-checkout", "isolated-catalog")
TERMINAL_STATES = frozenset({"ACTIVE", "FAILED", "BLOCKED", "SUPERSEDED"})


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("explicit ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


def _git_copy(tmp_path: Path, repo_id: str) -> tuple[Path, str]:
    root = tmp_path / repo_id
    shutil.copytree(FIXTURE_ROOT / repo_id, root)
    for command in (
        ("git", "init", "--initial-branch=main"),
        ("git", "config", "user.email", "tests@example.test"),
        ("git", "config", "user.name", "Tests"),
        ("git", "add", "."),
        ("git", "commit", "-m", "fixture"),
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    revision = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return root, revision


def _wait_for_terminal(
    client: TestClient, workspace_id: str, task_id: str, timeout_seconds: float = 30.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/workspaces/{workspace_id}/tasks/{task_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in TERMINAL_STATES:
            return last
        time.sleep(0.1)
    raise AssertionError(f"workspace build task {task_id} did not reach a terminal state: {last}")


def test_workspace_build_api_publishes_real_git_workspace_and_preserves_active_binding_on_invalid_request(
    tmp_path: Path,
) -> None:
    """The Web API publishes one frozen Git workspace and rejects invalid work without moving its binding."""
    uri, user, password = _credentials()
    workspace_id = f"workspace-api-{uuid4()}"
    generation_id = f"generation-{uuid4()}"
    repositories: list[dict[str, object]] = []
    snapshots: list[WorkspaceRepositorySnapshot] = []
    for repo_id in REPOSITORIES:
        root, revision = _git_copy(tmp_path, repo_id)
        repositories.append(
            {
                "repo_id": repo_id,
                "path": str(root),
                "branch": "main",
                "source_revision": revision,
                "languages": ["java", "yaml"],
            }
        )
        snapshots.append(
            WorkspaceRepositorySnapshot(
                workspace_id,
                repo_id,
                "main",
                revision,
                WorkspaceSourceDescriptor(WorkspaceSourceKind.LOCAL, repo_id),
            )
        )

    body = {
        "manifest": {"workspace_id": workspace_id, "name": "API workspace integration", "repositories": repositories},
        "generation_id": generation_id,
        "idempotency_key": f"request-{uuid4()}",
    }
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace_id, generation_id)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jWorkspaceRepository(driver)
    try:
        with TestClient(create_app()) as client:
            submitted = client.post(f"/api/workspaces/{workspace_id}/build", json=body)
            assert submitted.status_code == 202, submitted.text
            task = submitted.json()
            assert set(task) == {"task_id", "workspace_id", "generation_id", "status"}
            task_id = task["task_id"]
            assert isinstance(task_id, str) and task_id
            assert task["workspace_id"] == workspace_id
            assert task["generation_id"] == generation_id
            assert task["status"] == "PENDING"

            status = _wait_for_terminal(client, workspace_id, task_id)
            assert status["status"] == "ACTIVE", status

            assert repository.get_active_binding(workspace_id) == WorkspaceActiveBinding(workspace_id, generation_id)
            assert repository.get_generation(workspace_id, generation_id) == WorkspaceGeneration(
                workspace_id, generation_id, tuple(snapshots), WorkspaceGenerationState.ACTIVE
            )

            invalid = client.post(
                f"/api/workspaces/{workspace_id}/build",
                json={
                    **body,
                    "generation_id": f"generation-invalid-{uuid4()}",
                    "idempotency_key": f"request-invalid-{uuid4()}",
                    "manifest": {
                        **body["manifest"],
                        "repositories": [
                            {**repositories[0], "source_revision": "not-the-git-revision"},
                            *repositories[1:],
                        ],
                    },
                },
            )
            assert invalid.status_code == 422, invalid.text
            assert repository.get_active_binding(workspace_id) == WorkspaceActiveBinding(workspace_id, generation_id)
    finally:
        with driver.session() as session:
            session.run("MATCH (n { _ontoagent_namespace: $namespace }) DETACH DELETE n", namespace=namespace)
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace_id,
            )
        driver.close()
