"""Remote Neo4j proof for ACL-gated workspace service graph reads."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.domain.workspace_authorization import PrincipalIdentity, WorkspaceAuthorizationFailure, WorkspaceGrant
from ontoagent.domain.workspace_graph_query import WorkspaceGraphQueryRequest, WorkspaceGraphQueryValidationError
from ontoagent.execution.workspace_query_authorization import (
    WorkspaceAuthorizationError,
    WorkspaceQueryAuthorizationService,
)
from ontoagent.execution.workspace_service_graph_query import WorkspaceServiceGraphQueryService
from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.methods import (
    ConsumerMethodCall,
    ImplementationMethod,
    MethodEvidence,
    MethodFacts,
    OperationBinding,
    ServiceOperation,
)
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.workspace.models import (
    Workspace,
    WorkspaceGeneration,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.neo4j_query_repository import Neo4jWorkspaceServiceGraphQueryRepository
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import (
    Neo4jWorkspaceServiceGraphPublishComponentFactory,
    WorkspacePublishStatus,
    WorkspaceServiceGraphPublishInput,
    WorkspaceServiceGraphPublishOrchestrator,
)
from ontoagent.store.neo4j_workspace_acl_repository import Neo4jWorkspaceAclRepository

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "fixtures/service_graph/neutral_three_repo"
REVISIONS = {
    "provider-orders": "query-provider-v1",
    "consumer-checkout": "query-consumer-v1",
    "isolated-catalog": "query-isolated-v1",
}


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("explicit ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


def _input(
    workspace: Workspace,
    generation_id: str,
    expected_active: str | None,
    runtime_root: Path = FIXTURE,
) -> WorkspaceServiceGraphPublishInput:
    snapshots = tuple(
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
        RepositorySnapshot(repo_id, revision, runtime_root / repo_id, frozenset({"java", "yaml"}))
        for repo_id, revision in REVISIONS.items()
    )
    return WorkspaceServiceGraphPublishInput(
        workspace,
        snapshots,
        runtime,
        f"query-request-{generation_id}",
        generation_id,
        expected_active,
        (),
        _method_facts(generation_id),
    )


def _method_facts(generation_id: str) -> tuple[MethodFacts, ...]:
    provider_repo, consumer_repo = "provider-orders", "consumer-checkout"
    provider_evidence = _evidence(provider_repo, generation_id, "OrderApi.find")
    provider_operation = ServiceOperation(
        provider_repo,
        "orders",
        "orders",
        REVISIONS[provider_repo],
        generation_id,
        "provider",
        "example.orders.OrderApi",
        "find",
        "example.orders.OrderApi#find(java.lang.String):Order",
        (provider_evidence.id,),
    )
    provider_implementation = _implementation(provider_repo, generation_id, provider_evidence.id, "OrderService")
    provider_fact = MethodFacts(
        "remote-query-proof",
        "1",
        provider_repo,
        REVISIONS[provider_repo],
        generation_id,
        (provider_operation,),
        (provider_implementation,),
        (),
        (
            OperationBinding(
                provider_repo,
                "orders",
                "orders",
                REVISIONS[provider_repo],
                generation_id,
                "spring-http:GET:/orders/{id}",
                provider_operation.id,
                provider_implementation.id,
                (provider_evidence.id,),
            ),
        ),
        (provider_evidence,),
        (),
    )
    consumer_evidence = _evidence(consumer_repo, generation_id, "CheckoutService.loadOrder")
    consumer_implementation = _implementation(consumer_repo, generation_id, consumer_evidence.id, "CheckoutService")
    consumer_call = ConsumerMethodCall(
        consumer_repo,
        "checkout",
        "checkout",
        REVISIONS[consumer_repo],
        generation_id,
        consumer_implementation.id,
        provider_operation.id,
        "operation",
        (consumer_evidence.id,),
    )
    consumer_fact = MethodFacts(
        "remote-query-proof",
        "1",
        consumer_repo,
        REVISIONS[consumer_repo],
        generation_id,
        (),
        (consumer_implementation,),
        (consumer_call,),
        (),
        (consumer_evidence,),
        (),
    )
    isolated_evidence = _evidence("isolated-catalog", generation_id, "CatalogService.find")
    isolated_fact = MethodFacts(
        "remote-query-proof",
        "1",
        "isolated-catalog",
        REVISIONS["isolated-catalog"],
        generation_id,
        (),
        (_implementation("isolated-catalog", generation_id, isolated_evidence.id, "CatalogService"),),
        (),
        (),
        (isolated_evidence,),
        (),
    )
    return provider_fact, consumer_fact, isolated_fact


def _evidence(repo_id: str, generation_id: str, subject: str) -> MethodEvidence:
    return MethodEvidence(
        repo_id,
        repo_id.split("-")[0],
        repo_id.split("-")[0],
        REVISIONS[repo_id],
        generation_id,
        "src/main/java/example/Service.java",
        1,
        1,
        "remote-query-proof",
        "1",
        "method",
        subject,
        1.0,
    )


def _implementation(repo_id: str, generation_id: str, evidence_id: str, class_name: str) -> ImplementationMethod:
    return ImplementationMethod(
        repo_id,
        repo_id.split("-")[0],
        repo_id.split("-")[0],
        REVISIONS[repo_id],
        generation_id,
        f"example.{class_name}",
        "find",
        f"example.{class_name}#find():void",
        "src/main/java/example/Service.java",
        (evidence_id,),
    )


def _all_service_directory_pages(
    service: WorkspaceServiceGraphQueryService,
    principal: PrincipalIdentity,
    request: WorkspaceGraphQueryRequest,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    nodes: dict[str, dict[str, object]] = {}
    edges: dict[str, dict[str, object]] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    max_pages = (request.node_limit + request.page_size - 1) // request.page_size
    for _ in range(max_pages):
        page = service.service_directory(principal, replace(request, cursor=cursor))
        nodes.update({str(node["id"]): node for node in page.nodes})
        edges.update({str(edge["id"]): edge for edge in page.edges})
        if page.next_cursor is None:
            return tuple(nodes.values()), tuple(edges.values())
        if page.next_cursor in seen_cursors:
            pytest.fail("service directory returned a repeated cursor")
        seen_cursors.add(page.next_cursor)
        cursor = page.next_cursor
    pytest.fail("service directory exceeded the request node_limit pagination bound")


@dataclass
class _RemoteWorkspaceGraph:
    workspace: Workspace
    driver: object
    workspace_repository: Neo4jWorkspaceRepository
    acl_repository: Neo4jWorkspaceAclRepository
    service: WorkspaceServiceGraphQueryService
    orchestrator: WorkspaceServiceGraphPublishOrchestrator
    namespaces: list[str]

    def publish(self, generation_id: str, expected_active: str | None, runtime_root: Path = FIXTURE) -> object:
        namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(self.workspace.workspace_id, generation_id)
        self.namespaces.append(namespace)
        return self.orchestrator.publish(_input(self.workspace, generation_id, expected_active, runtime_root))


@dataclass(frozen=True)
class _RemoteNeo4j:
    driver: object
    workspace_repository: Neo4jWorkspaceRepository
    acl_repository: Neo4jWorkspaceAclRepository
    publish_factory: Neo4jWorkspaceServiceGraphPublishComponentFactory


def _consume(session: object, query: str, **params: object) -> None:
    result = session.run(query, **params)  # type: ignore[union-attr]
    result.consume()


def _cleanup_workspace_graph(environment: _RemoteWorkspaceGraph) -> None:
    """Delete only the nodes and relationships attached to this test workspace's exact scopes."""
    graph_labels = ("ServiceDefinition", "Endpoint", "Evidence")
    method_labels = (
        "ServiceOperation",
        "ImplementationMethod",
        "ConsumerMethodCall",
        "OperationBinding",
        "MethodEvidence",
        "MethodUnresolved",
        "MethodCallTarget",
    )
    with environment.driver.session() as session:  # type: ignore[union-attr]
        for namespace in environment.namespaces:
            for label in graph_labels:
                _consume(
                    session,
                    f"MATCH (node:{label} {{_ontoagent_namespace: $namespace}}) DETACH DELETE node",
                    namespace=namespace,
                )
            for label in method_labels:
                _consume(
                    session,
                    f"MATCH (node:{label} {{workspaceId: $workspace_id, namespace: $namespace}}) DETACH DELETE node",
                    workspace_id=environment.workspace.workspace_id,
                    namespace=namespace,
                )
            _consume(
                session,
                "MATCH (receipt:OntoAgentWorkspaceServiceGraphReceipt "
                "{workspaceId: $workspace_id, namespace: $namespace}) DETACH DELETE receipt",
                workspace_id=environment.workspace.workspace_id,
                namespace=namespace,
            )
        for label in (
            "OntoAgentWorkspaceAclGrant",
            "OntoAgentWorkspaceBuildTask",
            "OntoAgentWorkspaceRepositorySnapshot",
            "OntoAgentWorkspaceGeneration",
            "OntoAgentWorkspaceActiveBinding",
            "OntoAgentWorkspace",
        ):
            _consume(
                session,
                f"MATCH (node:{label} {{workspaceId: $workspace_id}}) DETACH DELETE node",
                workspace_id=environment.workspace.workspace_id,
            )


@pytest.fixture(scope="session")
def remote_neo4j() -> Iterator[_RemoteNeo4j]:
    """Create one remote driver and schema-ready publish factory for this integration module."""
    uri, user, password = _credentials()
    driver = GraphDatabase.driver(uri, auth=(user, password))
    workspace_repository = Neo4jWorkspaceRepository(driver)
    acl_repository = Neo4jWorkspaceAclRepository(driver)
    publish_factory = Neo4jWorkspaceServiceGraphPublishComponentFactory(
        driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
    )
    try:
        yield _RemoteNeo4j(driver, workspace_repository, acl_repository, publish_factory)
    finally:
        driver.close()


@pytest.fixture
def remote_workspace_graph(remote_neo4j: _RemoteNeo4j) -> Iterator[_RemoteWorkspaceGraph]:
    """Create one remote Neo4j workspace and remove only its recorded graph scopes after the test."""
    workspace = Workspace(f"workspace-query-{uuid4()}", "Workspace query integration")
    service = WorkspaceServiceGraphQueryService(
        WorkspaceQueryAuthorizationService(remote_neo4j.acl_repository),
        Neo4jWorkspaceServiceGraphQueryRepository(remote_neo4j.driver),
        b"remote-workspace-query-cursor-secret",
    )
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(remote_neo4j.publish_factory)
    environment = _RemoteWorkspaceGraph(
        workspace,
        remote_neo4j.driver,
        remote_neo4j.workspace_repository,
        remote_neo4j.acl_repository,
        service,
        orchestrator,
        [],
    )
    try:
        yield environment
    finally:
        _cleanup_workspace_graph(environment)


def test_workspace_graph_query_remote_active_publish_receipt_and_full_query(
    remote_workspace_graph: _RemoteWorkspaceGraph,
) -> None:
    environment = remote_workspace_graph
    generation_id = f"generation-query-active-{uuid4()}"
    full = PrincipalIdentity(f"full-{uuid4()}")
    first = environment.publish(generation_id, None)

    assert first.status is WorkspacePublishStatus.ACTIVE
    assert first.graph_write_confirmed is True
    namespace = environment.namespaces[0]
    with environment.driver.session() as session:  # type: ignore[union-attr]
        receipt = session.run(
            "MATCH (receipt:OntoAgentWorkspaceServiceGraphReceipt {workspaceId: $workspace_id, "
            "generationId: $generation_id, namespace: $namespace}) "
            "RETURN receipt.confirmed AS confirmed, receipt.nodeCount AS node_count, "
            "receipt.relationCount AS relation_count, receipt.fingerprint AS fingerprint",
            workspace_id=environment.workspace.workspace_id,
            generation_id=generation_id,
            namespace=namespace,
        ).single()
    assert receipt is not None
    assert receipt["confirmed"] is True
    assert receipt["node_count"] > 0 and receipt["relation_count"] > 0
    assert isinstance(receipt["fingerprint"], str) and receipt["fingerprint"]

    assert (
        environment.acl_repository.upsert_grant(WorkspaceGrant.full(full, environment.workspace.workspace_id)).principal
        == full
    )
    provider_operation = _method_facts(generation_id)[0].operations[0]
    consumer_call = _method_facts(generation_id)[1].consumer_calls[0]
    provider_evidence = _method_facts(generation_id)[0].evidences[0]
    request = WorkspaceGraphQueryRequest(environment.workspace.workspace_id, page_size=100, node_limit=1000)
    full_page = environment.service.service_directory(full, request)
    full_nodes, full_edges = _all_service_directory_pages(environment.service, full, request)
    full_node_ids = {str(node["id"]) for node in full_nodes}

    assert full_page.visibility.value == "full"
    assert {provider_operation.id, consumer_call.id, provider_evidence.id}.issubset(full_node_ids)
    assert any(
        edge["relation_type"] == "CALLS_OPERATION"
        and edge["source_id"] == consumer_call.id
        and edge["target_id"] == provider_operation.id
        for edge in full_edges
    )


def test_workspace_graph_query_remote_filtered_and_ungranted_physical_filtering(
    remote_workspace_graph: _RemoteWorkspaceGraph,
    tmp_path: Path,
) -> None:
    environment = remote_workspace_graph
    generation_id = f"generation-query-filtered-{uuid4()}"
    full = PrincipalIdentity(f"full-{uuid4()}")
    filtered = PrincipalIdentity(f"filtered-{uuid4()}")
    ungranted = PrincipalIdentity(f"ungranted-{uuid4()}")
    for repo_id in REVISIONS:
        (tmp_path / repo_id).mkdir()
    assert environment.publish(generation_id, None, tmp_path).status is WorkspacePublishStatus.ACTIVE
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

    provider_operation = _method_facts(generation_id)[0].operations[0]
    consumer_caller = _method_facts(generation_id)[1].implementations[0]
    consumer_call = _method_facts(generation_id)[1].consumer_calls[0]
    provider_evidence = _method_facts(generation_id)[0].evidences[0]
    consumer_evidence = _method_facts(generation_id)[1].evidences[0]
    request = WorkspaceGraphQueryRequest(environment.workspace.workspace_id, page_size=100, node_limit=1000)
    full_page = environment.service.service_directory(full, request)
    filtered_page = environment.service.service_directory(filtered, request)
    narrowed_page = environment.service.service_directory(
        full,
        WorkspaceGraphQueryRequest(
            environment.workspace.workspace_id, repo_id="consumer-checkout", page_size=100, node_limit=1000
        ),
    )
    full_node_ids = {str(node["id"]) for node in full_page.nodes}
    filtered_payload = json.dumps({"nodes": filtered_page.nodes, "edges": filtered_page.edges}, sort_keys=True)

    assert full_page.visibility.value == "full"
    assert full_page.next_cursor is None
    assert {consumer_caller.id, consumer_call.id, provider_operation.id, provider_evidence.id}.issubset(full_node_ids)
    assert any(
        edge["relation_type"] == "CALLS_OPERATION"
        and edge["source_id"] == consumer_call.id
        and edge["target_id"] == provider_operation.id
        for edge in full_page.edges
    )
    assert filtered_page.visibility.value == "filtered"
    assert filtered_page.next_cursor is None
    assert all(node.get("repo_id", node.get("repoId")) == "consumer-checkout" for node in filtered_page.nodes)
    assert all(set(edge["repo_ids"]) <= {"consumer-checkout"} for edge in filtered_page.edges)
    assert {consumer_call.id, consumer_evidence.id}.issubset({str(node["id"]) for node in filtered_page.nodes})
    assert provider_operation.id not in filtered_payload
    assert provider_evidence.id not in filtered_payload
    assert "provider-orders" not in filtered_payload
    assert "total" not in filtered_payload and "count" not in filtered_payload

    assert narrowed_page.visibility.value == "full"
    assert narrowed_page.next_cursor is None
    assert {str(node["id"]) for node in narrowed_page.nodes} <= full_node_ids
    assert {str(edge["id"]) for edge in narrowed_page.edges} <= {str(edge["id"]) for edge in full_page.edges}
    assert consumer_call.id in {str(node["id"]) for node in narrowed_page.nodes}
    with pytest.raises(WorkspaceAuthorizationError) as denied:
        environment.service.service_directory(ungranted, request)
    assert denied.value.failure is WorkspaceAuthorizationFailure.FORBIDDEN


def test_workspace_graph_query_remote_endpoint_methods_real_acl_safe_subgraph(
    remote_workspace_graph: _RemoteWorkspaceGraph,
) -> None:
    """Resolve a published endpoint from the real directory and read its method neighborhood."""
    environment = remote_workspace_graph
    generation_id = f"generation-query-endpoint-methods-{uuid4()}"
    full = PrincipalIdentity(f"full-endpoint-methods-{uuid4()}")
    filtered = PrincipalIdentity(f"filtered-endpoint-methods-{uuid4()}")
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

    request = WorkspaceGraphQueryRequest(environment.workspace.workspace_id, page_size=100, node_limit=1000)
    full_nodes, _ = _all_service_directory_pages(environment.service, full, request)
    provider_nodes = [
        node
        for node in full_nodes
        if node.get("repo_id", node.get("repoId")) == "provider-orders"
        and node.get("node_type") in {"Endpoint", "ServiceOperation"}
    ]
    assert provider_nodes
    endpoint = next((node for node in provider_nodes if node.get("node_type") == "Endpoint"), provider_nodes[0])
    endpoint_id = str(endpoint["id"])
    if endpoint.get("node_type") != "Endpoint":
        endpoint_id = str(endpoint.get("endpoint_id", endpoint.get("endpointId", endpoint_id)))

    full_page = environment.service.endpoint_methods(full, request, endpoint_id)
    full_ids = {str(node["id"]) for node in full_page.nodes}
    assert endpoint_id in full_ids
    assert any(
        node.get("node_type")
        in {
            "ServiceOperation",
            "ImplementationMethod",
            "ConsumerMethodCall",
            "OperationBinding",
            "MethodEvidence",
            "Evidence",
        }
        for node in full_page.nodes
    )
    assert all({str(edge["source_id"]), str(edge["target_id"])} <= full_ids for edge in full_page.edges)
    assert all("factPayload" not in node for node in full_page.nodes)

    filtered_page = environment.service.endpoint_methods(filtered, request, endpoint_id)
    assert filtered_page.nodes == ()
    assert filtered_page.edges == ()
    filtered_payload = json.dumps({"nodes": filtered_page.nodes, "edges": filtered_page.edges}, sort_keys=True)
    assert "provider-orders" not in filtered_payload
    assert "factPayload" not in filtered_payload


def test_workspace_graph_query_remote_cursor_generation_and_receipt_boundaries(
    remote_workspace_graph: _RemoteWorkspaceGraph,
) -> None:
    environment = remote_workspace_graph
    generation_one = f"generation-query-one-{uuid4()}"
    generation_two = f"generation-query-two-{uuid4()}"
    pending_generation = f"generation-query-pending-{uuid4()}"
    full = PrincipalIdentity(f"full-{uuid4()}")
    other = PrincipalIdentity(f"other-{uuid4()}")
    assert environment.publish(generation_one, None).status is WorkspacePublishStatus.ACTIVE
    assert (
        environment.acl_repository.upsert_grant(WorkspaceGrant.full(full, environment.workspace.workspace_id)).principal
        == full
    )
    assert (
        environment.acl_repository.upsert_grant(
            WorkspaceGrant.full(other, environment.workspace.workspace_id)
        ).principal
        == other
    )
    request = WorkspaceGraphQueryRequest(environment.workspace.workspace_id, page_size=100, node_limit=1000)
    cursor_page = environment.service.service_directory(
        full, WorkspaceGraphQueryRequest(environment.workspace.workspace_id, page_size=1)
    )
    assert cursor_page.next_cursor is not None
    with pytest.raises(WorkspaceGraphQueryValidationError):
        environment.service.service_directory(
            full,
            WorkspaceGraphQueryRequest(
                environment.workspace.workspace_id, page_size=1, cursor=cursor_page.next_cursor + "x"
            ),
        )
    with pytest.raises(WorkspaceGraphQueryValidationError):
        environment.service.service_directory(
            other,
            WorkspaceGraphQueryRequest(environment.workspace.workspace_id, page_size=1, cursor=cursor_page.next_cursor),
        )

    pending = WorkspaceGeneration(
        environment.workspace.workspace_id,
        pending_generation,
        _input(environment.workspace, pending_generation, generation_one).snapshots,
    )
    assert environment.workspace_repository.create_generation(pending) == pending
    with pytest.raises(WorkspaceAuthorizationError) as inactive:
        environment.service.service_directory(
            full, WorkspaceGraphQueryRequest(environment.workspace.workspace_id, generation_id=pending_generation)
        )
    assert inactive.value.failure is WorkspaceAuthorizationFailure.CONFLICT

    assert environment.publish(generation_two, generation_one).status is WorkspacePublishStatus.ACTIVE
    with pytest.raises(WorkspaceGraphQueryValidationError):
        environment.service.service_directory(
            full,
            WorkspaceGraphQueryRequest(environment.workspace.workspace_id, page_size=1, cursor=cursor_page.next_cursor),
        )
    with pytest.raises(WorkspaceAuthorizationError) as superseded:
        environment.service.service_directory(
            full, WorkspaceGraphQueryRequest(environment.workspace.workspace_id, generation_id=generation_one)
        )
    assert superseded.value.failure is WorkspaceAuthorizationFailure.CONFLICT

    with environment.driver.session() as session:  # type: ignore[union-attr]
        _consume(
            session,
            "MATCH (receipt:OntoAgentWorkspaceServiceGraphReceipt "
            "{workspaceId: $workspace_id, generationId: $generation_id}) SET receipt.confirmed = false",
            workspace_id=environment.workspace.workspace_id,
            generation_id=generation_two,
        )
    with pytest.raises(WorkspaceAuthorizationError) as untrusted:
        environment.service.service_directory(full, request)
    assert untrusted.value.failure is WorkspaceAuthorizationFailure.CONFLICT
