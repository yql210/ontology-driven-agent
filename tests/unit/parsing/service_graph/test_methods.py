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
    ServiceOperation,
)


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
