from __future__ import annotations

import os
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.generation_manifest import (
    ManifestResolutionStatus,
    Neo4jNamespace,
    ServiceGraphManifest,
)
from ontoagent.parsing.service_graph.graph_plan import GraphNode, GraphRelation, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import WriteReceipt
from ontoagent.parsing.service_graph.neo4j_manifest_repository import (
    ManifestPublicationStatus,
    Neo4jServiceGraphManifestRepository,
)

pytestmark = pytest.mark.integration


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not uri or not user or not password:
        pytest.skip("ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


def _plan(repo_id: str, generation_id: str, source_revision: str) -> GraphWritePlan:
    node = GraphNode(
        "endpoint-1",
        "Endpoint",
        {
            "id": "endpoint-1",
            "repo_id": repo_id,
            "generation_id": generation_id,
            "source_revision": source_revision,
            "canonical_key": "endpoint-1",
            "evidence_ids": ("evidence-1",),
        },
    )
    relation = GraphRelation(
        "relation-1",
        "PROVIDES_ENDPOINT",
        node.id,
        node.id,
        {
            "generation_id": generation_id,
            "source_revision": source_revision,
            "canonical_key": "relation-1",
            "evidence_ids": ("evidence-1",),
        },
    )
    return GraphWritePlan((node,), (relation,))


def test_manifest_persistence_and_active_compare_and_set_against_neo4j() -> None:
    uri, user, password = _credentials()
    namespace = Neo4jNamespace(f"integration-manifest-{uuid4()}")
    repo_id = f"repo-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    repository = Neo4jServiceGraphManifestRepository(driver)
    first = ServiceGraphManifest(repo_id, "generation-1", "revision-1", namespace)
    second = ServiceGraphManifest(repo_id, "generation-2", "revision-2", namespace)
    third = ServiceGraphManifest(repo_id, "generation-3", "revision-3", namespace)
    try:
        first_plan = _plan(repo_id, first.generation_id, first.source_revision)
        second_plan = _plan(repo_id, second.generation_id, second.source_revision)
        assert (
            repository.persist_verified(first, first_plan, WriteReceipt(True, 1, 1, first_plan, namespace.value)).status
            is ManifestResolutionStatus.READY
        )
        assert (
            repository.persist_verified(
                second, second_plan, WriteReceipt(True, 1, 1, second_plan, namespace.value)
            ).status
            is ManifestResolutionStatus.READY
        )
        repository.persist_building(third)
        assert repository.get(repo_id, namespace, first.generation_id) is not None
        assert (
            repository.publish_active(repo_id, namespace, "nonexistent-active", first.generation_id).status
            is ManifestPublicationStatus.REJECTED_STALE_ACTIVE
        )
        assert (
            repository.publish_active(repo_id, namespace, None, first.generation_id).status
            is ManifestPublicationStatus.PUBLISHED
        )
        assert (
            repository.publish_active(repo_id, namespace, None, second.generation_id).status
            is ManifestPublicationStatus.REJECTED_STALE_ACTIVE
        )
        assert (
            repository.publish_active(repo_id, namespace, first.generation_id, third.generation_id).status
            is ManifestPublicationStatus.REJECTED_CANDIDATE_NOT_READY
        )
        assert (
            repository.publish_active(repo_id, namespace, first.generation_id, second.generation_id).status
            is ManifestPublicationStatus.PUBLISHED
        )
        assert repository.resolve(repo_id, second.generation_id, namespace).status is ManifestResolutionStatus.READY
        assert repository.resolve(repo_id, first.generation_id, namespace).status is ManifestResolutionStatus.BLOCKED
    finally:
        with driver.session() as session:
            session.run(
                "MATCH (n) WHERE (n:OntoAgentServiceGraphManifest OR n:OntoAgentServiceGraphActive) "
                "AND n.namespace = $namespace AND n.repoId = $repo_id DETACH DELETE n",
                namespace=namespace.value,
                repo_id=repo_id,
            )
        driver.close()
