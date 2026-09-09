"""Remote Neo4j coverage for workspace-scoped service graph publication."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.domain.workspace_authorization import PrincipalIdentity, WorkspaceAuthorizationFailure, WorkspaceGrant
from ontoagent.domain.workspace_graph_query import WorkspaceGraphQueryRequest
from ontoagent.execution.workspace_query_authorization import (
    WorkspaceAuthorizationError,
    WorkspaceQueryAuthorizationService,
)
from ontoagent.execution.workspace_service_graph_query import WorkspaceServiceGraphQueryService
from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext
from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.dubbo_method import DubboMethodDetector
from ontoagent.parsing.service_graph.detectors.feign_method import FeignMethodDetector
from ontoagent.parsing.service_graph.detectors.grpc_method import GrpcMethodDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.messaging_method import MessagingMethodDetector
from ontoagent.parsing.service_graph.detectors.python_http_method import PythonHttpMethodDetector
from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.detectors.spring_http_method import SpringHttpMethodDetector
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder
from ontoagent.parsing.service_graph.graph_writer import GraphWriter, WriteReceipt
from ontoagent.parsing.service_graph.java_contract_index import (
    ContractSourceMapping,
    ContractSourceRole,
    JavaContractSource,
)
from ontoagent.parsing.service_graph.method_graph_writer import MethodGraphScope, MethodGraphWritePlan
from ontoagent.parsing.service_graph.methods import (
    ConsumerMethodCall,
    ImplementationMethod,
    MethodEvidence,
    MethodFacts,
    OperationBinding,
    ServiceOperation,
)
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.neo4j_graph_sink import Neo4jGraphSink
from ontoagent.parsing.service_graph.provider_method_binder import AuthorizedProviderSource
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver
from ontoagent.parsing.service_graph.workspace.models import (
    Workspace,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.neo4j_query_repository import Neo4jWorkspaceServiceGraphQueryRepository
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import (
    Neo4jWorkspaceServiceGraphPublishComponentFactory,
    WorkspacePublishStatus,
    WorkspaceServiceGraphPublishComponents,
    WorkspaceServiceGraphPublishInput,
    WorkspaceServiceGraphPublishOrchestrator,
)
from ontoagent.parsing.service_graph.workspace_java_rpc_resolution import WorkspaceJavaRpcAuthorization
from ontoagent.store.neo4j_workspace_acl_repository import Neo4jWorkspaceAclRepository

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "fixtures/service_graph/neutral_three_repo"
CONFIGURED_MESSAGING_FIXTURE = Path(__file__).parents[1] / "fixtures/service_graph/configured_messaging_three_repo"
GRPC_FIXTURE = Path(__file__).parents[1] / "fixtures/service_graph/neutral_grpc_three_repo"
PYTHON_HTTP_FIXTURE = Path(__file__).parents[1] / "fixtures/service_graph/python_http_three_repo"
XML_DUBBO_FIXTURE = Path(__file__).parents[1] / "fixtures/service_graph/xml_dubbo_three_repo"
JAVA_RPC_CONTRACTS_FIXTURE = Path(__file__).parents[1] / "fixtures/java_rpc_contracts"
PYTHON_HTTP_REVISIONS = {
    "provider-api": "provider-v1",
    "consumer-client": "consumer-v1",
    "isolated-worker": "worker-v1",
}
REVISIONS = {
    "provider-orders": "fixture-provider-v1",
    "consumer-checkout": "fixture-consumer-v1",
    "isolated-catalog": "fixture-isolated-v1",
}
XML_DUBBO_REVISIONS = {
    "provider-orders": "xml-provider-v1",
    "consumer-checkout": "xml-consumer-v1",
    "isolated-catalog": "xml-isolated-v1",
}


def _d1_revisions() -> dict[str, str]:
    manifest = json.loads((JAVA_RPC_CONTRACTS_FIXTURE / "expected.json").read_text(encoding="utf-8"))
    repositories = manifest["repositories"]
    assert isinstance(repositories, list)
    return {
        item["repo_id"]: item["revision"]
        for item in repositories
        if item["repo_id"] in {"sample-order-contract", "sample-order-provider", "sample-checkout-consumer"}
    }


D1_REVISIONS = _d1_revisions()


def _d1_authorization(mappings: tuple[ContractSourceMapping, ...]) -> WorkspaceJavaRpcAuthorization:
    return WorkspaceJavaRpcAuthorization(
        (
            JavaContractSource(
                "sample-order-contract",
                "sample-order-contract",
                D1_REVISIONS["sample-order-contract"],
                JAVA_RPC_CONTRACTS_FIXTURE / "sample-order-contract",
                ContractSourceRole.API,
            ),
        ),
        mappings,
        frozenset(
            {
                AuthorizedProviderSource(
                    "sample-order-provider",
                    "sample-order-provider",
                    D1_REVISIONS["sample-order-provider"],
                )
            }
        ),
        lambda resolution, operation, binding: (
            operation.group == "orders"
            and operation.version == "1.0"
            and operation.alias is None
            and binding.provider_endpoint_reference.endswith("|group=orders|version=1.0|alias=")
        ),
    )


def _d1_mapping(consumer_repo_id: str) -> ContractSourceMapping:
    return ContractSourceMapping(
        consumer_repo_id,
        consumer_repo_id,
        D1_REVISIONS[consumer_repo_id],
        "sample-order-contract",
        "sample-order-contract",
        D1_REVISIONS["sample-order-contract"],
        "1.0",
        "workspace-contracts.yaml",
        1,
        1,
    )


def _d1_input(
    workspace: Workspace,
    generation_id: str,
    expected_active: str | None,
    mappings: tuple[ContractSourceMapping, ...],
) -> WorkspaceServiceGraphPublishInput:
    repositories = ("sample-order-contract", "sample-order-provider", "sample-checkout-consumer")
    snapshots = tuple(
        WorkspaceRepositorySnapshot(
            workspace.workspace_id,
            repo_id,
            "main",
            D1_REVISIONS[repo_id],
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id in repositories
    )
    runtime = tuple(
        RepositorySnapshot(repo_id, D1_REVISIONS[repo_id], JAVA_RPC_CONTRACTS_FIXTURE / repo_id, frozenset({"java"}))
        for repo_id in repositories
    )
    return WorkspaceServiceGraphPublishInput(
        workspace,
        snapshots,
        runtime,
        f"request-{generation_id}",
        generation_id,
        expected_active,
        (),
        (),
        _d1_authorization(mappings),
    )


def test_workspace_publisher_persists_source_pinned_d1_java_rpc_only_when_explicitly_mapped() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-d1-java-rpc-{uuid4()}", "D1 Java RPC remote integration")
    mapped_generation = f"generation-d1-mapped-{uuid4()}"
    unmapped_generation = f"generation-d1-unmapped-{uuid4()}"
    namespaces = tuple(
        WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation)
        for generation in (mapped_generation, unmapped_generation)
    )
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    contract_signature = "example.orders.api.OrderService#getOrder(java.lang.String):example.orders.api.OrderSummary"
    implementation_signature = (
        "example.orders.provider.OrderServiceProvider#getOrder(java.lang.String):example.orders.api.OrderSummary"
    )
    try:
        mapped = orchestrator.publish(
            _d1_input(
                workspace,
                mapped_generation,
                None,
                (_d1_mapping("sample-order-provider"), _d1_mapping("sample-checkout-consumer")),
            )
        )

        assert mapped.status is WorkspacePublishStatus.ACTIVE
        assert mapped.candidate_namespace == namespaces[0]
        for repo_id in ("sample-order-provider", "sample-checkout-consumer"):
            assert not (
                JAVA_RPC_CONTRACTS_FIXTURE / repo_id / "src/main/java/example/orders/api/OrderService.java"
            ).exists()

        with driver.session() as session:
            evidence_rows = session.run(
                "MATCH (e:MethodEvidence {namespace: $namespace}) RETURN e.id AS id, e.factPayload AS fact_payload",
                namespace=namespaces[0],
            ).data()
            chain_rows = session.run(
                "MATCH (implementation:ImplementationMethod {namespace: $namespace, repoId: 'sample-checkout-consumer'}) "
                "-[:CALLER_METHOD]->(call:ConsumerMethodCall {namespace: $namespace}) "
                "-[:CALLS_OPERATION]->(operation:ServiceOperation {namespace: $namespace, "
                "repoId: 'sample-order-provider'}) "
                "MATCH (call)-[:METHOD_EVIDENCE]->(call_evidence:MethodEvidence {namespace: $namespace}) "
                "MATCH (binding:OperationBinding {namespace: $namespace, repoId: 'sample-order-provider'}) "
                "-[:OPERATION_BINDING]->(operation) "
                "MATCH (binding)-[:BINDS_IMPLEMENTATION]->"
                "(provider_implementation:ImplementationMethod {namespace: $namespace, "
                "repoId: 'sample-order-provider'}) "
                "RETURN call.id AS call_id, call.factPayload AS call_payload, "
                "call_evidence.id AS call_evidence_id, call_evidence.factPayload AS call_evidence_payload, "
                "operation.id AS operation_id, operation.factPayload AS operation_payload, "
                "binding.id AS binding_id, binding.factPayload AS binding_payload, "
                "provider_implementation.id AS implementation_id, "
                "provider_implementation.factPayload AS implementation_payload",
                namespace=namespaces[0],
            ).data()

        contract_evidences = [
            evidence
            for row in evidence_rows
            for evidence in json.loads(row["fact_payload"])["evidences"]
            if evidence["id"] == row["id"]
            and evidence["repo_id"] == "sample-order-contract"
            and evidence["source_revision"] == D1_REVISIONS["sample-order-contract"]
            and evidence["file_path"] == "src/main/java/example/orders/api/OrderService.java"
        ]
        a01_chains = []
        for row in chain_rows:
            call = next(
                item for item in json.loads(row["call_payload"])["consumer_calls"] if item["id"] == row["call_id"]
            )
            call_evidence = next(
                item
                for item in json.loads(row["call_evidence_payload"])["evidences"]
                if item["id"] == row["call_evidence_id"]
            )
            operation = next(
                item for item in json.loads(row["operation_payload"])["operations"] if item["id"] == row["operation_id"]
            )
            binding = next(
                item for item in json.loads(row["binding_payload"])["bindings"] if item["id"] == row["binding_id"]
            )
            provider_implementation = next(
                item
                for item in json.loads(row["implementation_payload"])["implementations"]
                if item["id"] == row["implementation_id"]
            )
            if (
                call["target_reference"].startswith(f"dubbo-operation:{contract_signature}|group=orders|version=1.0")
                and call_evidence["file_path"] == "src/main/java/example/checkout/CheckoutService.java"
                and call_evidence["start_line"] == 20
                and operation["canonical_signature"] == contract_signature
                and binding["operation_id"] == operation["id"]
                and binding["implementation_id"] == provider_implementation["id"]
                and provider_implementation["canonical_signature"] == implementation_signature
            ):
                a01_chains.append(row)
        assert contract_evidences
        assert len(a01_chains) == 1
        assert contract_signature != implementation_signature

        unmapped = orchestrator.publish(_d1_input(workspace, unmapped_generation, mapped_generation, ()))

        assert unmapped.status is WorkspacePublishStatus.ACTIVE
        assert unmapped.candidate_namespace == namespaces[1]
        with driver.session() as session:
            namespace_counts = session.run(
                "MATCH (n) WHERE n._ontoagent_namespace IN $namespaces OR n.namespace IN $namespaces "
                "RETURN n.namespace AS namespace, n.generationId AS generation_id, count(n) AS count "
                "ORDER BY namespace, generation_id",
                namespaces=list(namespaces),
            ).data()
            unmapped_cross_repo_calls = session.run(
                "MATCH (:ImplementationMethod {namespace: $namespace, repoId: 'sample-checkout-consumer'}) "
                "-[:CALLER_METHOD]->(:ConsumerMethodCall {namespace: $namespace}) "
                "-[:CALLS_OPERATION]->(:ServiceOperation {namespace: $namespace, repoId: 'sample-order-provider'}) "
                "RETURN count(*) AS count",
                namespace=namespaces[1],
            ).single()["count"]
            unmapped_provider_operations = session.run(
                "MATCH (operation:ServiceOperation {namespace: $namespace, repoId: 'sample-order-provider'}) "
                "RETURN count(operation) AS count",
                namespace=namespaces[1],
            ).single()["count"]

        assert {row["namespace"] for row in namespace_counts if row["namespace"] is not None} == set(namespaces)
        assert {row["generation_id"] for row in namespace_counts if row["generation_id"] is not None} == {
            mapped_generation,
            unmapped_generation,
        }
        assert unmapped_cross_repo_calls == 0
        assert unmapped_provider_operations == 0
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (n) WHERE n._ontoagent_namespace IN $namespaces OR n.namespace IN $namespaces DETACH DELETE n",
                namespaces=list(namespaces),
            )
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace.workspace_id,
            )
            remaining = session.run(
                "MATCH (n) WHERE n._ontoagent_namespace IN $namespaces OR n.namespace IN $namespaces "
                "RETURN count(n) AS count",
                namespaces=list(namespaces),
            ).single()["count"]
        driver.close()
        assert remaining == 0


def test_workspace_publisher_reresolves_d1_java_rpc_across_generations_without_leaking_superseded_graph() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-d1-reresolution-{uuid4()}", "D1 Java RPC re-resolution")
    generation_one = f"generation-d1-unmapped-{uuid4()}"
    generation_two = f"generation-d1-mapped-{uuid4()}"
    generation_three = f"generation-d1-stale-{uuid4()}"
    namespaces = tuple(
        WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation)
        for generation in (generation_one, generation_two, generation_three)
    )
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    repository = Neo4jWorkspaceRepository(driver)
    acl_repository = Neo4jWorkspaceAclRepository(driver)
    principal = PrincipalIdentity(f"d1-reader-{uuid4()}")
    query_service = WorkspaceServiceGraphQueryService(
        WorkspaceQueryAuthorizationService(acl_repository),
        Neo4jWorkspaceServiceGraphQueryRepository(driver),
        b"d1-java-rpc-reresolution-query-secret",
    )
    try:
        first = orchestrator.publish(_d1_input(workspace, generation_one, None, ()))

        assert first.status is WorkspacePublishStatus.ACTIVE
        with driver.session() as session:
            unmapped_operations = session.run(
                "MATCH (operation:ServiceOperation {namespace: $namespace, repoId: 'sample-order-provider'}) "
                "RETURN count(operation) AS count",
                namespace=namespaces[0],
            ).single()["count"]
            unmapped_cross_repo_calls = session.run(
                "MATCH (:ImplementationMethod {namespace: $namespace, repoId: 'sample-checkout-consumer'}) "
                "-[:CALLER_METHOD]->(:ConsumerMethodCall {namespace: $namespace}) "
                "-[:CALLS_OPERATION]->(:ServiceOperation {namespace: $namespace, repoId: 'sample-order-provider'}) "
                "RETURN count(*) AS count",
                namespace=namespaces[0],
            ).single()["count"]
        assert unmapped_operations == 0
        assert unmapped_cross_repo_calls == 0

        second = orchestrator.publish(
            _d1_input(
                workspace,
                generation_two,
                generation_one,
                (_d1_mapping("sample-order-provider"), _d1_mapping("sample-checkout-consumer")),
            )
        )

        assert second.status is WorkspacePublishStatus.ACTIVE
        assert repository.get_active_binding(workspace.workspace_id).generation_id == generation_two  # type: ignore[union-attr]
        assert (
            repository.get_generation(workspace.workspace_id, generation_one).state
            is WorkspaceGenerationState.SUPERSEDED
        )  # type: ignore[union-attr]
        with driver.session() as session:
            old_namespace_nodes = session.run(
                "MATCH (node) WHERE node._ontoagent_namespace = $namespace OR node.namespace = $namespace "
                "RETURN count(node) AS count",
                namespace=namespaces[0],
            ).single()["count"]
            a01_chains = session.run(
                "MATCH (:ImplementationMethod {namespace: $namespace, repoId: 'sample-checkout-consumer'}) "
                "-[:CALLER_METHOD]->(call:ConsumerMethodCall {namespace: $namespace}) "
                "-[:CALLS_OPERATION]->(:ServiceOperation {namespace: $namespace, repoId: 'sample-order-provider'}) "
                "RETURN call.id AS call_id, call.factPayload AS fact_payload",
                namespace=namespaces[1],
            ).data()
            mixed_generation_relations = session.run(
                "MATCH (source {workspaceId: $workspace_id})-[relation]->(target {workspaceId: $workspace_id}) "
                "WHERE source.namespace IN $namespaces AND target.namespace IN $namespaces "
                "AND source.generationId <> target.generationId "
                "RETURN count(relation) AS count",
                workspace_id=workspace.workspace_id,
                namespaces=list(namespaces[:2]),
            ).single()["count"]
        assert old_namespace_nodes > 0
        a01_calls = [
            next(item for item in json.loads(row["fact_payload"])["consumer_calls"] if item["id"] == row["call_id"])
            for row in a01_chains
        ]
        assert any(
            call["target_reference"].startswith(
                "dubbo-operation:example.orders.api.OrderService#getOrder("
                "java.lang.String):example.orders.api.OrderSummary"
            )
            for call in a01_calls
        )
        assert mixed_generation_relations == 0

        assert (
            acl_repository.upsert_grant(WorkspaceGrant.full(principal, workspace.workspace_id)).principal == principal
        )
        active_page = query_service.service_directory(
            principal,
            WorkspaceGraphQueryRequest(workspace.workspace_id, generation_two, page_size=100, node_limit=1000),
        )
        assert any(node["node_type"] == "ConsumerMethodCall" for node in active_page.nodes)
        with pytest.raises(WorkspaceAuthorizationError) as superseded:
            query_service.service_directory(
                principal,
                WorkspaceGraphQueryRequest(workspace.workspace_id, generation_one, page_size=100, node_limit=1000),
            )
        assert superseded.value.failure is WorkspaceAuthorizationFailure.CONFLICT

        stale = orchestrator.publish(
            _d1_input(
                workspace,
                generation_three,
                generation_one,
                (_d1_mapping("sample-order-provider"), _d1_mapping("sample-checkout-consumer")),
            )
        )
        assert stale.status is WorkspacePublishStatus.BLOCKED
        assert repository.get_active_binding(workspace.workspace_id).generation_id == generation_two  # type: ignore[union-attr]
        assert (
            repository.get_generation(workspace.workspace_id, generation_three).state
            is WorkspaceGenerationState.BLOCKED
        )  # type: ignore[union-attr]
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (node) WHERE node._ontoagent_namespace IN $namespaces OR node.namespace IN $namespaces "
                "DETACH DELETE node",
                namespaces=list(namespaces),
            )
            session.run(
                "MATCH (node) WHERE node.workspaceId = $workspace_id "
                "AND (node:OntoAgentWorkspace OR node:OntoAgentWorkspaceBuildTask "
                "OR node:OntoAgentWorkspaceGeneration OR node:OntoAgentWorkspaceRepositorySnapshot "
                "OR node:OntoAgentWorkspaceActiveBinding OR node:OntoAgentWorkspaceAclGrant) DETACH DELETE node",
                workspace_id=workspace.workspace_id,
            )
        driver.close()


class _FailingDetectorRegistry:
    ids = ("deterministic-failure",)

    def detect(self, snapshot: RepositorySnapshot, detector_id: str | None = None) -> object:
        raise RuntimeError("deterministic detector failure")


class _UnconfirmedWriter:
    def __init__(self, writer: GraphWriter) -> None:
        self._writer = writer

    def write(self, plan: object) -> WriteReceipt:
        receipt = self._writer.write(plan)  # type: ignore[arg-type]
        return replace(receipt, confirmed=False)


class _InjectedFactory:
    def __init__(self, driver: object, registry: object, *, unconfirmed: bool = False) -> None:
        self._driver = driver
        self._registry = registry
        self._unconfirmed = unconfirmed

    def create(self, namespace: str) -> WorkspaceServiceGraphPublishComponents:
        writer = GraphWriter(Neo4jGraphSink(self._driver, namespace=namespace))  # type: ignore[arg-type]
        return WorkspaceServiceGraphPublishComponents(
            self._registry,  # type: ignore[arg-type]
            ServiceGraphResolver(),
            GraphPlanBuilder(),
            _UnconfirmedWriter(writer) if self._unconfirmed else writer,
            Neo4jWorkspaceRepository(self._driver),  # type: ignore[arg-type]
        )


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
    method_facts: tuple[MethodFacts, ...] = (),
    source_root: Path = FIXTURE,
    languages: frozenset[str] = frozenset({"java", "yaml"}),
    revisions: dict[str, str] = REVISIONS,
) -> WorkspaceServiceGraphPublishInput:
    frozen = tuple(
        WorkspaceRepositorySnapshot(
            workspace.workspace_id,
            repo_id,
            "main",
            revision,
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id, revision in revisions.items()
    )
    runtime = tuple(
        RepositorySnapshot(repo_id, revision, source_root / repo_id, languages)
        for repo_id, revision in revisions.items()
    )
    return WorkspaceServiceGraphPublishInput(
        workspace, frozen, runtime, f"request-{generation_id}", generation_id, expected_active, (), method_facts
    )


def _method_facts(generation_id: str) -> tuple[MethodFacts, ...]:
    facts: list[MethodFacts] = []
    for repo_id, revision in REVISIONS.items():
        evidence = MethodEvidence(
            repo_id,
            "module",
            "service",
            revision,
            generation_id,
            "src/Service.java",
            1,
            1,
            "generic-java",
            "1",
            "method",
            f"{repo_id}.find",
            1.0,
        )
        operation = ServiceOperation(
            repo_id,
            "module",
            "service",
            revision,
            generation_id,
            "provider",
            f"example.{repo_id}.Api",
            "find",
            f"example.{repo_id}.Api#find():void",
            (evidence.id,),
        )
        implementation = ImplementationMethod(
            repo_id,
            "module",
            "service",
            revision,
            generation_id,
            f"example.{repo_id}.Service",
            "find",
            f"example.{repo_id}.Service#find():void",
            "src/Service.java",
            (evidence.id,),
        )
        facts.append(
            MethodFacts(
                "generic-java",
                "1",
                repo_id,
                revision,
                generation_id,
                (operation,),
                (implementation,),
                (),
                (
                    OperationBinding(
                        repo_id,
                        "module",
                        "service",
                        revision,
                        generation_id,
                        "endpoint-ref",
                        operation.id,
                        implementation.id,
                        (evidence.id,),
                    ),
                ),
                (evidence,),
                (),
            )
        )
    return tuple(facts)


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
        explicit_method_facts = _method_facts(generation_one)
        first = orchestrator.publish(_input(workspace, generation_one, None, explicit_method_facts))
        assert first.status is WorkspacePublishStatus.ACTIVE
        assert first.candidate_namespace == namespaces[0]

        repository = Neo4jWorkspaceRepository(driver)
        assert repository.get_active_binding(workspace.workspace_id).generation_id == generation_one  # type: ignore[union-attr]
        with driver.session() as session:
            count = session.run(
                "MATCH (n { _ontoagent_namespace: $namespace }) RETURN count(n) AS count", namespace=namespaces[0]
            ).single()["count"]
        assert count > 0
        feign_snapshot = RepositorySnapshot(
            "consumer-checkout",
            REVISIONS["consumer-checkout"],
            FIXTURE / "consumer-checkout",
            frozenset({"java", "yaml"}),
        )
        feign_operations = (
            FeignMethodDetector()
            .detect_methods(
                feign_snapshot,
                MethodDetectionContext(
                    "consumer-checkout",
                    "consumer-checkout",
                    "consumer-checkout",
                    feign_snapshot.source_revision,
                    generation_one,
                ),
            )
            .operations
        )
        expected_operations_by_protocol = {
            "explicit": tuple(operation for fact in explicit_method_facts for operation in fact.operations),
            "feign": feign_operations,
            "spring": (
                ServiceOperation(
                    "provider-orders",
                    "provider-orders",
                    "provider-orders",
                    REVISIONS["provider-orders"],
                    generation_one,
                    "provider",
                    "spring-http:GET:/orders/{id}",
                    "get",
                    "example.orders.OrderApi#get(java.lang.String):example.orders.OrderDto",
                    ("expected-evidence",),
                ),
                ServiceOperation(
                    "provider-orders",
                    "provider-orders",
                    "provider-orders",
                    REVISIONS["provider-orders"],
                    generation_one,
                    "provider",
                    "spring-http:POST:/orders",
                    "create",
                    "example.orders.OrderApi#create():example.orders.OrderDto",
                    ("expected-evidence",),
                ),
                ServiceOperation(
                    "provider-orders",
                    "provider-orders",
                    "provider-orders",
                    REVISIONS["provider-orders"],
                    generation_one,
                    "provider",
                    "spring-http:GET:/orders/lookup/by-key",
                    "lookup",
                    "example.orders.OrderApi#lookup(java.lang.String):example.orders.OrderDto",
                    ("expected-evidence",),
                ),
                ServiceOperation(
                    "provider-orders",
                    "provider-orders",
                    "provider-orders",
                    REVISIONS["provider-orders"],
                    generation_one,
                    "provider",
                    "spring-http:GET:/orders/lookup/by-number",
                    "lookup",
                    "example.orders.OrderApi#lookup(long):example.orders.OrderDto",
                    ("expected-evidence",),
                ),
            ),
            "dubbo": (
                ServiceOperation(
                    "provider-orders",
                    "provider-orders",
                    "provider-orders",
                    REVISIONS["provider-orders"],
                    generation_one,
                    "provider",
                    "example.orders.OrderApi",
                    "getOrder",
                    "example.orders.OrderApi#getOrder(java.lang.String):java.lang.String",
                    ("expected-evidence",),
                    group="orders",
                    version="1.0",
                ),
                ServiceOperation(
                    "provider-orders",
                    "provider-orders",
                    "provider-orders",
                    REVISIONS["provider-orders"],
                    generation_one,
                    "provider",
                    "example.orders.OrderApi",
                    "cancelOrder",
                    "example.orders.OrderApi#cancelOrder(java.lang.String):void",
                    ("expected-evidence",),
                    group="orders",
                    version="1.0",
                ),
                ServiceOperation(
                    "isolated-catalog",
                    "isolated-catalog",
                    "isolated-catalog",
                    REVISIONS["isolated-catalog"],
                    generation_one,
                    "provider",
                    "example.catalog.CatalogApi",
                    "lookup",
                    "example.catalog.CatalogApi#lookup(java.lang.String):java.lang.String",
                    ("expected-evidence",),
                    group="catalog",
                    version="9.0",
                ),
            ),
            "messaging": (
                ServiceOperation(
                    "consumer-checkout",
                    "consumer-checkout",
                    "consumer-checkout",
                    REVISIONS["consumer-checkout"],
                    generation_one,
                    "provider",
                    "messaging-operation:kafka|destination=order-events|group=checkout",
                    "consume",
                    "example.checkout.CheckoutService#consume():void",
                    ("expected-evidence",),
                    group="checkout",
                ),
                ServiceOperation(
                    "consumer-checkout",
                    "consumer-checkout",
                    "consumer-checkout",
                    REVISIONS["consumer-checkout"],
                    generation_one,
                    "provider",
                    "messaging-operation:kafka|destination=payments|group=checkout",
                    "consume",
                    "example.checkout.CheckoutService#consume():void",
                    ("expected-evidence",),
                    group="checkout",
                ),
                ServiceOperation(
                    "consumer-checkout",
                    "consumer-checkout",
                    "consumer-checkout",
                    REVISIONS["consumer-checkout"],
                    generation_one,
                    "provider",
                    "messaging-operation:rabbitmq|destination=order.queue|group=checkout-workers",
                    "run",
                    "example.checkout.CheckoutService#run(java.lang.String):void",
                    ("expected-evidence",),
                    group="checkout-workers",
                ),
                ServiceOperation(
                    "consumer-checkout",
                    "consumer-checkout",
                    "consumer-checkout",
                    REVISIONS["consumer-checkout"],
                    generation_one,
                    "provider",
                    "messaging-operation:rabbitmq|destination=audit.queue|group=checkout-workers",
                    "run",
                    "example.checkout.CheckoutService#run(java.lang.String):void",
                    ("expected-evidence",),
                    group="checkout-workers",
                ),
            ),
        }
        assert {protocol: len(operations) for protocol, operations in expected_operations_by_protocol.items()} == {
            "explicit": 3,
            "feign": 5,
            "spring": 4,
            "dubbo": 3,
            "messaging": 4,
        }
        expected_operations = tuple(
            operation for operations in expected_operations_by_protocol.values() for operation in operations
        )
        with driver.session() as session:
            actual_operations = session.run(
                "MATCH (n:ServiceOperation {namespace: $namespace, workspaceId: $workspace_id, "
                "generationId: $generation_id}) "
                "RETURN n.id AS id, labels(n) AS labels, n.factPayload AS fact_payload ORDER BY n.id",
                namespace=namespaces[0],
                workspace_id=workspace.workspace_id,
                generation_id=generation_one,
            ).data()
        expected_rows = sorted(
            (
                operation.id,
                ["ServiceOperation"],
                operation.declaring_interface_fqcn,
                operation.operation_name,
                operation.canonical_signature,
                operation.group,
                operation.version,
            )
            for operation in expected_operations
        )
        actual_rows = []
        for row in actual_operations:
            payload = json.loads(row["fact_payload"])
            operation = next(item for item in payload["operations"] if item["id"] == row["id"])
            actual_rows.append(
                (
                    row["id"],
                    row["labels"],
                    operation["declaring_interface_fqcn"],
                    operation["operation_name"],
                    operation["canonical_signature"],
                    operation["group"],
                    operation["version"],
                )
            )
        assert actual_rows == expected_rows

        second = orchestrator.publish(_input(workspace, generation_two, generation_one))
        assert second.status is WorkspacePublishStatus.ACTIVE
        assert repository.get_active_binding(workspace.workspace_id).generation_id == generation_two  # type: ignore[union-attr]
        assert (
            repository.get_generation(workspace.workspace_id, generation_one).state
            is WorkspaceGenerationState.SUPERSEDED
        )  # type: ignore[union-attr]

        stale = orchestrator.publish(
            _input(workspace, generation_three, generation_one, _method_facts(generation_three))
        )
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


def test_workspace_publisher_links_spring_consumer_method_to_provider_operation() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-spring-methods-{uuid4()}", "Spring method graph integration")
    generation_id = f"generation-spring-methods-{uuid4()}"
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation_id)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    try:
        outcome = orchestrator.publish(_input(workspace, generation_id, None))

        assert outcome.status is WorkspacePublishStatus.ACTIVE
        load_order = ImplementationMethod(
            "consumer-checkout",
            "consumer-checkout",
            "consumer-checkout",
            REVISIONS["consumer-checkout"],
            generation_id,
            "example.checkout.CheckoutService",
            "loadOrder",
            "example.checkout.CheckoutService#loadOrder(java.lang.String):java.lang.Object",
            "src/main/java/example/checkout/CheckoutService.java",
            ("expected-evidence",),
        )
        run = ImplementationMethod(
            "consumer-checkout",
            "consumer-checkout",
            "consumer-checkout",
            REVISIONS["consumer-checkout"],
            generation_id,
            "example.checkout.CheckoutService",
            "run",
            "example.checkout.CheckoutService#run(java.lang.String):void",
            "src/main/java/example/checkout/CheckoutService.java",
            ("expected-evidence",),
        )
        get_order = ServiceOperation(
            "provider-orders",
            "provider-orders",
            "provider-orders",
            REVISIONS["provider-orders"],
            generation_id,
            "provider",
            "spring-http:GET:/orders/{id}",
            "get",
            "example.orders.OrderApi#get(java.lang.String):example.orders.OrderDto",
            ("expected-evidence",),
        )
        create_order = ServiceOperation(
            "provider-orders",
            "provider-orders",
            "provider-orders",
            REVISIONS["provider-orders"],
            generation_id,
            "provider",
            "spring-http:POST:/orders",
            "create",
            "example.orders.OrderApi#create():example.orders.OrderDto",
            ("expected-evidence",),
        )
        with driver.session() as session:
            links = session.run(
                "MATCH (caller:ImplementationMethod {namespace: $namespace, repoId: 'consumer-checkout'}) "
                "-[:CALLER_METHOD]->(call:ConsumerMethodCall)-[:CALLS_OPERATION]->"
                "(operation:ServiceOperation {namespace: $namespace, repoId: 'provider-orders'}) "
                "RETURN caller.id AS caller, call.id AS call, operation.id AS operation "
                "ORDER BY caller, call, operation",
                namespace=namespace,
            ).data()
        expected_links = [
            {
                "caller": load_order.id,
                "call": ConsumerMethodCall(
                    "consumer-checkout",
                    "consumer-checkout",
                    "consumer-checkout",
                    REVISIONS["consumer-checkout"],
                    generation_id,
                    load_order.id,
                    "spring-http:GET:/orders/{id}",
                    "operation",
                    ("expected-evidence",),
                ).id,
                "operation": get_order.id,
            },
            {
                "caller": run.id,
                "call": ConsumerMethodCall(
                    "consumer-checkout",
                    "consumer-checkout",
                    "consumer-checkout",
                    REVISIONS["consumer-checkout"],
                    generation_id,
                    run.id,
                    "spring-http:POST:/orders",
                    "operation",
                    ("expected-evidence",),
                ).id,
                "operation": create_order.id,
            },
        ]
        assert {tuple(item.values()) for item in links} >= {tuple(item.values()) for item in expected_links}
    finally:
        with driver.session() as session:
            session.run("MATCH (n { _ontoagent_namespace: $namespace }) DETACH DELETE n", namespace=namespace)
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace.workspace_id,
            )
        driver.close()


def test_workspace_publisher_links_exact_ordered_feign_method_triples() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-feign-methods-{uuid4()}", "Feign method graph integration")
    generation_id = f"generation-feign-methods-{uuid4()}"
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation_id)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    try:
        assert orchestrator.publish(_input(workspace, generation_id, None)).status is WorkspacePublishStatus.ACTIVE
        provider_snapshot = RepositorySnapshot(
            "provider-orders", REVISIONS["provider-orders"], FIXTURE / "provider-orders", frozenset({"java", "yaml"})
        )
        provider = SpringHttpMethodDetector().detect_methods(
            provider_snapshot,
            MethodDetectionContext(
                "provider-orders",
                "provider-orders",
                "provider-orders",
                provider_snapshot.source_revision,
                generation_id,
            ),
        )
        consumer_snapshot = RepositorySnapshot(
            "consumer-checkout",
            REVISIONS["consumer-checkout"],
            FIXTURE / "consumer-checkout",
            frozenset({"java", "yaml"}),
        )
        consumer = FeignMethodDetector().detect_methods(
            consumer_snapshot,
            MethodDetectionContext(
                "consumer-checkout",
                "consumer-checkout",
                "consumer-checkout",
                consumer_snapshot.source_revision,
                generation_id,
            ),
        )
        plan = MethodGraphWritePlan(
            MethodGraphScope(
                namespace,
                WorkspaceGeneration(
                    workspace.workspace_id,
                    generation_id,
                    tuple(
                        WorkspaceRepositorySnapshot(
                            workspace.workspace_id,
                            repo_id,
                            "main",
                            revision,
                            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
                        )
                        for repo_id, revision in REVISIONS.items()
                    ),
                ),
            ),
            (provider, consumer),
        )
        expected = sorted(
            (call.caller_implementation_id, call.id, plan.operation_id_for(call.target_reference))
            for call in consumer.consumer_calls
            if plan.operation_ids_for(call.target_reference)
        )
        with driver.session() as session:
            actual = [
                (row["caller"], row["call"], row["operation"])
                for row in session.run(
                    "MATCH (caller:ImplementationMethod {namespace: $namespace})-[:CALLER_METHOD]->"
                    "(call:ConsumerMethodCall)-[:CALLS_OPERATION]->"
                    "(operation:ServiceOperation {namespace: $namespace, repoId: 'provider-orders'}) "
                    "WHERE caller.id IN $caller_ids "
                    "RETURN caller.id AS caller, call.id AS call, operation.id AS operation "
                    "ORDER BY caller, call, operation",
                    namespace=namespace,
                    caller_ids=[item[0] for item in expected],
                )
            ]
        assert actual == expected
    finally:
        with driver.session() as session:
            session.run("MATCH (n {namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run("MATCH (n {_ontoagent_namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace.workspace_id,
            )
        driver.close()


def test_workspace_publisher_links_dubbo_consumer_method_to_exact_provider_operation() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-dubbo-methods-{uuid4()}", "Dubbo method graph integration")
    generation_id = f"generation-dubbo-methods-{uuid4()}"
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation_id)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    try:
        outcome = orchestrator.publish(_input(workspace, generation_id, None))

        assert outcome.status is WorkspacePublishStatus.ACTIVE
        snapshots = {
            repo_id: RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
            for repo_id, revision in REVISIONS.items()
        }
        facts = {
            repo_id: DubboMethodDetector().detect_methods(
                snapshot,
                MethodDetectionContext(repo_id, repo_id, repo_id, snapshot.source_revision, generation_id),
            )
            for repo_id, snapshot in snapshots.items()
        }
        caller = next(item for item in facts["consumer-checkout"].implementations if item.method_name == "consume")
        call = facts["consumer-checkout"].consumer_calls[0]
        operation = next(item for item in facts["provider-orders"].operations if item.operation_name == "getOrder")
        with driver.session() as session:
            links = session.run(
                "MATCH (caller:ImplementationMethod {namespace: $namespace})-[:CALLER_METHOD]->"
                "(call:ConsumerMethodCall)-[:CALLS_OPERATION]->"
                "(operation:ServiceOperation {namespace: $namespace}) "
                "RETURN caller.id AS caller, call.id AS call, operation.id AS operation",
                namespace=namespace,
            ).data()
        assert {tuple(row.values()) for row in links} >= {(caller.id, call.id, operation.id)}
    finally:
        with driver.session() as session:
            session.run("MATCH (n { _ontoagent_namespace: $namespace }) DETACH DELETE n", namespace=namespace)
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace.workspace_id,
            )
        driver.close()


def test_workspace_publisher_links_xml_dubbo_caller_call_and_provider_operation() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-xml-dubbo-methods-{uuid4()}", "XML Dubbo method graph integration")
    generation_id = f"generation-xml-dubbo-methods-{uuid4()}"
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation_id)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    try:
        outcome = orchestrator.publish(
            _input(
                workspace,
                generation_id,
                None,
                source_root=XML_DUBBO_FIXTURE,
                languages=frozenset({"java", "xml"}),
                revisions=XML_DUBBO_REVISIONS,
            )
        )

        assert outcome.status is WorkspacePublishStatus.ACTIVE
        snapshots = {
            repo_id: RepositorySnapshot(repo_id, revision, XML_DUBBO_FIXTURE / repo_id, frozenset({"java", "xml"}))
            for repo_id, revision in XML_DUBBO_REVISIONS.items()
        }
        facts = {
            repo_id: DubboMethodDetector().detect_methods(
                snapshot,
                MethodDetectionContext(repo_id, repo_id, repo_id, snapshot.source_revision, generation_id),
            )
            for repo_id, snapshot in snapshots.items()
        }
        caller = next(item for item in facts["consumer-checkout"].implementations if item.method_name == "load")
        call = next(
            item
            for item in facts["consumer-checkout"].consumer_calls
            if "#find(java.lang.String)" in item.target_reference
        )
        operation = next(
            item
            for item in facts["provider-orders"].operations
            if item.canonical_signature == "example.orders.OrderApi#find(java.lang.String):java.lang.String"
        )
        with driver.session() as session:
            links = session.run(
                "MATCH (caller:ImplementationMethod {namespace: $namespace})-[:CALLER_METHOD]->"
                "(call:ConsumerMethodCall)-[:CALLS_OPERATION]->"
                "(operation:ServiceOperation {namespace: $namespace}) "
                "RETURN caller.id AS caller, call.id AS call, operation.id AS operation",
                namespace=namespace,
            ).data()
        targets = {row["operation"] for row in links if row["caller"] == caller.id and row["call"] == call.id}
        assert targets == {operation.id}
    finally:
        with driver.session() as session:
            session.run("MATCH (n {namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run("MATCH (n {_ontoagent_namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace.workspace_id,
            )
        driver.close()


def test_workspace_publisher_links_exact_ordered_python_http_method_triples() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-python-http-methods-{uuid4()}", "Python HTTP method graph integration")
    generation_id = f"generation-python-http-methods-{uuid4()}"
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation_id)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    try:
        assert (
            orchestrator.publish(
                _input(
                    workspace,
                    generation_id,
                    None,
                    source_root=PYTHON_HTTP_FIXTURE,
                    languages=frozenset({"java", "python", "yaml"}),
                    revisions=PYTHON_HTTP_REVISIONS,
                )
            ).status
            is WorkspacePublishStatus.ACTIVE
        )
        facts = {
            repo_id: PythonHttpMethodDetector().detect_methods(
                RepositorySnapshot(repo_id, revision, PYTHON_HTTP_FIXTURE / repo_id, frozenset({"python"})),
                MethodDetectionContext(repo_id, repo_id, repo_id, revision, generation_id),
            )
            for repo_id, revision in PYTHON_HTTP_REVISIONS.items()
        }
        plan = MethodGraphWritePlan(
            MethodGraphScope(
                namespace,
                WorkspaceGeneration(
                    workspace.workspace_id,
                    generation_id,
                    tuple(
                        WorkspaceRepositorySnapshot(
                            workspace.workspace_id,
                            repo_id,
                            "main",
                            revision,
                            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
                        )
                        for repo_id, revision in PYTHON_HTTP_REVISIONS.items()
                    ),
                ),
            ),
            tuple(facts.values()),
        )
        expected = sorted(
            (call.caller_implementation_id, call.id, plan.operation_id_for(call.target_reference))
            for call in facts["consumer-client"].consumer_calls
        )
        with driver.session() as session:
            actual = [
                (row["caller"], row["call"], row["operation"])
                for row in session.run(
                    "MATCH (caller:ImplementationMethod {namespace: $namespace})-[:CALLER_METHOD]->"
                    "(call:ConsumerMethodCall)-[:CALLS_OPERATION]->"
                    "(operation:ServiceOperation {namespace: $namespace}) "
                    "WHERE caller.id IN $caller_ids "
                    "RETURN caller.id AS caller, call.id AS call, operation.id AS operation "
                    "ORDER BY caller, call, operation",
                    namespace=namespace,
                    caller_ids=[item[0] for item in expected],
                )
            ]
        assert actual == expected
    finally:
        with driver.session() as session:
            session.run("MATCH (n {namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run("MATCH (n {_ontoagent_namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace.workspace_id,
            )
        driver.close()


def test_workspace_publisher_links_exact_ordered_grpc_method_triples() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-grpc-methods-{uuid4()}", "gRPC method graph integration")
    generation_id = f"generation-grpc-methods-{uuid4()}"
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation_id)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    try:
        assert (
            orchestrator.publish(_input(workspace, generation_id, None, source_root=GRPC_FIXTURE)).status
            is WorkspacePublishStatus.ACTIVE
        )
        facts = {
            repo_id: GrpcMethodDetector().detect_methods(
                RepositorySnapshot(repo_id, revision, GRPC_FIXTURE / repo_id, frozenset({"java", "yaml"})),
                MethodDetectionContext(repo_id, repo_id, repo_id, revision, generation_id),
            )
            for repo_id, revision in REVISIONS.items()
        }
        plan = MethodGraphWritePlan(
            MethodGraphScope(
                namespace,
                WorkspaceGeneration(
                    workspace.workspace_id,
                    generation_id,
                    tuple(
                        WorkspaceRepositorySnapshot(
                            workspace.workspace_id,
                            repo_id,
                            "main",
                            revision,
                            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
                        )
                        for repo_id, revision in REVISIONS.items()
                    ),
                ),
            ),
            tuple(facts.values()),
        )
        expected = sorted(
            (
                call.caller_implementation_id,
                call.id,
                plan.operation_id_for(call.target_reference),
            )
            for call in facts["consumer-checkout"].consumer_calls
        )
        with driver.session() as session:
            actual = [
                (row["caller"], row["call"], row["operation"])
                for row in session.run(
                    "MATCH (caller:ImplementationMethod {namespace: $namespace})-[:CALLER_METHOD]->"
                    "(call:ConsumerMethodCall)-[:CALLS_OPERATION]->"
                    "(operation:ServiceOperation {namespace: $namespace}) "
                    "WHERE caller.id IN $caller_ids "
                    "RETURN caller.id AS caller, call.id AS call, operation.id AS operation "
                    "ORDER BY caller, call, operation",
                    namespace=namespace,
                    caller_ids=[item[0] for item in expected],
                )
            ]
        assert actual == expected
    finally:
        with driver.session() as session:
            session.run("MATCH (n {namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run("MATCH (n {_ontoagent_namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace.workspace_id,
            )
        driver.close()


def test_workspace_publisher_links_configured_messaging_methods_to_exact_listener_operations() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-messaging-methods-{uuid4()}", "Messaging method graph integration")
    generation_id = f"generation-messaging-methods-{uuid4()}"
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation_id)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    orchestrator = WorkspaceServiceGraphPublishOrchestrator(
        Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
    )
    try:
        assert (
            orchestrator.publish(
                _input(workspace, generation_id, None, source_root=CONFIGURED_MESSAGING_FIXTURE)
            ).status
            is WorkspacePublishStatus.ACTIVE
        )
        facts = {
            repo_id: MessagingMethodDetector().detect_methods(
                RepositorySnapshot(
                    repo_id, revision, CONFIGURED_MESSAGING_FIXTURE / repo_id, frozenset({"java", "yaml"})
                ),
                MethodDetectionContext(repo_id, repo_id, repo_id, revision, generation_id),
            )
            for repo_id, revision in REVISIONS.items()
        }
        provider = facts["provider-orders"]
        expected = sorted(
            {
                (
                    next(item.id for item in provider.implementations if item.method_name == "publish"),
                    call.id,
                    MethodGraphWritePlan(
                        MethodGraphScope(
                            namespace,
                            WorkspaceGeneration(
                                workspace.workspace_id,
                                generation_id,
                                tuple(
                                    WorkspaceRepositorySnapshot(
                                        workspace.workspace_id,
                                        repo_id,
                                        "main",
                                        revision,
                                        WorkspaceSourceDescriptor(
                                            WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"
                                        ),
                                    )
                                    for repo_id, revision in REVISIONS.items()
                                ),
                            ),
                        ),
                        tuple(facts.values()),
                    ).operation_id_for(call.target_reference),
                )
                for call in provider.consumer_calls
            }
        )
        with driver.session() as session:
            actual = [
                (row["caller"], row["call"], row["operation"])
                for row in session.run(
                    "MATCH (caller:ImplementationMethod {namespace: $namespace, repoId: 'provider-orders'}) "
                    "-[:CALLER_METHOD]->(call:ConsumerMethodCall)-[:CALLS_OPERATION]->"
                    "(operation:ServiceOperation {namespace: $namespace, repoId: 'consumer-checkout'}) "
                    "RETURN caller.id AS caller, call.id AS call, operation.id AS operation "
                    "ORDER BY caller, call, operation",
                    namespace=namespace,
                )
            ]
        assert actual == expected
    finally:
        with driver.session() as session:
            session.run("MATCH (n {namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run("MATCH (n {_ontoagent_namespace: $namespace}) DETACH DELETE n", namespace=namespace)
            session.run(
                "MATCH (n) WHERE n.workspaceId = $workspace_id "
                "AND (n:OntoAgentWorkspace OR n:OntoAgentWorkspaceBuildTask "
                "OR n:OntoAgentWorkspaceGeneration OR n:OntoAgentWorkspaceRepositorySnapshot "
                "OR n:OntoAgentWorkspaceActiveBinding) DETACH DELETE n",
                workspace_id=workspace.workspace_id,
            )
        driver.close()


def test_configured_messaging_fixture_builds_endpoint_plan_for_all_frozen_snapshots() -> None:
    workspace = Workspace("workspace-configured-messaging-plan", "Configured messaging endpoint plan")
    generation_id = "generation-configured-messaging-plan"
    request = _input(workspace, generation_id, None, source_root=CONFIGURED_MESSAGING_FIXTURE)
    frozen_by_repo = {snapshot.repo_id: snapshot for snapshot in request.snapshots}
    batches = tuple(
        FactBatch(
            snapshot.repo_id,
            snapshot.source_revision,
            generation_id,
            frozen_by_repo[snapshot.repo_id].branch,
            (MessagingDetector().detect(snapshot),),
        )
        for snapshot in request.repository_snapshots
    )

    plan = GraphPlanBuilder().build(ServiceGraphResolver().resolve(batches))

    assert {node.props.get("repo_id") for node in plan.nodes} >= {
        "provider-orders",
        "consumer-checkout",
        "isolated-catalog",
    }


def test_remote_failures_preserve_exact_prior_active_workspace_binding() -> None:
    uri, user, password = _credentials()
    workspace = Workspace(f"workspace-preservation-{uuid4()}", "Workspace graph integration")
    active_generation = f"generation-active-{uuid4()}"
    detector_generation = f"generation-detector-failure-{uuid4()}"
    readback_generation = f"generation-readback-failure-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    namespaces = tuple(
        WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace.workspace_id, generation)
        for generation in (active_generation, detector_generation, readback_generation)
    )
    try:
        active = WorkspaceServiceGraphPublishOrchestrator(
            Neo4jWorkspaceServiceGraphPublishComponentFactory(
                driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
            )
        ).publish(_input(workspace, active_generation, None))
        assert active.status is WorkspacePublishStatus.ACTIVE

        repository = Neo4jWorkspaceRepository(driver)
        assert repository.get_active_binding(workspace.workspace_id).generation_id == active_generation  # type: ignore[union-attr]

        detector_failure = WorkspaceServiceGraphPublishOrchestrator(
            _InjectedFactory(driver, _FailingDetectorRegistry())
        ).publish(_input(workspace, detector_generation, active_generation))
        assert detector_failure.status is WorkspacePublishStatus.FAILED
        assert (
            repository.get_generation(workspace.workspace_id, detector_generation).state
            is WorkspaceGenerationState.FAILED
        )  # type: ignore[union-attr]
        assert repository.get_active_binding(workspace.workspace_id).generation_id == active_generation  # type: ignore[union-attr]

        unconfirmed = WorkspaceServiceGraphPublishOrchestrator(
            _InjectedFactory(
                driver,
                DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()]),
                unconfirmed=True,
            )
        ).publish(_input(workspace, readback_generation, active_generation))
        assert unconfirmed.status is WorkspacePublishStatus.FAILED
        assert (
            repository.get_generation(workspace.workspace_id, readback_generation).state
            is WorkspaceGenerationState.FAILED
        )  # type: ignore[union-attr]
        assert repository.get_active_binding(workspace.workspace_id).generation_id == active_generation  # type: ignore[union-attr]
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
