from __future__ import annotations

from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext, MethodDetector
from ontoagent.parsing.service_graph.detectors.dubbo_method import DubboMethodDetector
from ontoagent.parsing.service_graph.detectors.grpc_method import GrpcMethodDetector
from ontoagent.parsing.service_graph.detectors.messaging_method import MessagingMethodDetector
from ontoagent.parsing.service_graph.detectors.spring_http_method import SpringHttpMethodDetector
from ontoagent.parsing.service_graph.method_graph_writer import MethodGraphScope, MethodGraphWritePlan
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.workspace.models import (
    WorkspaceGeneration,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)


def _detect(tmp_path: Path, source: str, repo_id: str = "orders") -> object:
    path = tmp_path / "src/main/java/example/orders/OrderServiceGrpc.java"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    snapshot = RepositorySnapshot(repo_id, "rev-1", tmp_path, frozenset({"java"}))
    return GrpcMethodDetector().detect_methods(
        snapshot, MethodDetectionContext(repo_id, repo_id, repo_id, "rev-1", "gen-1")
    )


def test_grpc_method_detector_emits_provider_operations_implementations_and_bindings(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
class GetOrderRequest {} class GetOrderResponse {}
class StreamObserver<T> {}
class OrderServiceGrpc { abstract static class OrderServiceImplBase {
  public void getOrder(GetOrderRequest request, StreamObserver<GetOrderResponse> observer) {}
}}
class OrderService extends OrderServiceGrpc.OrderServiceImplBase {
  @Override public void getOrder(GetOrderRequest request, StreamObserver<GetOrderResponse> observer) {}
}""",
    )

    assert isinstance(GrpcMethodDetector(), MethodDetector)
    assert [item.canonical_signature for item in facts.operations] == [
        "example.orders.OrderServiceGrpc#GetOrder(example.orders.GetOrderRequest):example.orders.GetOrderResponse"
    ]
    assert facts.operations[0].declaring_interface_fqcn == "example.orders.OrderServiceGrpc"
    assert facts.operations[0].operation_name == "GetOrder"
    assert len(facts.implementations) == len(facts.bindings) == 1
    assert facts.bindings[0].implementation_id == facts.implementations[0].id
    assert all(item.evidence_ids for item in (*facts.operations, *facts.implementations, *facts.bindings))


def test_grpc_method_detector_maps_blocking_and_async_stub_calls_from_fields_and_locals(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
class GetOrderRequest {} class GetOrderResponse {} class StreamObserver<T> {}
class Channel {}
class OrderServiceGrpc {
  static class OrderServiceBlockingStub { GetOrderResponse getOrder(GetOrderRequest request) { return null; } }
  static class OrderServiceStub { void getOrder(GetOrderRequest request, StreamObserver<GetOrderResponse> observer) {} }
  static OrderServiceBlockingStub newBlockingStub(Channel channel) { return null; }
  static OrderServiceStub newStub(Channel channel) { return null; }
}
class Checkout {
  private final OrderServiceGrpc.OrderServiceBlockingStub orders;
  Checkout(Channel channel) { this.orders = OrderServiceGrpc.newBlockingStub(channel); }
  GetOrderResponse blocking(GetOrderRequest request) { return orders.getOrder(request); }
  void async(Channel channel, GetOrderRequest request, StreamObserver<GetOrderResponse> observer) {
    OrderServiceGrpc.OrderServiceStub local = OrderServiceGrpc.newStub(channel);
    local.getOrder(request, observer);
  }
  String helper(String value) { return value.toUpperCase(); }
}""",
    )

    assert {item.target_reference for item in facts.consumer_calls} == {
        "grpc-operation:example.orders.OrderServiceGrpc#GetOrder(example.orders.GetOrderRequest):example.orders.GetOrderResponse"
    }
    assert len(facts.consumer_calls) == 2
    assert {item.method_name for item in facts.implementations} >= {"blocking", "async"}
    assert not facts.unresolved


@pytest.mark.parametrize(
    "detector",
    (SpringHttpMethodDetector(), DubboMethodDetector(), MessagingMethodDetector()),
)
def test_non_grpc_method_detectors_canonicalize_nested_grpc_stub_evidence(
    tmp_path: Path, detector: MethodDetector
) -> None:
    path = tmp_path / "src/main/java/example/orders/OrderServiceGrpc.java"
    path.parent.mkdir(parents=True)
    path.write_text(
        """package example.orders;
class Request {} class Response {} class StreamObserver<T> {} class Channel {}
class OrderServiceGrpc {
  static class OrderServiceBlockingStub { Response get(Request request) { return null; } }
  static class OrderServiceStub { void get(Request request, StreamObserver<Response> observer) {} }
  static OrderServiceBlockingStub newBlockingStub(Channel channel) { return null; }
  static OrderServiceStub newStub(Channel channel) { return null; }
}"""
    )
    snapshot = RepositorySnapshot("orders", "rev-1", tmp_path, frozenset({"java"}))

    facts = detector.detect_methods(snapshot, MethodDetectionContext("orders", "orders", "orders", "rev-1", "gen-1"))

    assert len({item.id for item in facts.evidences}) == len(facts.evidences)


def test_grpc_method_detector_keeps_same_rpc_name_for_different_services_separate(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
class FindRequest {} class FindResponse {} class StreamObserver<T> {}
class OrderServiceGrpc { abstract static class OrderServiceImplBase {} }
class CatalogServiceGrpc { abstract static class CatalogServiceImplBase {} }
class Orders extends OrderServiceGrpc.OrderServiceImplBase {
  @Override public void find(FindRequest request, StreamObserver<FindResponse> observer) {}
}
class Catalog extends CatalogServiceGrpc.CatalogServiceImplBase {
  @Override public void find(FindRequest request, StreamObserver<FindResponse> observer) {}
}""",
    )

    assert {item.canonical_signature for item in facts.operations} == {
        "example.orders.OrderServiceGrpc#Find(example.orders.FindRequest):example.orders.FindResponse",
        "example.orders.CatalogServiceGrpc#Find(example.orders.FindRequest):example.orders.FindResponse",
    }


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (
            """package example.orders;
class Request {} class Response {} class Channel {}
class OrderServiceGrpc { static class OrderServiceBlockingStub { Response get(Request request) { return null; } } }
class Checkout { OrderServiceGrpc.OrderServiceBlockingStub stub; Response call(Request request) { return stub.get(request); } }""",
            "UNSUPPORTED_TARGET_SHAPE",
        ),
        (
            """package example.orders;
class Request {} class Response {} class Channel {}
class OrderServiceGrpc { static class OrderServiceBlockingStub { Response get(Request request) { return null; } }
 static OrderServiceBlockingStub newBlockingStub(Channel channel) { return null; } }
class Checkout { Response call(Channel channel, Request request) {
  OrderServiceGrpc.OrderServiceBlockingStub stub = OrderServiceGrpc.newBlockingStub(channelFor(channel));
  return stub.get(request); }
  Channel channelFor(Channel channel) { return channel; } }""",
            "DYNAMIC_TARGET",
        ),
    ],
)
def test_grpc_method_detector_marks_unknown_or_dynamic_stub_calls_unresolved(
    tmp_path: Path, source: str, reason: str
) -> None:
    facts = _detect(tmp_path, source)

    assert not facts.consumer_calls
    assert {item.reason_code for item in facts.unresolved} == {reason}
    assert all(item.evidence_ids for item in facts.unresolved)


def test_grpc_method_detector_marks_stub_call_outside_an_enclosing_method_unresolved(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
class Request {} class Response {} class Channel {}
class OrderServiceGrpc {
 static class OrderServiceBlockingStub { Response get(Request request) { return null; } }
 static OrderServiceBlockingStub newBlockingStub(Channel channel) { return null; } }
class Checkout {
 OrderServiceGrpc.OrderServiceBlockingStub stub = OrderServiceGrpc.newBlockingStub(channel);
 { stub.get(request); }
}""",
    )

    assert not facts.consumer_calls
    assert [item.reason_code for item in facts.unresolved] == ["MISSING_IMPLEMENTATION"]
    assert facts.unresolved[0].evidence_ids


def test_grpc_method_detector_marks_multiple_generated_service_factories_ambiguous(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
class Request {} class Response {} class Channel {}
class OrderServiceGrpc {
 static class OrderServiceBlockingStub { Response get(Request request) { return null; } }
 static OrderServiceBlockingStub newBlockingStub(Channel channel) { return null; } }
class OtherServiceGrpc { static OrderServiceGrpc.OrderServiceBlockingStub newBlockingStub(Channel channel) { return null; } }
class Checkout { OrderServiceGrpc.OrderServiceBlockingStub stub;
 Response call(Channel channel, Request request) {
  stub = OrderServiceGrpc.newBlockingStub(channel);
  stub = OtherServiceGrpc.newBlockingStub(channel);
  return stub.get(request); }
}""",
    )

    assert not facts.consumer_calls
    assert [item.reason_code for item in facts.unresolved] == ["AMBIGUOUS_TARGET"]


def test_workspace_method_plan_resolves_only_exact_grpc_signature(tmp_path: Path) -> None:
    provider = _detect(
        tmp_path / "provider",
        """package example.orders;
class Request {} class Response {} class StreamObserver<T> {}
class OrderServiceGrpc { abstract static class OrderServiceImplBase {} }
class Orders extends OrderServiceGrpc.OrderServiceImplBase {
 @Override public void get(Request request, StreamObserver<Response> observer) {} }""",
        "provider",
    )
    consumer = _detect(
        tmp_path / "consumer",
        """package example.orders;
class Request {} class Response {} class Channel {}
class OrderServiceGrpc { static class OrderServiceBlockingStub { Response get(Request request) { return null; } }
 static OrderServiceBlockingStub newBlockingStub(Channel channel) { return null; } }
class Checkout { OrderServiceGrpc.OrderServiceBlockingStub stub;
 Response checkout(Channel channel, Request request) { stub = OrderServiceGrpc.newBlockingStub(channel); return stub.get(request); } }""",
        "consumer",
    )
    generation = WorkspaceGeneration(
        "workspace",
        "gen-1",
        (
            WorkspaceRepositorySnapshot(
                "workspace",
                "provider",
                "main",
                "rev-1",
                WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, "https://example/provider"),
            ),
            WorkspaceRepositorySnapshot(
                "workspace",
                "consumer",
                "main",
                "rev-1",
                WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, "https://example/consumer"),
            ),
        ),
    )
    plan = MethodGraphWritePlan(MethodGraphScope("namespace", generation), (provider, consumer))

    call = consumer.consumer_calls[0]
    assert plan.operation_id_for(call.target_reference) == provider.operations[0].id
    mismatch = call.target_reference.replace("OrderServiceGrpc#Get", "OtherServiceGrpc#Get")
    assert mismatch != call.target_reference
    with pytest.raises(ValueError, match="ambiguous"):
        plan.operation_id_for(mismatch)
