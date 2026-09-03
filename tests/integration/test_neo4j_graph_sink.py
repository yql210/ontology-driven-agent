from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.graph_plan import GraphNode, GraphPlanBuilder, GraphRelation, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import GraphWriter
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.neo4j_graph_sink import Neo4jGraphSink
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/service_graph/neutral_three_repo"
REVISIONS = {
    "provider-orders": "fixture-provider-v1",
    "consumer-checkout": "fixture-consumer-v1",
    "isolated-catalog": "fixture-isolated-v1",
}


@pytest.fixture
def neo4j_driver():
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    try:
        yield driver
    finally:
        driver.close()


def _node_signature(node: GraphNode) -> tuple[str, str, str, str, str, tuple[str, ...]]:
    props = node.props
    evidence_ids = _evidence_ids(props)
    return (
        node.node_type,
        _string_prop(props, "repo_id"),
        _string_prop(props, "source_revision"),
        _string_prop(props, "protocol"),
        _string_prop(props, "canonical_key"),
        evidence_ids,
    )


def _relation_signature(relation: GraphRelation) -> tuple[str, str, str, str, str, str, tuple[str, ...]]:
    props = relation.props
    evidence_ids = _evidence_ids(props)
    return (
        relation.relation_type,
        relation.source_id,
        relation.target_id,
        _string_prop(props, "source_revision"),
        _string_prop(props, "generation_id"),
        _string_prop(props, "canonical_key"),
        evidence_ids,
    )


def _repo_provenance(nodes: tuple[GraphNode, ...]) -> set[tuple[str, str]]:
    return {(_string_prop(node.props, "repo_id"), _string_prop(node.props, "source_revision")) for node in nodes}


def _string_prop(props: Mapping[str, object], key: str) -> str:
    value = props.get(key)
    assert isinstance(value, str)
    return value


def _evidence_ids(props: Mapping[str, object]) -> tuple[str, ...]:
    evidence_ids = props.get("evidence_ids")
    assert isinstance(evidence_ids, tuple) and all(isinstance(item, str) for item in evidence_ids)
    return evidence_ids


def _assert_no_unexpected_cross_links(plan: GraphWritePlan) -> None:
    nodes = {node.id: node for node in plan.nodes}
    dependencies = [relation for relation in plan.relations if relation.relation_type == "DEPENDS_ON"]
    assert len(dependencies) == 3
    assert {(relation.props["provider_repo_id"], relation.props["consumer_repo_id"]) for relation in dependencies} == {
        ("provider-orders", "consumer-checkout")
    }
    assert all(
        nodes[relation.source_id].props["repo_id"] == "consumer-checkout"
        and nodes[relation.target_id].props["repo_id"] == "provider-orders"
        for relation in dependencies
    )
    assert all(
        "isolated-catalog" not in (relation.props.get("provider_repo_id"), relation.props.get("consumer_repo_id"))
        for relation in dependencies
    )


def test_neo4j_sink_round_trips_neutral_three_repo_plan(neo4j_driver) -> None:
    namespace = f"i3b-{uuid4()}"
    batches = []
    for repo_id, revision in REVISIONS.items():
        snapshot = RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
        facts = tuple(
            detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
        )
        batches.append(FactBatch(repo_id, revision, namespace, "main", facts))
    plan = GraphPlanBuilder().build(ServiceGraphResolver().resolve(tuple(batches)))
    sink = Neo4jGraphSink(neo4j_driver, namespace=namespace)

    try:
        receipt = GraphWriter(sink).write(plan)
        readback = receipt.readback

        assert receipt.confirmed
        assert receipt.node_count == len(plan.nodes)
        assert receipt.relation_count == len(plan.relations)
        assert _repo_provenance(readback.nodes) == set(REVISIONS.items())
        assert {(node.props["repo_id"], node.props["source_revision"]) for node in readback.nodes} == set(
            REVISIONS.items()
        )
        assert {_node_signature(node) for node in readback.nodes} == {_node_signature(node) for node in plan.nodes}
        assert {_relation_signature(relation) for relation in readback.relations} == {
            _relation_signature(relation) for relation in plan.relations
        }
        _assert_no_unexpected_cross_links(readback)

        with neo4j_driver.session() as session:
            stored_relation_count = session.run(
                "MATCH (source)-[r]->(target) WHERE r._ontoagent_namespace = $namespace RETURN count(r) AS count",
                namespace=namespace,
            ).single()["count"]
        assert stored_relation_count == len(readback.relations)
    finally:
        with neo4j_driver.session() as session:
            session.run("MATCH (n) WHERE n._ontoagent_namespace = $namespace DETACH DELETE n", namespace=namespace)
