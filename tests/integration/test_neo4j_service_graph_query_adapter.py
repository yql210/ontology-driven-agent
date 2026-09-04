from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.generation_manifest import (
    ManifestResolutionStatus,
    Neo4jNamespace,
    ServiceGraphManifest,
)
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder
from ontoagent.parsing.service_graph.graph_writer import GraphWriter
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.neo4j_graph_sink import Neo4jGraphSink
from ontoagent.parsing.service_graph.neo4j_manifest_repository import (
    ManifestPublicationStatus,
    Neo4jServiceGraphManifestRepository,
)
from ontoagent.parsing.service_graph.neo4j_query_adapter import Neo4jServiceGraphQueryAdapter
from ontoagent.parsing.service_graph.query import ServiceGraphQueryBlockReason, ServiceGraphQueryStatus
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

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
        pytest.skip("ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


def _plan(generation_id: str):
    batches = []
    for repo_id, revision in REVISIONS.items():
        snapshot = RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
        facts = tuple(
            detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
        )
        batches.append(FactBatch(repo_id, revision, generation_id, "main", facts))
    return GraphPlanBuilder().build(ServiceGraphResolver().resolve(tuple(batches)))


def test_durable_active_adapter_queries_neutral_three_repo_service_graph() -> None:
    uri, user, password = _credentials()
    namespace = Neo4jNamespace(f"service-query-{uuid4()}")
    generation_id = f"generation-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jServiceGraphManifestRepository(driver)
    repo_ids = tuple(REVISIONS)
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
        adapter = Neo4jServiceGraphQueryAdapter(driver, repository, namespace)
        endpoints = [node for node in plan.nodes if node.node_type == "Endpoint"]
        protocols = {str(node.props["protocol"]) for node in endpoints}
        assert {"HTTP", "DUBBO", "MQ"} <= protocols
        for protocol in ("HTTP", "DUBBO", "MQ"):
            provider = next(
                node for node in endpoints if node.props["protocol"] == protocol and node.props["role"] == "provider"
            )
            consumer = next(
                node for node in endpoints if node.props["protocol"] == protocol and node.props["role"] == "consumer"
            )
            providers = adapter.find_endpoint_providers(
                provider.props["repo_id"], generation_id, provider.props["canonical_key"]
            )
            consumers = adapter.find_endpoint_consumers(
                consumer.props["repo_id"], generation_id, consumer.props["canonical_key"]
            )
            assert providers.status is ServiceGraphQueryStatus.READY
            assert consumers.status is ServiceGraphQueryStatus.READY
            assert provider.id in {node.id for node in providers.nodes}
            assert consumer.id in {node.id for node in consumers.nodes}
        dependency = next(relation for relation in plan.relations if relation.relation_type == "DEPENDS_ON")
        consumer_id = dependency.source_id
        consumer_repo_id = next(node.props["repo_id"] for node in endpoints if node.id == consumer_id)
        dependencies = adapter.find_service_dependencies(consumer_repo_id, generation_id, consumer_id)
        evidence = adapter.get_evidence(consumer_repo_id, generation_id, dependency.id)
        assert dependencies.status is ServiceGraphQueryStatus.READY
        assert dependency.id in {relation.id for relation in dependencies.relations}
        assert evidence.status is ServiceGraphQueryStatus.READY
        assert set(dependency.props["evidence_ids"]) <= {node.id for node in evidence.nodes}
        wrong_generation = adapter.service_directory("provider-orders", "wrong-generation")
        inactive = adapter.service_directory("inactive-repo", generation_id)
        assert wrong_generation.status is ServiceGraphQueryStatus.BLOCKED
        assert wrong_generation.reasons == (ServiceGraphQueryBlockReason.GENERATION_MISMATCH,)
        assert inactive.status is ServiceGraphQueryStatus.BLOCKED
        assert inactive.reasons == (ServiceGraphQueryBlockReason.MISSING_ACTIVE,)
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (n) WHERE n._ontoagent_namespace = $namespace DETACH DELETE n", namespace=namespace.value
            )
            session.run(
                "MATCH (n) WHERE (n:OntoAgentServiceGraphManifest OR n:OntoAgentServiceGraphActive) "
                "AND n.namespace = $namespace AND n.repoId IN $repo_ids DETACH DELETE n",
                namespace=namespace.value,
                repo_ids=list(repo_ids),
            )
        driver.close()
