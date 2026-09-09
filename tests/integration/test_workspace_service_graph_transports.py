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
from tests.integration.test_workspace_service_graph_publish_orchestrator import _d1_input, _d1_mapping
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


def _web_query(
    client: TestClient,
    operation: str,
    workspace_id: str,
    principal: PrincipalIdentity,
    value: str | None = None,
    **params: object,
) -> object:
    """Invoke an existing Web service-graph operation with its typed value parameter."""
    if operation == "endpoint_methods":
        assert value is not None
        path = f"/api/workspaces/{workspace_id}/service-graph/endpoints/{value}/methods"
    else:
        path = f"/api/workspaces/{workspace_id}/service-graph/{operation}"
        if value is not None:
            params[{"consumers": "endpoint_key", "providers": "endpoint_key"}.get(operation, "node_id")] = value
    return client.get(path, params=params, headers={"X-Workspace-Principal": principal.principal_id})


def _cli_query(
    operation: str,
    workspace_id: str,
    principal: PrincipalIdentity,
    value: str | None = None,
    **params: object,
):
    """Invoke an existing CLI service-graph operation with equivalent arguments."""
    arguments = ["workspace-service-graph", operation, workspace_id, "--principal", principal.principal_id]
    if value is not None:
        option = {"consumers": "endpoint-key", "providers": "endpoint-key"}.get(operation, "node-id")
        arguments.extend((f"--{option}", value))
    for name, item in params.items():
        if item is not None:
            arguments.extend((f"--{name.replace('_', '-')}", str(item)))
    return CliRunner().invoke(main, arguments)


def _mcp_query(
    operation: str,
    workspace_id: str,
    principal: PrincipalIdentity,
    value: str | None = None,
    **params: object,
) -> dict[str, object]:
    """Invoke the public MCP counterpart of an existing service-graph operation."""
    from ontoagent.api import mcp_server

    method = getattr(mcp_server, f"workspace_service_graph_{operation}")
    arguments = (
        (workspace_id, principal.principal_id) if value is None else (workspace_id, principal.principal_id, value)
    )
    return method(*arguments, **params)


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


def test_workspace_service_graph_transports_prove_source_pinned_d1_java_rpc_acl_boundary(
    remote_workspace_graph: _RemoteWorkspaceGraph, client: TestClient
) -> None:
    """Mapped D1 Java RPC facts retain the authorized contract chain across every remote transport."""
    environment = remote_workspace_graph
    mapped_generation = f"generation-transport-d1-mapped-{uuid4()}"
    unmapped_generation = f"generation-transport-d1-unmapped-{uuid4()}"
    full = PrincipalIdentity(f"full-transport-d1-{uuid4()}")
    filtered = PrincipalIdentity(f"filtered-transport-d1-{uuid4()}")
    environment.namespaces.append(
        environment.orchestrator.namespace_for(environment.workspace.workspace_id, mapped_generation)
    )
    assert (
        environment.orchestrator.publish(
            _d1_input(
                environment.workspace,
                mapped_generation,
                None,
                (_d1_mapping("sample-order-provider"), _d1_mapping("sample-checkout-consumer")),
            )
        ).status
        is WorkspacePublishStatus.ACTIVE
    )
    assert (
        environment.acl_repository.upsert_grant(WorkspaceGrant.full(full, environment.workspace.workspace_id)).principal
        == full
    )
    assert (
        environment.acl_repository.upsert_grant(
            WorkspaceGrant.filtered(filtered, environment.workspace.workspace_id, ("sample-checkout-consumer",))
        ).principal
        == filtered
    )
    params = {"generation_id": mapped_generation, "page_size": 100, "node_limit": 1000}

    directory = _web_query(client, "directory", environment.workspace.workspace_id, full, **params)
    assert directory.status_code == 200, directory.text
    directory_nodes = directory.json()["nodes"]
    consumer_call = next(
        node
        for node in directory_nodes
        if node["node_type"] == "ConsumerMethodCall"
        and node["repo_id"] == "sample-checkout-consumer"
        and str(node["target_reference"]).startswith(
            "dubbo-operation:example.orders.api.OrderService#getOrder(java.lang.String):"
            "example.orders.api.OrderSummary|group=orders|version=1.0|alias="
        )
    )
    provider_operation = next(
        node
        for node in directory_nodes
        if node["node_type"] == "ServiceOperation"
        and node["repo_id"] == "sample-order-provider"
        and node["canonical_signature"]
        == "example.orders.api.OrderService#getOrder(java.lang.String):example.orders.api.OrderSummary"
    )

    dependency_responses = (
        _web_query(
            client, "dependencies", environment.workspace.workspace_id, full, consumer_call["id"], depth=2, **params
        ),
        _cli_query("dependencies", environment.workspace.workspace_id, full, consumer_call["id"], depth=2, **params),
        _mcp_query("dependencies", environment.workspace.workspace_id, full, consumer_call["id"], depth=2, **params),
    )
    assert dependency_responses[0].status_code == 200, dependency_responses[0].text
    assert dependency_responses[1].exit_code == 0, dependency_responses[1].output
    dependencies = _normalized(dependency_responses[0].json())
    assert (
        dependencies == _normalized(json.loads(dependency_responses[1].output)) == _normalized(dependency_responses[2])
    )
    assert provider_operation["id"] in {node["id"] for node in dependencies["nodes"]}

    impact_responses = (
        _web_query(
            client, "impact", environment.workspace.workspace_id, full, provider_operation["id"], depth=2, **params
        ),
        _cli_query("impact", environment.workspace.workspace_id, full, provider_operation["id"], depth=2, **params),
        _mcp_query("impact", environment.workspace.workspace_id, full, provider_operation["id"], depth=2, **params),
    )
    assert impact_responses[0].status_code == 200, impact_responses[0].text
    assert impact_responses[1].exit_code == 0, impact_responses[1].output
    impact = _normalized(impact_responses[0].json())
    assert impact == _normalized(json.loads(impact_responses[1].output)) == _normalized(impact_responses[2])
    assert {"ConsumerMethodCall", "OperationBinding", "ImplementationMethod"} <= {
        node["node_type"] for node in impact["nodes"]
    }

    evidence_responses = (
        _web_query(client, "evidence", environment.workspace.workspace_id, full, provider_operation["id"], **params),
        _cli_query("evidence", environment.workspace.workspace_id, full, provider_operation["id"], **params),
        _mcp_query("evidence", environment.workspace.workspace_id, full, provider_operation["id"], **params),
    )
    assert evidence_responses[0].status_code == 200, evidence_responses[0].text
    assert evidence_responses[1].exit_code == 0, evidence_responses[1].output
    evidence = _normalized(evidence_responses[0].json())
    assert evidence == _normalized(json.loads(evidence_responses[1].output)) == _normalized(evidence_responses[2])
    contract_evidence = next(
        node
        for node in evidence["nodes"]
        if node["node_type"] == "MethodEvidence"
        and node["repo_id"] == "sample-order-contract"
        and node["source_revision"] == "6666666666666666666666666666666666666666"
        and node["file_path"] == "src/main/java/example/orders/api/OrderService.java"
    )

    filtered_responses = (
        _web_query(
            client, "dependencies", environment.workspace.workspace_id, filtered, consumer_call["id"], depth=2, **params
        ),
        _cli_query(
            "dependencies", environment.workspace.workspace_id, filtered, consumer_call["id"], depth=2, **params
        ),
        _mcp_query(
            "dependencies", environment.workspace.workspace_id, filtered, consumer_call["id"], depth=2, **params
        ),
    )
    assert filtered_responses[0].status_code == 200, filtered_responses[0].text
    assert filtered_responses[1].exit_code == 0, filtered_responses[1].output
    filtered_dependencies = _normalized(filtered_responses[0].json())
    assert (
        filtered_dependencies
        == _normalized(json.loads(filtered_responses[1].output))
        == _normalized(filtered_responses[2])
    )
    filtered_payload = json.dumps(filtered_dependencies, sort_keys=True)
    assert filtered_dependencies["visibility"] == "filtered"
    assert "sample-order-provider" not in filtered_payload
    assert "sample-order-contract" not in filtered_payload
    assert provider_operation["id"] not in filtered_payload
    assert contract_evidence["id"] not in filtered_payload
    assert all(node["repo_id"] == "sample-checkout-consumer" for node in filtered_dependencies["nodes"])
    assert all(set(edge["repo_ids"]) <= {"sample-checkout-consumer"} for edge in filtered_dependencies["edges"])
    assert "provider_operation_id" not in filtered_payload
    assert "provider_endpoint_reference" not in filtered_payload
    assert "target_reference" not in filtered_payload

    environment.namespaces.append(
        environment.orchestrator.namespace_for(environment.workspace.workspace_id, unmapped_generation)
    )
    assert (
        environment.orchestrator.publish(
            _d1_input(environment.workspace, unmapped_generation, mapped_generation, ())
        ).status
        is WorkspacePublishStatus.ACTIVE
    )
    unmapped_directory = _web_query(
        client,
        "directory",
        environment.workspace.workspace_id,
        full,
        page_size=100,
        node_limit=1000,
    )
    assert unmapped_directory.status_code == 200, unmapped_directory.text
    unmapped_payload = json.dumps(unmapped_directory.json(), sort_keys=True)
    assert provider_operation["id"] not in unmapped_payload
    assert not any(
        node["node_type"] in {"ServiceOperation", "OperationBinding"} and node["repo_id"] == "sample-order-provider"
        for node in unmapped_directory.json()["nodes"]
    )


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
