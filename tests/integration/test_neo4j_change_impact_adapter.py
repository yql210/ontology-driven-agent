from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.change_analysis import (
    ServiceGraphChangeAnalysisBlockReason,
    ServiceGraphChangeAnalysisStatus,
)
from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.generation_manifest import (
    ManifestResolutionStatus,
    Neo4jNamespace,
    ServiceGraphManifest,
)
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import GraphWriter, WriteReceipt
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.neo4j_change_impact_adapter import Neo4jServiceGraphChangeImpactAdapter
from ontoagent.parsing.service_graph.neo4j_graph_sink import Neo4jGraphSink
from ontoagent.parsing.service_graph.neo4j_manifest_repository import Neo4jServiceGraphManifestRepository
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


def _plan(generation_id: str, revisions: dict[str, str]) -> GraphWritePlan:
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


def _persist_generation(
    repository: Neo4jServiceGraphManifestRepository,
    namespace: Neo4jNamespace,
    generation_id: str,
    revisions: dict[str, str],
    plan: GraphWritePlan,
    receipt: WriteReceipt,
) -> None:
    for repo_id, revision in revisions.items():
        manifest = ServiceGraphManifest(repo_id, generation_id, revision, namespace)
        assert repository.persist_verified(manifest, plan, receipt).status is ManifestResolutionStatus.READY


def test_historical_generations_preserve_deleted_provider_consumer_impact() -> None:
    uri, user, password = _credentials()
    namespace = Neo4jNamespace(f"service-change-impact-{uuid4()}")
    generation_1 = f"generation-1-{uuid4()}"
    generation_2 = f"generation-2-{uuid4()}"
    revisions_2 = {
        "provider-orders": "fixture-provider-v2",
        "consumer-checkout": "fixture-consumer-v2",
        "isolated-catalog": "fixture-isolated-v2",
    }
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jServiceGraphManifestRepository(driver)
    try:
        from_plan = _plan(generation_1, REVISIONS)
        dependency = next(relation for relation in from_plan.relations if relation.relation_type == "DEPENDS_ON")
        provider = next(node for node in from_plan.nodes if node.id == dependency.target_id)
        to_plan = _plan(generation_2, revisions_2)
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
        _persist_generation(repository, namespace, generation_1, REVISIONS, from_plan, from_receipt)
        _persist_generation(repository, namespace, generation_2, revisions_2, to_plan, to_receipt)

        result = Neo4jServiceGraphChangeImpactAdapter(driver, repository, namespace).analyze(
            "provider-orders", generation_1, generation_2
        )

        assert result.status is ServiceGraphChangeAnalysisStatus.READY
        assert [item.contract.canonical_key for item in result.endpoint_deletions] == [provider.props["canonical_key"]]
        assert [(item.changed_endpoint.repo_id, item.impacted_endpoint.repo_id) for item in result.direct_impacts] == [
            ("provider-orders", "consumer-checkout")
        ]
        wrong = Neo4jServiceGraphChangeImpactAdapter(driver, repository, namespace).analyze(
            "provider-orders", generation_1, "wrong-generation"
        )
        assert wrong.status is ServiceGraphChangeAnalysisStatus.BLOCKED
        assert wrong.reasons == (ServiceGraphChangeAnalysisBlockReason.MISSING_DURABLE_MANIFEST,)
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
