from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import WriteReceipt
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.query import (
    ServiceGraphQuery,
    ServiceGraphQueryBlockReason,
    ServiceGraphQueryStatus,
)
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


def _plan() -> GraphWritePlan:
    batches = []
    for repo_id, revision in (("provider-orders", "p1"), ("consumer-checkout", "c1"), ("isolated-catalog", "i1")):
        snapshot = RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
        facts = tuple(
            detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
        )
        batches.append(FactBatch(repo_id, revision, "generation-1", "main", facts))
    return GraphPlanBuilder().build(ServiceGraphResolver().resolve(tuple(batches)))


def _query(plan: GraphWritePlan | None = None, receipt: WriteReceipt | None = None) -> ServiceGraphQuery:
    plan = plan or _plan()
    receipt = receipt or WriteReceipt(True, len(plan.nodes), len(plan.relations), plan, "service-graph")
    return ServiceGraphQuery(plan, receipt)


def _endpoint(plan: GraphWritePlan, repo_id: str, role: str) -> object:
    return next(
        node
        for node in plan.nodes
        if node.node_type == "Endpoint" and node.props["repo_id"] == repo_id and node.props["role"] == role
    )


def test_service_directory_is_generation_bound_immutable_and_json_safe() -> None:
    plan = _plan()

    result = _query(plan).service_directory(" provider-orders ", " generation-1 ")

    assert result.status is ServiceGraphQueryStatus.READY
    assert result.repo_id == "provider-orders"
    assert result.generation_id == "generation-1"
    assert result.reasons == ()
    assert result.nodes
    assert {node.node_type for node in result.nodes} == {"ServiceDefinition"}
    assert all(node.properties["repo_id"] == "provider-orders" for node in result.nodes)
    assert json.loads(json.dumps(result.to_dict())) == result.to_dict()


def test_provider_and_consumer_queries_keep_cross_repo_dependency_context() -> None:
    plan = _plan()
    provider = _endpoint(plan, "provider-orders", "provider")
    consumer = _endpoint(plan, "consumer-checkout", "consumer")
    query = _query(plan)

    providers = query.find_endpoint_providers("consumer-checkout", "generation-1", provider.props["canonical_key"])
    consumers = query.find_endpoint_consumers("provider-orders", "generation-1", consumer.props["canonical_key"])

    assert providers.status is ServiceGraphQueryStatus.READY
    assert {node.properties["repo_id"] for node in providers.nodes} == {"provider-orders"}
    assert consumers.status is ServiceGraphQueryStatus.READY
    assert {node.properties["repo_id"] for node in consumers.nodes} == {"consumer-checkout"}


def test_service_dependencies_return_cross_repo_depends_on_and_endpoints() -> None:
    plan = _plan()
    dependency = next(relation for relation in plan.relations if relation.relation_type == "DEPENDS_ON")
    consumer = next(node for node in plan.nodes if node.id == dependency.source_id)
    service_link = next(
        relation
        for relation in plan.relations
        if relation.relation_type == "CONSUMES_ENDPOINT" and relation.target_id == consumer.id
    )
    service = next(node for node in plan.nodes if node.id == service_link.source_id)
    query = _query(plan)

    result = query.find_service_dependencies("consumer-checkout", "generation-1", service.id)

    assert result.status is ServiceGraphQueryStatus.READY
    assert {relation.relation_type for relation in result.relations} == {"DEPENDS_ON"}
    assert {node.properties["repo_id"] for node in result.nodes} == {"provider-orders", "consumer-checkout"}


def test_endpoint_query_returns_verified_empty_for_absent_key() -> None:
    result = _query().find_endpoint_providers("consumer-checkout", "generation-1", "DUBBO|-|missing.Api|get|1")

    assert result.status is ServiceGraphQueryStatus.READY
    assert result.nodes == ()
    assert result.relations == ()
    assert result.reasons == ()


def test_missing_service_or_evidence_is_blocked_not_a_verified_empty_result() -> None:
    query = _query()

    dependency = query.find_service_dependencies("consumer-checkout", "generation-1", "missing")
    evidence = query.get_evidence("consumer-checkout", "generation-1", "missing")

    assert dependency.status is ServiceGraphQueryStatus.BLOCKED
    assert dependency.reasons == (ServiceGraphQueryBlockReason.NODE_NOT_FOUND,)
    assert evidence.status is ServiceGraphQueryStatus.BLOCKED
    assert evidence.reasons == (ServiceGraphQueryBlockReason.ENTITY_OR_RELATION_NOT_FOUND,)


def test_get_evidence_returns_referenced_evidence_for_entity_and_relation() -> None:
    plan = _plan()
    relation = next(relation for relation in plan.relations if relation.relation_type == "DEPENDS_ON")
    endpoint = next(node for node in plan.nodes if node.id == relation.source_id)
    query = _query(plan)

    entity_evidence = query.get_evidence("consumer-checkout", "generation-1", endpoint.id)
    relation_evidence = query.get_evidence("consumer-checkout", "generation-1", relation.id)

    assert entity_evidence.status is ServiceGraphQueryStatus.READY
    assert relation_evidence.status is ServiceGraphQueryStatus.READY
    assert {node.node_type for node in entity_evidence.nodes} == {"Evidence"}
    assert {node.id for node in relation_evidence.nodes} == set(relation.props["evidence_ids"])


def test_query_blocks_bad_request_identity_and_repo_generation_mismatch() -> None:
    query = _query()

    malformed = query.service_directory(" ", "generation-1")
    repo_mismatch = query.service_directory("missing", "generation-1")
    generation_mismatch = query.service_directory("consumer-checkout", "other")

    assert malformed.reasons == (ServiceGraphQueryBlockReason.MALFORMED_REQUEST,)
    assert repo_mismatch.reasons == (ServiceGraphQueryBlockReason.REPO_MISMATCH,)
    assert generation_mismatch.reasons == (ServiceGraphQueryBlockReason.GENERATION_MISMATCH,)


def test_query_blocks_unconfirmed_or_mismatched_readback() -> None:
    plan = _plan()

    unconfirmed = _query(plan, WriteReceipt(False, len(plan.nodes), len(plan.relations), plan, "service-graph"))
    mismatched = _query(
        plan, WriteReceipt(True, len(plan.nodes), len(plan.relations), GraphWritePlan((), ()), "service-graph")
    )

    assert unconfirmed.service_directory("consumer-checkout", "generation-1").reasons == (
        ServiceGraphQueryBlockReason.UNCONFIRMED_READBACK,
    )
    assert mismatched.service_directory("consumer-checkout", "generation-1").reasons == (
        ServiceGraphQueryBlockReason.RECEIPT_READBACK_MISMATCH,
    )


def test_query_blocks_malformed_plan_provenance_and_missing_referenced_evidence() -> None:
    plan = _plan()
    endpoint = _endpoint(plan, "consumer-checkout", "consumer")
    malformed_node = replace(endpoint, props={**endpoint.props, "id": "other"})
    malformed_plan = replace(
        plan, nodes=tuple(malformed_node if node.id == endpoint.id else node for node in plan.nodes)
    )
    missing_evidence = replace(endpoint, props={**endpoint.props, "evidence_ids": ("missing-evidence",)})
    evidence_plan = replace(
        plan, nodes=tuple(missing_evidence if node.id == endpoint.id else node for node in plan.nodes)
    )

    assert _query(malformed_plan).service_directory("consumer-checkout", "generation-1").reasons == (
        ServiceGraphQueryBlockReason.NODE_PROVENANCE_MISMATCH,
    )
    assert _query(evidence_plan).service_directory("consumer-checkout", "generation-1").reasons == (
        ServiceGraphQueryBlockReason.EVIDENCE_NOT_FOUND,
    )
