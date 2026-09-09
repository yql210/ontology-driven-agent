from __future__ import annotations

import json

import pytest

from ontoagent.parsing.service_graph import (
    ConsumerMethodCall,
    DetectorCapability,
    DetectorMetadata,
    ImplementationMethod,
    MethodDetector,
    MethodEvidence,
    MethodFacts,
    MethodUnresolved,
    OperationBinding,
    RetainedSourceCall,
    ServiceOperation,
)
from ontoagent.parsing.service_graph.method_graph_writer import fact_from_dict


def _evidence(subject: str = "provider operation") -> MethodEvidence:
    return MethodEvidence(
        repo_id="orders-repo",
        module_id="orders-api",
        service_id="orders",
        source_revision="commit-1",
        generation_id="generation-1",
        file_path="src/main/java/example/orders/OrderApi.java",
        start_line=10,
        end_line=10,
        detector_id="generic-java",
        detector_version="1.0",
        evidence_type="declared_operation",
        subject=subject,
        confidence=1.0,
    )


def _operation(signature: str, evidence_id: str) -> ServiceOperation:
    return ServiceOperation(
        repo_id="orders-repo",
        module_id="orders-api",
        service_id="orders",
        source_revision="commit-1",
        generation_id="generation-1",
        role="provider",
        declaring_interface_fqcn="example.orders.OrderApi",
        operation_name="find",
        canonical_signature=signature,
        evidence_ids=(evidence_id,),
    )


def test_service_operations_keep_overloads_distinct_and_json_safe():
    evidence = _evidence()
    by_id = _operation("example.orders.OrderApi#find(java.lang.String):example.orders.Order", evidence.id)
    by_number = _operation("example.orders.OrderApi#find(long):example.orders.Order", evidence.id)

    assert by_id.id != by_number.id
    assert by_id.canonical_key != by_number.canonical_key
    assert json.loads(json.dumps(by_id.to_dict()))["canonical_signature"] == by_id.canonical_signature


def test_same_display_name_in_different_repositories_does_not_merge():
    evidence = _evidence()
    left = _operation("example.orders.OrderApi#find(java.lang.String):example.orders.Order", evidence.id)
    right = ServiceOperation(
        repo_id="archive-repo",
        module_id="orders-api",
        service_id="orders",
        source_revision="commit-1",
        generation_id="generation-1",
        role="provider",
        declaring_interface_fqcn="example.orders.OrderApi",
        operation_name="find",
        canonical_signature=left.canonical_signature,
        evidence_ids=(evidence.id,),
    )

    assert left.display_name == right.display_name == "example.orders.OrderApi.find"
    assert left.id != right.id


def test_consumer_method_call_requires_evidence_and_resolves_through_method_facts():
    evidence = _evidence("consumer call")
    caller = ImplementationMethod(
        repo_id="orders-repo",
        module_id="orders-api",
        service_id="orders",
        source_revision="commit-1",
        generation_id="generation-1",
        class_fqcn="example.orders.OrderClient",
        method_name="load",
        canonical_signature="example.orders.OrderClient#load(java.lang.String):example.orders.Order",
        file_path="src/main/java/example/orders/OrderClient.java",
        evidence_ids=(evidence.id,),
    )
    operation = _operation("example.orders.OrderApi#find(java.lang.String):example.orders.Order", evidence.id)
    with pytest.raises(ValueError, match="evidence_ids"):
        ConsumerMethodCall(
            repo_id="orders-repo",
            module_id="orders-api",
            service_id="orders",
            source_revision="commit-1",
            generation_id="generation-1",
            caller_implementation_id=caller.id,
            target_reference=operation.id,
            target_kind="operation",
            evidence_ids=(),
        )

    call = ConsumerMethodCall(
        repo_id="orders-repo",
        module_id="orders-api",
        service_id="orders",
        source_revision="commit-1",
        generation_id="generation-1",
        caller_implementation_id=caller.id,
        target_reference=operation.id,
        target_kind="operation",
        evidence_ids=(evidence.id,),
    )
    facts = MethodFacts(
        detector_id="generic-java",
        detector_version="1.0",
        repo_id="orders-repo",
        source_revision="commit-1",
        generation_id="generation-1",
        operations=(operation,),
        implementations=(caller,),
        consumer_calls=(call,),
        bindings=(),
        evidences=(evidence,),
        unresolved=(),
    )

    assert facts.to_dict()["consumer_calls"][0]["evidence_ids"] == [evidence.id]


def test_method_unresolved_preserves_typed_reason_and_evidence():
    evidence = _evidence("unresolved call")
    unresolved = MethodUnresolved(
        repo_id="orders-repo",
        module_id="orders-api",
        service_id="orders",
        source_revision="commit-1",
        generation_id="generation-1",
        reason_code="DYNAMIC_TARGET",
        subject="client.lookup(dynamicTarget)",
        evidence_ids=(evidence.id,),
    )
    facts = MethodFacts(
        "generic-java",
        "1.0",
        "orders-repo",
        "commit-1",
        "generation-1",
        (),
        (),
        (),
        (),
        (evidence,),
        (unresolved,),
    )

    assert facts.to_dict()["unresolved"] == [unresolved.to_dict()]
    with pytest.raises(ValueError):
        MethodUnresolved(
            "orders-repo", "orders-api", "orders", "commit-1", "generation-1", "UNKNOWN", "target", (evidence.id,)
        )


def test_operation_binding_is_evidence_backed_and_optional_implementation():
    evidence = _evidence("binding")
    operation = _operation("example.orders.OrderApi#find(java.lang.String):example.orders.Order", evidence.id)
    binding = OperationBinding(
        repo_id="orders-repo",
        module_id="orders-api",
        service_id="orders",
        source_revision="commit-1",
        generation_id="generation-1",
        provider_endpoint_reference="provider-endpoint-id",
        operation_id=operation.id,
        implementation_id=None,
        evidence_ids=(evidence.id,),
    )

    assert binding.to_dict()["implementation_id"] is None


def _retained_source_call(
    caller_implementation_id: str, evidence_id: str, *, start_line: int = 42
) -> RetainedSourceCall:
    return RetainedSourceCall(
        repo_id="orders-repo",
        module_id="orders-api",
        service_id="orders",
        source_revision="commit-1",
        generation_id="generation-1",
        file_path="src/main/java/example/orders/OrderClient.java",
        start_line=start_line,
        start_column=9,
        end_line=start_line,
        end_column=31,
        caller_implementation_id=caller_implementation_id,
        receiver_declaration="private final OrderApi orderApi",
        receiver_type="example.orders.OrderApi",
        receiver_missing_reason=None,
        receiver_evidence_ids=(evidence_id,),
        method_name="find",
        argument_summaries=("orderId", '"full"'),
        argument_types=("java.lang.String", "java.lang.String"),
        argument_evidence_ids=((evidence_id,), (evidence_id,)),
        protocol_settings=(("group", "retail"), ("version", "1.0")),
        resolution_stage="SOURCE_CAPTURE",
        resolution_status="CAPTURED",
        resolution_reason=None,
        evidence_ids=(evidence_id,),
    )


def test_retained_source_call_is_immutable_source_pinned_and_json_safe():
    evidence = _evidence("retained source call")
    caller = ImplementationMethod(
        "orders-repo",
        "orders-api",
        "orders",
        "commit-1",
        "generation-1",
        "example.orders.OrderClient",
        "load",
        "example.orders.OrderClient#load(java.lang.String):example.orders.Order",
        "src/main/java/example/orders/OrderClient.java",
        (evidence.id,),
    )
    retained = _retained_source_call(caller.id, evidence.id)

    assert json.loads(json.dumps(retained.to_dict())) == retained.to_dict()
    assert retained.to_dict()["argument_evidence_ids"] == [[evidence.id], [evidence.id]]
    with pytest.raises(ValueError, match="receiver_missing_reason"):
        RetainedSourceCall(
            **{
                key: value
                for key, value in {**retained.to_dict(), "receiver_type": None, "receiver_missing_reason": None}.items()
                if key != "id"
            }
        )


def test_retained_source_calls_at_distinct_source_lines_do_not_collapse_and_round_trip():
    evidence = _evidence("retained source calls")
    caller = ImplementationMethod(
        "orders-repo",
        "orders-api",
        "orders",
        "commit-1",
        "generation-1",
        "example.orders.OrderClient",
        "load",
        "example.orders.OrderClient#load(java.lang.String):example.orders.Order",
        "src/main/java/example/orders/OrderClient.java",
        (evidence.id,),
    )
    first = _retained_source_call(caller.id, evidence.id, start_line=42)
    second = _retained_source_call(caller.id, evidence.id, start_line=43)
    facts = MethodFacts(
        "generic-java",
        "1.0",
        "orders-repo",
        "commit-1",
        "generation-1",
        (),
        (caller,),
        (),
        (),
        (evidence,),
        (),
        (second, first),
    )

    assert first.id != second.id
    assert facts.retained_source_calls == tuple(sorted((first, second), key=lambda item: item.id))
    assert fact_from_dict(facts.to_dict()) == facts


def test_fact_from_dict_accepts_legacy_method_facts_without_retained_source_calls():
    evidence = _evidence("legacy payload")
    facts = MethodFacts(
        "generic-java", "1.0", "orders-repo", "commit-1", "generation-1", (), (), (), (), (evidence,), ()
    )
    payload = facts.to_dict()

    assert "retained_source_calls" not in payload
    assert fact_from_dict(payload) == facts


def test_detector_sdk_accepts_a_protocol_neutral_fake_detector():
    class FakeDetector:
        metadata = DetectorMetadata(
            detector_id="generic-java",
            detector_version="1.0",
            supported_languages=frozenset({"java"}),
            capabilities=(DetectorCapability("method-declarations", "1"),),
        )

        def detect_methods(self, snapshot):
            return MethodFacts(
                self.metadata.detector_id,
                self.metadata.detector_version,
                snapshot.repo_id,
                snapshot.source_revision,
                "generation-1",
                (),
                (),
                (),
                (),
                (),
                (),
            )

    detector = FakeDetector()
    assert isinstance(detector, MethodDetector)
    assert detector.metadata.capabilities[0].capability_id == "method-declarations"
