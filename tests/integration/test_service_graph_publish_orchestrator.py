"""Remote Neo4j coverage for service graph build and all-repository ACTIVE publication."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.generation_manifest import ManifestResolutionStatus, Neo4jNamespace
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.neo4j_manifest_repository import Neo4jServiceGraphManifestRepository
from ontoagent.parsing.service_graph.neo4j_query_adapter import Neo4jServiceGraphQueryAdapter
from ontoagent.parsing.service_graph.publish_orchestrator import (
    Neo4jServiceGraphPublishComponentFactory,
    ServiceGraphPublishInput,
    ServiceGraphPublishOrchestrator,
    ServiceGraphPublishStatus,
)

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
        pytest.skip("explicit ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


def test_publish_orchestrator_activates_each_neutral_repository_in_remote_neo4j() -> None:
    uri, user, password = _credentials()
    namespace = Neo4jNamespace(f"service-graph-publish-{uuid4()}")
    generations = {repo_id: f"generation-{repo_id}-{uuid4()}" for repo_id in REVISIONS}
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jServiceGraphManifestRepository(driver)
    registry = DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
    orchestrator = ServiceGraphPublishOrchestrator(Neo4jServiceGraphPublishComponentFactory(driver, registry))
    inputs = tuple(
        ServiceGraphPublishInput(
            RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"})),
            generations[repo_id],
            "main",
            None,
        )
        for repo_id, revision in REVISIONS.items()
    )
    try:
        outcome = orchestrator.publish(namespace, inputs)

        assert outcome.status is ServiceGraphPublishStatus.ACTIVE
        assert outcome.graph_write_confirmed
        assert all(receipt.active_published for receipt in outcome.publication_receipts)
        adapter = Neo4jServiceGraphQueryAdapter(driver, repository, namespace)
        for repo_id, generation_id in generations.items():
            assert repository.resolve(repo_id, generation_id, namespace).status is ManifestResolutionStatus.READY
            assert adapter.service_directory(repo_id, generation_id).status.value == "ready"
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
