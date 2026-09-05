"""Remote Neo4j coverage for the local workspace-build CLI vertical slice."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from click.testing import CliRunner
from neo4j import GraphDatabase

from ontoagent.api.cli import main
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import WorkspacePublishStatus

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


def test_workspace_build_cli_publishes_local_three_repository_manifest_to_remote_neo4j(tmp_path: Path) -> None:
    uri, user, password = _credentials()
    workspace_id = f"workspace-cli-{uuid4()}"
    repositories = []
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
    manifest = tmp_path / "workspace.json"
    manifest.write_text(
        json.dumps({"workspace_id": workspace_id, "name": "CLI workspace integration", "repositories": repositories}),
        encoding="utf-8",
    )
    driver = GraphDatabase.driver(uri, auth=(user, password))
    namespace = None
    try:
        result = CliRunner().invoke(main, ["workspace-build", str(manifest)])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["task_id"]
        assert payload["generation_id"]
        assert payload["outcome"]["status"] == WorkspacePublishStatus.ACTIVE.value
        namespace = payload["outcome"]["candidate_namespace"]
        binding = Neo4jWorkspaceRepository(driver).get_active_binding(workspace_id)
        assert binding is not None
        assert binding.generation_id == payload["generation_id"]
    finally:
        with driver.session() as session:
            if namespace is not None:
                session.run("MATCH (n { _ontoagent_namespace: $namespace }) DETACH DELETE n", namespace=namespace)
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace_id,
            )
        driver.close()
