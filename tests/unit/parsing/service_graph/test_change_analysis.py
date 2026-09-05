from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ontoagent.parsing.service_graph.change_analysis import (
    ServiceGraphChangeAnalysis,
    ServiceGraphChangeAnalysisBlockReason,
    ServiceGraphChangeAnalysisStatus,
)
from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import WriteReceipt
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"
REPO_ID = "provider-orders"


def _plan(generation_id: str, provider_revision: str = "p1") -> GraphWritePlan:
    batches = []
    for repo_id, revision in (
        ("provider-orders", provider_revision),
        ("consumer-checkout", "c1"),
        ("isolated-catalog", "i1"),
    ):
        snapshot = RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
        facts = tuple(
            detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
        )
        batches.append(FactBatch(repo_id, revision, generation_id, "main", facts))
    return GraphPlanBuilder().build(ServiceGraphResolver().resolve(tuple(batches)))


def _receipt(plan: GraphWritePlan, *, confirmed: bool = True) -> WriteReceipt:
    return WriteReceipt(confirmed, len(plan.nodes), len(plan.relations), plan, "service-graph")


def _analyze(
    from_plan: GraphWritePlan,
    to_plan: GraphWritePlan,
    *,
    from_receipt: WriteReceipt | None = None,
    to_receipt: WriteReceipt | None = None,
    repo_id: object = REPO_ID,
    from_generation: object = "generation-1",
    to_generation: object = "generation-2",
):
    return ServiceGraphChangeAnalysis(
        from_plan, from_receipt or _receipt(from_plan), to_plan, to_receipt or _receipt(to_plan)
    ).analyze(repo_id, from_generation, to_generation)


def _without_node(plan: GraphWritePlan, node_id: str) -> GraphWritePlan:
    relations = tuple(
        relation for relation in plan.relations if node_id not in {relation.source_id, relation.target_id}
    )
    return GraphWritePlan(tuple(node for node in plan.nodes if node.id != node_id), relations)


def test_deleted_provider_reports_exact_cross_repo_consuming_endpoint_as_direct_impact() -> None:
    from_plan = _plan("generation-1")
    dependency = next(relation for relation in from_plan.relations if relation.relation_type == "DEPENDS_ON")
    provider = next(node for node in from_plan.nodes if node.id == dependency.target_id)
    unmodified_to_plan = _plan("generation-2")
    to_provider = next(
        node
        for node in unmodified_to_plan.nodes
        if node.node_type == "Endpoint"
        and node.props["repo_id"] == REPO_ID
        and node.props["canonical_key"] == provider.props["canonical_key"]
    )
    to_plan = _without_node(unmodified_to_plan, to_provider.id)

    result = _analyze(from_plan, to_plan)

    assert result.status is ServiceGraphChangeAnalysisStatus.READY
    assert [change.contract.canonical_key for change in result.endpoint_deletions] == [provider.props["canonical_key"]]
    assert len(result.direct_impacts) == 1
    impact = result.direct_impacts[0]
    assert impact.changed_endpoint.repo_id == REPO_ID
    assert impact.impacted_endpoint.repo_id == "consumer-checkout"
    assert impact.impacted_endpoint.role == "consumer"
    assert json.loads(json.dumps(result.to_dict())) == result.to_dict()
    assert _analyze(from_plan, to_plan) == result


def test_addition_and_unchanged_contracts_are_deterministic_verified_results() -> None:
    unchanged = _analyze(_plan("generation-1"), _plan("generation-2"))
    to_plan = _plan("generation-2")
    dependency = next(relation for relation in to_plan.relations if relation.relation_type == "DEPENDS_ON")
    endpoint = next(node for node in to_plan.nodes if node.id == dependency.target_id)
    from_plan = _plan("generation-1")
    from_endpoint = next(
        node
        for node in from_plan.nodes
        if node.node_type == "Endpoint"
        and node.props["repo_id"] == REPO_ID
        and node.props["canonical_key"] == endpoint.props["canonical_key"]
    )
    addition = _analyze(_without_node(from_plan, from_endpoint.id), to_plan)

    assert unchanged.status is ServiceGraphChangeAnalysisStatus.READY
    assert unchanged.endpoint_additions == ()
    assert unchanged.endpoint_deletions == ()
    assert unchanged.contract_changes == ()
    assert [change.contract.canonical_key for change in addition.endpoint_additions] == [
        endpoint.props["canonical_key"]
    ]
    assert len(addition.direct_impacts) == 1
    assert addition.direct_impacts[0].impacted_endpoint.repo_id == "consumer-checkout"


def test_evidence_or_source_revision_change_is_fact_revision_not_contract_change() -> None:
    from_plan = _plan("generation-1", "p1")
    to_plan = _plan("generation-2", "p2")

    result = _analyze(from_plan, to_plan)

    assert result.status is ServiceGraphChangeAnalysisStatus.READY
    assert result.endpoint_additions == ()
    assert result.endpoint_deletions == ()
    assert result.contract_changes == ()
    assert len(result.fact_revisions) == 8
    assert {revision.before.source_revision for revision in result.fact_revisions} == {"p1"}
    assert {revision.after.source_revision for revision in result.fact_revisions} == {"p2"}


def test_same_endpoint_id_with_changed_contract_is_contract_change() -> None:
    from_plan = _plan("generation-1")
    to_plan = _plan("generation-2")
    from_endpoint = next(
        node for node in from_plan.nodes if node.node_type == "Endpoint" and node.props["repo_id"] == REPO_ID
    )
    to_endpoint = next(
        node for node in to_plan.nodes if node.node_type == "Endpoint" and node.props["repo_id"] == REPO_ID
    )
    changed_key = f"{to_endpoint.props['canonical_key']}|v2"
    replacement = replace(
        to_endpoint,
        id=from_endpoint.id,
        props={**to_endpoint.props, "id": from_endpoint.id, "canonical_key": changed_key},
    )
    to_plan = GraphWritePlan(
        tuple(replacement if node.id == to_endpoint.id else node for node in to_plan.nodes),
        tuple(
            replace(
                relation,
                source_id=from_endpoint.id if relation.source_id == to_endpoint.id else relation.source_id,
                target_id=from_endpoint.id if relation.target_id == to_endpoint.id else relation.target_id,
                props={**relation.props, "canonical_key": changed_key}
                if relation.source_id == to_endpoint.id
                else relation.props,
            )
            for relation in to_plan.relations
        ),
    )

    result = _analyze(from_plan, to_plan)

    assert result.status is ServiceGraphChangeAnalysisStatus.READY
    assert len(result.contract_changes) == 1
    assert result.contract_changes[0].before.canonical_key == from_endpoint.props["canonical_key"]
    assert result.contract_changes[0].after.canonical_key == changed_key


def test_analysis_fails_closed_for_missing_identity_unconfirmed_readback_and_bad_dependency_provenance() -> None:
    from_plan = _plan("generation-1")
    to_plan = _plan("generation-2")
    dependency = next(relation for relation in to_plan.relations if relation.relation_type == "DEPENDS_ON")
    malformed = GraphWritePlan(
        to_plan.nodes,
        tuple(
            replace(relation, props={**relation.props, "provider_repo_id": "wrong"})
            if relation.id == dependency.id
            else relation
            for relation in to_plan.relations
        ),
    )

    assert _analyze(from_plan, to_plan, repo_id=" ").reasons == (
        ServiceGraphChangeAnalysisBlockReason.MALFORMED_REQUEST,
    )
    assert _analyze(from_plan, to_plan, from_receipt=_receipt(from_plan, confirmed=False)).reasons == (
        ServiceGraphChangeAnalysisBlockReason.UNCONFIRMED_READBACK,
    )
    assert _analyze(from_plan, malformed).reasons == (
        ServiceGraphChangeAnalysisBlockReason.RELATION_PROVENANCE_MISMATCH,
    )
