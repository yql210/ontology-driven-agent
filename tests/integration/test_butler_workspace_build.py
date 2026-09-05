"""Remote Neo4j coverage for the Butler workspace-build vertical slice."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.butler.engine import ButlerEngine
from ontoagent.butler.event_bus import ButlerEvent
from ontoagent.butler.handlers.workspace_build import create_workspace_build_handler
from ontoagent.config import OntoAgentConfig
from ontoagent.parsing.service_graph.workspace.models import WorkspaceActiveBinding
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import WorkspaceServiceGraphPublishOrchestrator

pytestmark = pytest.mark.integration

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures/service_graph/neutral_three_repo"
REPOSITORIES = ("provider-orders", "consumer-checkout", "isolated-catalog")


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


async def _submit(engine: ButlerEngine, event: ButlerEvent) -> list[object]:
    await engine.start()
    try:
        return await engine.submit_event(event)
    finally:
        await engine.stop()


def test_butler_workspace_event_publishes_three_local_git_repositories_to_remote_neo4j(tmp_path: Path) -> None:
    """A Butler event uses the workspace service and preserves the active binding on invalid preflight."""
    uri, user, password = _credentials()
    workspace_id = f"workspace-butler-{uuid4()}"
    generation_id = f"generation-butler-{uuid4()}"
    repositories: list[dict[str, object]] = []
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
    config = OntoAgentConfig(neo4j_uri=uri, neo4j_user=user, neo4j_password=password)
    config.data_dir = str(tmp_path / ".ontoagent")
    handler = create_workspace_build_handler(config)
    engine = ButlerEngine(config)
    engine.register_handler(handler)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jWorkspaceRepository(driver)
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace_id, generation_id)
    payload = {
        "workspace_id": workspace_id,
        "generation_id": generation_id,
        "idempotency_key": f"request-butler-{uuid4()}",
        "manifest_dir": str(tmp_path),
        "manifest": {"workspace_id": workspace_id, "name": "Butler workspace", "repositories": repositories},
    }
    try:
        results = asyncio.run(_submit(engine, ButlerEvent(event_type="workspace.build.requested", payload=payload)))
        assert len(results) == 1
        assert results[0].success is True
        task_id = results[0].result_data["task_id"]
        assert isinstance(task_id, str) and task_id
        assert repository.get_active_binding(workspace_id) == WorkspaceActiveBinding(workspace_id, generation_id)

        invalid_payload = {
            **payload,
            "generation_id": f"generation-invalid-{uuid4()}",
            "idempotency_key": f"bad-{uuid4()}",
        }
        invalid_payload["manifest"] = {
            **payload["manifest"],
            "repositories": [{**repositories[0], "source_revision": "not-a-revision"}, *repositories[1:]],
        }
        invalid_handler = create_workspace_build_handler(config)
        invalid_engine = ButlerEngine(config)
        invalid_engine.register_handler(invalid_handler)
        invalid_results = asyncio.run(
            _submit(invalid_engine, ButlerEvent(event_type="workspace.build.requested", payload=invalid_payload))
        )
        assert invalid_results[0].success is False
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
