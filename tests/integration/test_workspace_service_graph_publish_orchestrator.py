"""Remote Neo4j coverage for workspace-scoped service graph publication."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder
from ontoagent.parsing.service_graph.graph_writer import GraphWriter, WriteReceipt
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
from ontoagent.parsing.service_graph.resolver import ServiceGraphResolver
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
    WorkspaceServiceGraphPublishComponents,
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
) -> WorkspaceServiceGraphPublishInput:
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
        first = orchestrator.publish(_input(workspace, generation_one, None, _method_facts(generation_one)))
        assert first.status is WorkspacePublishStatus.ACTIVE
        assert first.candidate_namespace == namespaces[0]

        repository = Neo4jWorkspaceRepository(driver)
        assert repository.get_active_binding(workspace.workspace_id).generation_id == generation_one  # type: ignore[union-attr]
        with driver.session() as session:
            count = session.run(
                "MATCH (n { _ontoagent_namespace: $namespace }) RETURN count(n) AS count", namespace=namespaces[0]
            ).single()["count"]
        assert count > 0
        with driver.session() as session:
            method_count = session.run(
                "MATCH (n:ServiceOperation {namespace: $namespace, workspaceId: $workspace_id, "
                "generationId: $generation_id}) RETURN count(n) AS count",
                namespace=namespaces[0],
                workspace_id=workspace.workspace_id,
                generation_id=generation_one,
            ).single()["count"]
        # The three explicit generic facts remain alongside four Spring provider operations.
        assert method_count == 7

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
        assert links == [
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
