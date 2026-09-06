"""Remote Neo4j transport parity proof for workspace service graph reads."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from ontoagent.api.cli import main
from ontoagent.api.web.app import create_app
from ontoagent.domain.workspace_authorization import PrincipalIdentity, WorkspaceGrant
from ontoagent.parsing.service_graph.workspace.models import WorkspaceGeneration
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import WorkspacePublishStatus
from tests.integration.test_workspace_service_graph_query import (
    _input,
    _method_facts,
    _RemoteWorkspaceGraph,
)

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.test_workspace_service_graph_query",)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Create the Web transport without sharing its unrelated local ACL database."""
    monkeypatch.setenv("ONTOAGENT_ACL_DB", str(tmp_path / "transport-acl.db"))
    with TestClient(create_app()) as test_client:
        yield test_client


def _normalized(envelope: dict[str, object]) -> dict[str, object]:
    """Normalize JSON-compatible graph records before cross-transport comparison."""
    result = json.loads(json.dumps(envelope, sort_keys=True))
    result["nodes"] = sorted(result["nodes"], key=lambda node: node["id"])
    result["edges"] = sorted(result["edges"], key=lambda edge: edge["id"])
    return result


def _web_directory(client: TestClient, workspace_id: str, principal: PrincipalIdentity, **params: object) -> object:
    return client.get(
        f"/api/workspaces/{workspace_id}/service-graph/directory",
        params=params,
        headers={"X-Workspace-Principal": principal.principal_id},
    )


def _cli_directory(workspace_id: str, principal: PrincipalIdentity, **params: object):
    arguments = ["workspace-service-graph", "directory", workspace_id, "--principal", principal.principal_id]
    for name, value in params.items():
        if value is not None:
            arguments.extend((f"--{name.replace('_', '-')}", str(value)))
    return CliRunner().invoke(main, arguments)


def _mcp_directory(workspace_id: str, principal: PrincipalIdentity, **params: object) -> dict[str, object]:
    from ontoagent.api import mcp_server

    return mcp_server.workspace_service_graph_directory(workspace_id, principal.principal_id, **params)


def _assert_filtered_safe(envelope: dict[str, object], generation_id: str) -> None:
    payload = json.dumps(envelope, sort_keys=True)
    provider = _method_facts(generation_id)[0]
    hidden_values = (
        "provider-orders",
        provider.operations[0].id,
        provider.evidences[0].id,
        "factPayload",
        '"total"',
        '"count"',
    )

    assert envelope["visibility"] == "filtered"
    assert all(node.get("repo_id", node.get("repoId")) == "consumer-checkout" for node in envelope["nodes"])
    assert all(set(edge["repo_ids"]) <= {"consumer-checkout"} for edge in envelope["edges"])
    assert all(value not in payload for value in hidden_values)


def test_workspace_service_graph_transports_match_real_full_and_filtered_generation(
    remote_workspace_graph: _RemoteWorkspaceGraph, client: TestClient
) -> None:
    """Web, CLI, and MCP expose exactly the same real authorized graph envelope."""
    environment = remote_workspace_graph
    generation_id = f"generation-transport-{uuid4()}"
    full = PrincipalIdentity(f"full-transport-{uuid4()}")
    filtered = PrincipalIdentity(f"filtered-transport-{uuid4()}")
    assert environment.publish(generation_id, None).status is WorkspacePublishStatus.ACTIVE
    assert (
        environment.acl_repository.upsert_grant(WorkspaceGrant.full(full, environment.workspace.workspace_id)).principal
        == full
    )
    assert (
        environment.acl_repository.upsert_grant(
            WorkspaceGrant.filtered(filtered, environment.workspace.workspace_id, ("consumer-checkout",))
        ).principal
        == filtered
    )
    params = {"generation_id": generation_id, "page_size": 100, "node_limit": 1000}

    full_web = _web_directory(client, environment.workspace.workspace_id, full, **params)
    full_cli = _cli_directory(environment.workspace.workspace_id, full, **params)
    full_mcp = _mcp_directory(environment.workspace.workspace_id, full, **params)

    assert full_web.status_code == 200, full_web.text
    assert full_cli.exit_code == 0, full_cli.output
    assert _normalized(full_web.json()) == _normalized(json.loads(full_cli.output)) == _normalized(full_mcp)
    assert full_web.json()["visibility"] == "full"

    filtered_web = _web_directory(client, environment.workspace.workspace_id, filtered, **params)
    filtered_cli = _cli_directory(environment.workspace.workspace_id, filtered, **params)
    filtered_mcp = _mcp_directory(environment.workspace.workspace_id, filtered, **params)

    assert filtered_web.status_code == 200, filtered_web.text
    assert filtered_cli.exit_code == 0, filtered_cli.output
    filtered = _normalized(filtered_web.json())
    assert filtered == _normalized(json.loads(filtered_cli.output)) == _normalized(filtered_mcp)
    _assert_filtered_safe(filtered, generation_id)


@pytest.mark.parametrize(
    ("kind", "expected_error", "expected_exit"),
    (
        ("forbidden", "forbidden", 3),
        ("inactive", "conflict", 5),
        ("tampered", "conflict", 5),
        ("invalid", "invalid_request", 2),
    ),
)
def test_workspace_service_graph_transports_return_safe_equivalent_errors(
    remote_workspace_graph: _RemoteWorkspaceGraph,
    client: TestClient,
    kind: str,
    expected_error: str,
    expected_exit: int,
) -> None:
    """Each transport maps authorization and bound failures without leaking graph details."""
    environment = remote_workspace_graph
    generation_id = f"generation-transport-error-{uuid4()}"
    granted = PrincipalIdentity(f"granted-transport-{uuid4()}")
    principal = granted
    assert environment.publish(generation_id, None).status is WorkspacePublishStatus.ACTIVE
    assert (
        environment.acl_repository.upsert_grant(
            WorkspaceGrant.full(granted, environment.workspace.workspace_id)
        ).principal
        == granted
    )
    params: dict[str, object] = {"generation_id": generation_id, "page_size": 100, "node_limit": 1000}

    if kind == "forbidden":
        principal = PrincipalIdentity(f"ungranted-transport-{uuid4()}")
    elif kind == "inactive":
        inactive_id = f"generation-transport-pending-{uuid4()}"
        inactive = WorkspaceGeneration(
            environment.workspace.workspace_id,
            inactive_id,
            _input(environment.workspace, inactive_id, generation_id).snapshots,
        )
        assert environment.workspace_repository.create_generation(inactive) == inactive
        params["generation_id"] = inactive_id
    elif kind == "tampered":
        with environment.driver.session() as session:  # type: ignore[union-attr]
            session.run(
                "MATCH (receipt:OntoAgentWorkspaceServiceGraphReceipt "
                "{workspaceId: $workspace_id, generationId: $generation_id}) SET receipt.confirmed = false",
                workspace_id=environment.workspace.workspace_id,
                generation_id=generation_id,
            ).consume()
    else:
        params["page_size"] = 0

    web = _web_directory(client, environment.workspace.workspace_id, principal, **params)
    cli = _cli_directory(environment.workspace.workspace_id, principal, **params)
    mcp = _mcp_directory(environment.workspace.workspace_id, principal, **params)

    assert web.status_code == ({"forbidden": 403, "invalid_request": 422}.get(expected_error, 409)), web.text
    assert cli.exit_code == expected_exit
    assert json.loads(cli.stderr) == {"error": expected_error}
    assert mcp == {"error": expected_error}


def test_mcp_generic_graph_query_rejects_workspace_scoped_cypher() -> None:
    """Workspace graph Cypher cannot bypass the dedicated ACL-gated MCP tools."""
    from ontoagent.api import mcp_server

    with pytest.raises(ValueError, match="dedicated tools"):
        mcp_server.graph_query("MATCH (n:OntoAgentWorkspace {workspaceId: 'workspace'}) RETURN n")
