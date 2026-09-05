from __future__ import annotations

import json
from pathlib import Path

from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext, MethodDetector
from ontoagent.parsing.service_graph.detectors.spring_http_method import SpringHttpMethodDetector
from ontoagent.parsing.service_graph.method_graph_writer import fact_from_dict
from ontoagent.parsing.service_graph.models import RepositorySnapshot

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


def _detect(repo_id: str, generation_id: str = "generation-spring-methods"):
    snapshot = RepositorySnapshot(repo_id, f"fixture-{repo_id}-v1", FIXTURE / repo_id, frozenset({"java", "yaml"}))
    return SpringHttpMethodDetector().detect_methods(
        snapshot, MethodDetectionContext(repo_id, "spring-app", repo_id, snapshot.source_revision, generation_id)
    )


def test_spring_http_method_detector_implements_sdk_and_emits_json_safe_facts() -> None:
    detector = SpringHttpMethodDetector()

    assert isinstance(detector, MethodDetector)
    assert detector.metadata.capabilities[0].capability_id == "spring-http-methods"
    assert json.loads(json.dumps(_detect("provider-orders").to_dict()))["generation_id"] == "generation-spring-methods"


def test_provider_mapping_emits_operation_implementation_and_binding() -> None:
    facts = _detect("provider-orders")

    operation = next(item for item in facts.operations if item.operation_name == "get")
    implementation = next(item for item in facts.implementations if item.method_name == "get")
    binding = next(item for item in facts.bindings if item.operation_id == operation.id)
    assert operation.declaring_interface_fqcn == "spring-http:GET:/orders/{id}"
    assert operation.canonical_signature == "example.orders.OrderApi#get(java.lang.String):example.orders.OrderDto"
    assert (
        implementation.canonical_signature
        == "example.orders.OrderController#get(java.lang.String):example.orders.OrderDto"
    )
    assert binding.provider_endpoint_reference == "spring-http:GET:/orders/{id}"
    assert binding.implementation_id == implementation.id


def test_provider_overloads_keep_distinct_signatures() -> None:
    facts = _detect("provider-orders")

    overloads = [item for item in facts.operations if item.operation_name == "lookup"]
    assert len(overloads) == 2
    assert len({item.id for item in overloads}) == 2
    assert {item.canonical_signature for item in overloads} == {
        "example.orders.OrderApi#lookup(java.lang.String):example.orders.OrderDto",
        "example.orders.OrderApi#lookup(long):example.orders.OrderDto",
    }


def test_consumer_literal_call_uses_enclosing_method_and_operation_reference() -> None:
    facts = _detect("consumer-checkout")

    call = next(item for item in facts.consumer_calls if item.target_reference == "spring-http:GET:/orders/{id}")
    caller = next(item for item in facts.implementations if item.id == call.caller_implementation_id)
    assert call.target_kind == "operation"
    assert caller.method_name == "loadOrder"
    evidence = next(item for item in facts.evidences if item.id == call.evidence_ids[0])
    assert evidence.evidence_type == "consumer_method_call"
    assert evidence.file_path.endswith("CheckoutService.java")


def test_dynamic_url_is_unresolved_and_helper_method_does_not_emit_call() -> None:
    facts = _detect("consumer-checkout")

    assert any(item.reason_code == "DYNAMIC_TARGET" for item in facts.unresolved)
    assert any(item.reason_code == "MISSING_IMPLEMENTATION" for item in facts.unresolved)
    helper = next(item for item in facts.implementations if item.method_name == "helper")
    assert all(item.caller_implementation_id != helper.id for item in facts.consumer_calls)
    assert not facts.operations


def test_repeated_dynamic_target_is_one_persistable_unresolved_fact_with_all_evidence() -> None:
    facts = _detect("consumer-checkout")

    dynamic_calls = [
        item for item in facts.unresolved if item.reason_code == "DYNAMIC_TARGET" and item.subject == "baseUrl + id"
    ]

    assert len(dynamic_calls) == 1
    assert len(dynamic_calls[0].evidence_ids) == 2
    assert len({item.id for item in facts.unresolved}) == len(facts.unresolved)


def test_detected_facts_equal_their_canonical_payload_readback() -> None:
    facts = _detect("provider-orders")

    assert fact_from_dict(facts.to_dict()) == facts
