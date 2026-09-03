from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.generation_manifest import (
    ManifestBlockReason,
    ManifestResolutionStatus,
    ManifestState,
    Neo4jNamespace,
    ServiceGraphManifest,
    ServiceGraphManifestRegistry,
)
from ontoagent.parsing.service_graph.graph_plan import GraphNode, GraphPlanBuilder, GraphRelation, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import WriteReceipt
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


def _manifest(*, generation_id: str = "generation-1", namespace: str = "service-graph") -> ServiceGraphManifest:
    return ServiceGraphManifest("repo-1", generation_id, "revision-1", Neo4jNamespace(namespace))


def _plan(
    *, repo_id: str = "repo-1", generation_id: str = "generation-1", revision: str = "revision-1"
) -> GraphWritePlan:
    node_props = {
        "id": "endpoint-1",
        "repo_id": repo_id,
        "generation_id": generation_id,
        "source_revision": revision,
        "canonical_key": "endpoint-1",
        "evidence_ids": ("evidence-1",),
    }
    relation_props = {
        "generation_id": generation_id,
        "source_revision": revision,
        "canonical_key": "relation-1",
        "evidence_ids": ("evidence-1",),
    }
    return GraphWritePlan(
        (GraphNode("endpoint-1", "Endpoint", node_props),),
        (GraphRelation("relation-1", "PROVIDES_ENDPOINT", "endpoint-1", "endpoint-1", relation_props),),
    )


def _receipt(plan: GraphWritePlan, *, confirmed: bool = True, namespace: str = "service-graph") -> WriteReceipt:
    return WriteReceipt(confirmed, len(plan.nodes), len(plan.relations), plan, namespace)


def _three_repo_plan() -> GraphWritePlan:
    batches = []
    for repo_id, revision in (("provider-orders", "p1"), ("consumer-checkout", "c1"), ("isolated-catalog", "i1")):
        snapshot = RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
        facts = tuple(
            detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
        )
        batches.append(FactBatch(repo_id, revision, "generation-1", "main", facts))
    return GraphPlanBuilder().build(ServiceGraphResolver().resolve(tuple(batches)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repo_id", " "),
        ("generation_id", ""),
        ("source_revision", "\t"),
        ("status", "building"),
        ("status", ManifestState.READY),
    ],
)
def test_manifest_rejects_blank_identifiers_and_malformed_state(field: str, value: object) -> None:
    values: dict[str, object] = {
        "repo_id": "repo-1",
        "generation_id": "generation-1",
        "source_revision": "revision-1",
        "graph_namespace": Neo4jNamespace("service-graph"),
        "status": ManifestState.BUILDING,
    }
    values[field] = value

    with pytest.raises(ValueError):
        ServiceGraphManifest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("namespace", ["", " ", 1])
def test_neo4j_namespace_rejects_malformed_values(namespace: object) -> None:
    with pytest.raises(ValueError):
        Neo4jNamespace(namespace)  # type: ignore[arg-type]


def test_manifest_is_frozen_and_starts_building() -> None:
    manifest = _manifest()

    assert manifest.status is ManifestState.BUILDING
    with pytest.raises(AttributeError):
        manifest.repo_id = "other"  # type: ignore[misc]


def test_confirmed_exact_receipt_publishes_ready_binding() -> None:
    registry = ServiceGraphManifestRegistry()
    manifest = _manifest()
    plan = _plan()

    published = registry.publish(manifest, plan, _receipt(plan))
    resolved = registry.resolve("repo-1", "generation-1", Neo4jNamespace("service-graph"))

    assert published.status is ManifestResolutionStatus.READY
    assert published.binding is not None
    assert published.binding.manifest.status is ManifestState.BUILDING
    assert published.binding.status is ManifestState.READY
    assert resolved == published


def test_receipt_namespace_must_exactly_match_manifest() -> None:
    registry = ServiceGraphManifestRegistry()
    plan = _plan()

    blocked = registry.publish(_manifest(), plan, _receipt(plan, namespace="other-namespace"))

    assert blocked.status is ManifestResolutionStatus.BLOCKED
    assert blocked.reasons == (ManifestBlockReason.NAMESPACE_MISMATCH,)


def test_confirmed_replacement_atomically_replaces_active_binding() -> None:
    registry = ServiceGraphManifestRegistry()
    first_manifest = _manifest()
    first_plan = _plan()
    registry.publish(first_manifest, first_plan, _receipt(first_plan))
    second_manifest = _manifest(generation_id="generation-2", namespace="service-graph-v2")
    second_plan = _plan(generation_id="generation-2")

    registry.publish(second_manifest, second_plan, _receipt(second_plan, namespace="service-graph-v2"))

    active = registry.resolve("repo-1", "generation-2", Neo4jNamespace("service-graph-v2"))
    old = registry.resolve("repo-1", "generation-1", Neo4jNamespace("service-graph"))
    assert active.status is ManifestResolutionStatus.READY
    assert old.status is ManifestResolutionStatus.BLOCKED
    assert old.reasons == (ManifestBlockReason.GENERATION_MISMATCH,)


@pytest.mark.parametrize(
    "receipt_factory",
    [
        lambda plan: _receipt(plan, confirmed=False),
        lambda plan: replace(_receipt(plan), node_count=2),
        lambda plan: replace(_receipt(plan), readback=GraphWritePlan((), ())),
    ],
    ids=("unconfirmed", "count_mismatch", "readback_shape_mismatch"),
)
def test_failed_candidate_preserves_prior_active_binding(receipt_factory: object) -> None:
    registry = ServiceGraphManifestRegistry()
    old_manifest = _manifest()
    old_plan = _plan()
    registry.publish(old_manifest, old_plan, _receipt(old_plan))
    candidate = _manifest(generation_id="generation-2", namespace="service-graph-v2")
    candidate_plan = _plan(generation_id="generation-2")

    receipt = receipt_factory(candidate_plan)  # type: ignore[operator]
    blocked = registry.publish(candidate, candidate_plan, replace(receipt, graph_namespace="service-graph-v2"))

    assert blocked.status is ManifestResolutionStatus.BLOCKED
    assert (
        registry.resolve("repo-1", "generation-1", Neo4jNamespace("service-graph")).status
        is ManifestResolutionStatus.READY
    )
    assert registry.resolve("repo-1", "generation-2", Neo4jNamespace("service-graph-v2")).status is (
        ManifestResolutionStatus.BLOCKED
    )


def test_provenance_failure_preserves_prior_active_binding_when_manifest_repo_is_absent() -> None:
    registry = ServiceGraphManifestRegistry()
    old_manifest = _manifest()
    old_plan = _plan()
    registry.publish(old_manifest, old_plan, _receipt(old_plan))
    candidate = _manifest(generation_id="generation-2", namespace="service-graph-v2")
    candidate_plan = _plan(repo_id="repo-2", generation_id="generation-2")

    blocked = registry.publish(candidate, candidate_plan, _receipt(candidate_plan, namespace="service-graph-v2"))

    assert blocked.status is ManifestResolutionStatus.BLOCKED
    assert blocked.reasons == (ManifestBlockReason.NODE_PROVENANCE_MISMATCH,)
    assert (
        registry.resolve("repo-1", "generation-1", Neo4jNamespace("service-graph")).status
        is ManifestResolutionStatus.READY
    )


def test_stale_target_repo_node_blocks_and_preserves_prior_active_binding() -> None:
    registry = ServiceGraphManifestRegistry()
    old_plan = _plan()
    registry.publish(_manifest(), old_plan, _receipt(old_plan))
    candidate = _manifest(generation_id="generation-2", namespace="service-graph-v2")
    matching_plan = _plan(generation_id="generation-2")
    stale_node = replace(
        matching_plan.nodes[0],
        id="endpoint-stale",
        props={
            **matching_plan.nodes[0].props,
            "id": "endpoint-stale",
            "generation_id": "generation-1",
            "source_revision": "revision-stale",
            "canonical_key": "endpoint-stale",
        },
    )
    candidate_plan = replace(matching_plan, nodes=(*matching_plan.nodes, stale_node))

    blocked = registry.publish(candidate, candidate_plan, _receipt(candidate_plan, namespace="service-graph-v2"))

    assert blocked.status is ManifestResolutionStatus.BLOCKED
    assert blocked.reasons == (ManifestBlockReason.NODE_PROVENANCE_MISMATCH,)
    assert (
        registry.resolve("repo-1", "generation-1", Neo4jNamespace("service-graph")).status
        is ManifestResolutionStatus.READY
    )


@pytest.mark.parametrize(("field", "value"), (("generation_id", "other"), ("source_revision", "other")))
def test_relation_provenance_must_exactly_match_manifest(field: str, value: str) -> None:
    registry = ServiceGraphManifestRegistry()
    base_plan = _plan()
    relation = replace(base_plan.relations[0], props={**base_plan.relations[0].props, field: value})
    plan = replace(base_plan, relations=(relation,))

    blocked = registry.publish(_manifest(), plan, _receipt(plan))

    assert blocked.status is ManifestResolutionStatus.BLOCKED
    assert blocked.reasons == (ManifestBlockReason.RELATION_PROVENANCE_MISMATCH,)


def test_confirmed_empty_plan_cannot_be_published() -> None:
    registry = ServiceGraphManifestRegistry()
    plan = GraphWritePlan((), ())

    published = registry.publish(_manifest(), plan, _receipt(plan))

    assert published.status is ManifestResolutionStatus.BLOCKED
    assert published.reasons == (ManifestBlockReason.NODE_PROVENANCE_MISMATCH,)


def test_manifest_repo_can_publish_the_neutral_three_repo_plan() -> None:
    registry = ServiceGraphManifestRegistry()
    plan = _three_repo_plan()
    manifest = ServiceGraphManifest("consumer-checkout", "generation-1", "c1", Neo4jNamespace("service-graph"))

    published = registry.publish(manifest, plan, _receipt(plan))

    assert published.status is ManifestResolutionStatus.READY


@pytest.mark.parametrize(
    "plan_factory",
    [
        lambda plan: replace(plan, nodes=(object(),)),
        lambda plan: replace(plan, relations=(object(),)),
        lambda plan: replace(plan, relations=(replace(plan.relations[0], source_id="missing"),)),
        lambda plan: replace(plan, nodes=(replace(plan.nodes[0], props={"id": plan.nodes[0].id}),)),
    ],
    ids=("unsupported_node", "unsupported_relation", "missing_endpoint", "incomplete_node_provenance"),
)
def test_malformed_graph_records_block_and_preserve_prior_active_binding(plan_factory: object) -> None:
    registry = ServiceGraphManifestRegistry()
    old_plan = _plan()
    registry.publish(_manifest(), old_plan, _receipt(old_plan))
    candidate = _manifest(generation_id="generation-2", namespace="service-graph-v2")
    plan = plan_factory(_plan(generation_id="generation-2"))  # type: ignore[operator]

    blocked = registry.publish(candidate, plan, _receipt(plan, namespace="service-graph-v2"))

    assert blocked.status is ManifestResolutionStatus.BLOCKED
    assert (
        registry.resolve("repo-1", "generation-1", Neo4jNamespace("service-graph")).status
        is ManifestResolutionStatus.READY
    )


@pytest.mark.parametrize(
    ("repo_id", "generation_id", "namespace", "reason"),
    [
        ("missing", "generation-1", Neo4jNamespace("service-graph"), ManifestBlockReason.REPO_MISMATCH),
        ("repo-1", "other", Neo4jNamespace("service-graph"), ManifestBlockReason.GENERATION_MISMATCH),
        ("repo-1", "generation-1", Neo4jNamespace("other"), ManifestBlockReason.NAMESPACE_MISMATCH),
    ],
)
def test_resolve_returns_blocked_for_missing_and_identity_mismatches(
    repo_id: str, generation_id: str, namespace: Neo4jNamespace, reason: ManifestBlockReason
) -> None:
    registry = ServiceGraphManifestRegistry()
    plan = _plan()
    registry.publish(_manifest(), plan, _receipt(plan))

    resolution = registry.resolve(repo_id, generation_id, namespace)

    assert resolution.status is ManifestResolutionStatus.BLOCKED
    assert resolution.binding is None
    assert resolution.reasons == (reason,)


def test_resolve_returns_blocked_for_malformed_request_values() -> None:
    registry = ServiceGraphManifestRegistry()

    resolution = registry.resolve(" ", "generation-1", Neo4jNamespace("service-graph"))

    assert resolution.status is ManifestResolutionStatus.BLOCKED
    assert resolution.reasons == (ManifestBlockReason.MALFORMED_REQUEST,)


def test_resolve_returns_blocked_for_missing_active_binding() -> None:
    resolution = ServiceGraphManifestRegistry().resolve("repo-1", "generation-1", Neo4jNamespace("service-graph"))

    assert resolution.status is ManifestResolutionStatus.BLOCKED
    assert resolution.reasons == (ManifestBlockReason.MISSING_ACTIVE,)
