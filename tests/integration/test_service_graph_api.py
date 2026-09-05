"""Remote Neo4j integration coverage for durable service graph Web API queries."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from neo4j import GraphDatabase

from ontoagent.api.web.app import create_app
from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.generation_manifest import (
    ManifestResolutionStatus,
    Neo4jNamespace,
    ServiceGraphManifest,
)
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import GraphWriter
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.neo4j_graph_sink import Neo4jGraphSink
from ontoagent.parsing.service_graph.neo4j_manifest_repository import (
    ManifestPublicationStatus,
    Neo4jServiceGraphManifestRepository,
)
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

pytestmark = pytest.mark.integration

FIXTURE = Path(__file__).parents[1] / "fixtures/service_graph/neutral_three_repo"
REVISIONS = {"provider-orders": "fixture-provider-v1", "consumer-checkout": "fixture-consumer-v1"}
REVISIONS_2 = {"provider-orders": "fixture-provider-v2", "consumer-checkout": "fixture-consumer-v2"}


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("explicit ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


def _plan(generation_id: str, revisions: dict[str, str] = REVISIONS) -> GraphWritePlan:
    batches = []
    for repo_id, revision in revisions.items():
        snapshot = RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
        facts = tuple(
            detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
        )
        batches.append(FactBatch(repo_id, revision, generation_id, "main", facts))
    return GraphPlanBuilder().build(ServiceGraphResolver().resolve(tuple(batches)))


def _without_provider(plan: GraphWritePlan, provider_id: str) -> GraphWritePlan:
    return GraphWritePlan(
        tuple(node for node in plan.nodes if node.id != provider_id),
        tuple(relation for relation in plan.relations if provider_id not in {relation.source_id, relation.target_id}),
    )


def test_service_graph_api_reads_remote_active_generation() -> None:
    """The Web API reads only a real, persisted, ACTIVE generation in a unique namespace."""
    uri, user, password = _credentials()
    namespace = Neo4jNamespace(f"service-graph-api-{uuid4()}")
    generation_id = f"generation-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jServiceGraphManifestRepository(driver)
    try:
        plan = _plan(generation_id)
        receipt = GraphWriter(Neo4jGraphSink(driver, namespace=namespace.value)).write(plan)
        assert receipt.confirmed
        for repo_id, revision in REVISIONS.items():
            manifest = ServiceGraphManifest(repo_id, generation_id, revision, namespace)
            assert repository.persist_verified(manifest, plan, receipt).status is ManifestResolutionStatus.READY
            assert (
                repository.publish_active(repo_id, namespace, None, generation_id).status
                is ManifestPublicationStatus.PUBLISHED
            )

        endpoint = next(
            node for node in plan.nodes if node.node_type == "Endpoint" and node.props["role"] == "provider"
        )
        dependency = next(relation for relation in plan.relations if relation.relation_type == "DEPENDS_ON")
        consumer_id = dependency.source_id
        consumer_repo_id = next(node.props["repo_id"] for node in plan.nodes if node.id == consumer_id)
        base = {"repo_id": "provider-orders", "generation_id": generation_id, "namespace": namespace.value}
        client = TestClient(create_app())

        assert client.get("/api/service-graph/directory", params=base).status_code == 200
        assert (
            client.get(
                "/api/service-graph/providers", params=base | {"endpoint_key": endpoint.props["canonical_key"]}
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/service-graph/consumers", params=base | {"endpoint_key": endpoint.props["canonical_key"]}
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/service-graph/dependencies",
                params={
                    "repo_id": consumer_repo_id,
                    "generation_id": generation_id,
                    "namespace": namespace.value,
                    "service_id": consumer_id,
                },
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/service-graph/evidence",
                params={
                    "repo_id": consumer_repo_id,
                    "generation_id": generation_id,
                    "namespace": namespace.value,
                    "entity_or_relation_id": dependency.id,
                },
            ).status_code
            == 200
        )
        wrong_generation = client.get(
            "/api/service-graph/directory", params=base | {"generation_id": "wrong-generation"}
        )
        assert wrong_generation.status_code == 409
        assert wrong_generation.json()["reasons"] == ["generation_mismatch"]
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (n) WHERE n._ontoagent_namespace = $namespace DETACH DELETE n", namespace=namespace.value
            )
            session.run(
                "MATCH (n) WHERE (n:OntoAgentServiceGraphManifest OR n:OntoAgentServiceGraphActive) "
                "AND n.namespace = $namespace AND n.repoId IN $repo_ids DETACH DELETE n",
                namespace=namespace.value,
                repo_ids=list(REVISIONS),
            )
        driver.close()


def test_service_graph_changes_api_reads_remote_historical_generations() -> None:
    """The Web API analyzes durable history and preserves cross-repository consumer impact."""
    uri, user, password = _credentials()
    namespace = Neo4jNamespace(f"service-graph-changes-api-{uuid4()}")
    generation_1 = f"generation-1-{uuid4()}"
    generation_2 = f"generation-2-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jServiceGraphManifestRepository(driver)
    try:
        from_plan = _plan(generation_1)
        dependency = next(relation for relation in from_plan.relations if relation.relation_type == "DEPENDS_ON")
        provider = next(node for node in from_plan.nodes if node.id == dependency.target_id)
        to_plan = _plan(generation_2, REVISIONS_2)
        replacement = next(
            node
            for node in to_plan.nodes
            if node.node_type == "Endpoint"
            and node.props["repo_id"] == "provider-orders"
            and node.props["protocol"] == provider.props["protocol"]
            and node.props["canonical_key"] == provider.props["canonical_key"]
        )
        to_plan = _without_provider(to_plan, replacement.id)
        from_receipt = GraphWriter(Neo4jGraphSink(driver, namespace=namespace.value)).write(from_plan)
        to_receipt = GraphWriter(Neo4jGraphSink(driver, namespace=namespace.value)).write(to_plan)
        assert from_receipt.confirmed and to_receipt.confirmed
        for generation_id, revisions, plan, receipt in (
            (generation_1, REVISIONS, from_plan, from_receipt),
            (generation_2, REVISIONS_2, to_plan, to_receipt),
        ):
            for repo_id, revision in revisions.items():
                manifest = ServiceGraphManifest(repo_id, generation_id, revision, namespace)
                assert repository.persist_verified(manifest, plan, receipt).status is ManifestResolutionStatus.READY

        response = TestClient(create_app()).get(
            "/api/service-graph/changes",
            params={
                "repo_id": "provider-orders",
                "from_generation": generation_1,
                "to_generation": generation_2,
                "namespace": namespace.value,
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert [item["contract"]["canonical_key"] for item in result["endpoint_deletions"]] == [
            provider.props["canonical_key"]
        ]
        assert [
            (item["changed_endpoint"]["repo_id"], item["impacted_endpoint"]["repo_id"])
            for item in result["direct_impacts"]
        ] == [("provider-orders", "consumer-checkout")]
        wrong_generation = TestClient(create_app()).get(
            "/api/service-graph/changes",
            params={
                "repo_id": "provider-orders",
                "from_generation": generation_1,
                "to_generation": "wrong-generation",
                "namespace": namespace.value,
            },
        )
        assert wrong_generation.status_code == 409
        assert wrong_generation.json()["reasons"] == ["missing_durable_manifest"]
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (n) WHERE n._ontoagent_namespace = $namespace DETACH DELETE n", namespace=namespace.value
            )
            session.run(
                "MATCH (n) WHERE (n:OntoAgentServiceGraphManifest OR n:OntoAgentServiceGraphActive) "
                "AND n.namespace = $namespace DETACH DELETE n",
                namespace=namespace.value,
            )
        driver.close()
