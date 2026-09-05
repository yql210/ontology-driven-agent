from __future__ import annotations

import json
from pathlib import Path

from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext, MethodDetector
from ontoagent.parsing.service_graph.detectors.feign_method import FeignMethodDetector
from ontoagent.parsing.service_graph.detectors.spring_http_method import SpringHttpMethodDetector
from ontoagent.parsing.service_graph.method_graph_writer import MethodGraphScope, MethodGraphWritePlan, fact_from_dict
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.workspace.models import (
    WorkspaceGeneration,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"
REVISIONS = {"provider-orders": "fixture-provider-v1", "consumer-checkout": "fixture-consumer-v1"}


def _detect(repo_id: str):
    snapshot = RepositorySnapshot(repo_id, REVISIONS[repo_id], FIXTURE / repo_id, frozenset({"java"}))
    return FeignMethodDetector().detect_methods(
        snapshot, MethodDetectionContext(repo_id, repo_id, repo_id, snapshot.source_revision, "feign-gen")
    )


def _plan():
    provider_snapshot = RepositorySnapshot(
        "provider-orders", REVISIONS["provider-orders"], FIXTURE / "provider-orders", frozenset({"java"})
    )
    provider = SpringHttpMethodDetector().detect_methods(
        provider_snapshot,
        MethodDetectionContext(
            "provider-orders", "provider-orders", "provider-orders", provider_snapshot.source_revision, "feign-gen"
        ),
    )
    generation = WorkspaceGeneration(
        "feign-workspace",
        "feign-gen",
        tuple(
            WorkspaceRepositorySnapshot(
                "feign-workspace",
                repo_id,
                "main",
                revision,
                WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}"),
            )
            for repo_id, revision in REVISIONS.items()
        ),
    )
    return MethodGraphWritePlan(
        MethodGraphScope("feign-namespace", generation), (provider, _detect("consumer-checkout"))
    )


def test_feign_method_detector_implements_sdk_and_emits_json_safe_facts() -> None:
    facts = _detect("consumer-checkout")

    assert isinstance(FeignMethodDetector(), MethodDetector)
    assert FeignMethodDetector.metadata.capabilities[0].capability_id == "feign-methods"
    assert fact_from_dict(json.loads(json.dumps(facts.to_dict()))) == facts


def test_literal_feign_get_and_post_calls_resolve_to_provider_operations() -> None:
    plan = _plan()
    facts = next(item for item in plan.facts if item.detector_id == "feign-method")

    calls = {item.target_reference: item for item in facts.consumer_calls}
    assert set(calls) >= {"feign-http:GET:/orders/{id}", "feign-http:POST:/orders"}
    assert plan.operation_id_for(calls["feign-http:GET:/orders/{id}"].target_reference) == next(
        item.id
        for fact in plan.facts
        for item in fact.operations
        if item.declaring_interface_fqcn == "spring-http:GET:/orders/{id}"
    )
    assert plan.operation_id_for(calls["feign-http:POST:/orders"].target_reference) == next(
        item.id
        for fact in plan.facts
        for item in fact.operations
        if item.declaring_interface_fqcn == "spring-http:POST:/orders"
    )
    declared = next(
        item for item in facts.operations if item.declaring_interface_fqcn.endswith("endpoint=/orders/{id}")
    )
    assert "name=orders" in declared.declaring_interface_fqcn
    assert "contextId=orders-client" in declared.declaring_interface_fqcn
    assert "url=http://orders.internal" in declared.declaring_interface_fqcn


def test_feign_overloads_are_separate_and_ordinary_interface_is_not_detected() -> None:
    facts = _detect("consumer-checkout")

    overloads = [item for item in facts.operations if item.operation_name == "lookup"]
    assert len(overloads) == 2
    assert len({item.canonical_signature for item in overloads}) == 2
    assert all("OrdinaryHttpInterface" not in item.canonical_signature for item in facts.operations)


def test_dynamic_feign_metadata_and_orphan_proxy_calls_are_unresolved_without_calls() -> None:
    facts = _detect("consumer-checkout")

    assert any(
        item.reason_code == "DYNAMIC_TARGET" and "DynamicOrdersFeignClient" in item.subject for item in facts.unresolved
    )
    assert any(
        item.reason_code == "MISSING_IMPLEMENTATION" and "orders.get" in item.subject for item in facts.unresolved
    )
    assert all("dynamicOrders" not in item.target_reference for item in facts.consumer_calls)


def test_mapping_mismatch_is_unresolved_and_does_not_link_to_a_provider_operation() -> None:
    plan = _plan()
    facts = next(item for item in plan.facts if item.detector_id == "feign-method")

    mismatch = next(item for item in facts.consumer_calls if item.target_reference == "feign-http:GET:/orders/missing")
    assert any(
        item.reason_code == "IDENTITY_MISMATCH" and item.subject == mismatch.target_reference
        for item in facts.unresolved
    )
    try:
        plan.operation_id_for(mismatch.target_reference)
    except ValueError as error:
        assert "ambiguous call target" in str(error)
    else:
        raise AssertionError("mismatched Feign mapping must not resolve")


def test_multiple_feign_method_mappings_are_unresolved_without_a_high_confidence_call() -> None:
    facts = _detect("consumer-checkout")

    assert any(item.reason_code == "AMBIGUOUS_TARGET" and "write" in item.subject for item in facts.unresolved)
    assert all("ambiguous" not in item.target_reference for item in facts.consumer_calls)
