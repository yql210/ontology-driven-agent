from __future__ import annotations

from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext, MethodDetector
from ontoagent.parsing.service_graph.detectors.dubbo_method import DubboMethodDetector
from ontoagent.parsing.service_graph.method_graph_writer import MethodGraphScope, MethodGraphWritePlan
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.workspace.models import (
    WorkspaceGeneration,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)


def _detect(tmp_path: Path, source: str) -> object:
    path = tmp_path / "src/main/java/example/orders/OrderService.java"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    snapshot = RepositorySnapshot("orders", "rev-1", tmp_path, frozenset({"java"}))
    return DubboMethodDetector().detect_methods(
        snapshot, MethodDetectionContext("orders", "orders", "orders", "rev-1", "gen-1")
    )


def test_dubbo_method_detector_emits_provider_operations_bindings_and_overloads(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
interface OrderApi { String find(String id); String find(long id); }
@DubboService(interfaceClass = OrderApi.class, group = "orders", version = "1.0", alias = "primary")
class OrderService implements OrderApi {
  public String find(String id) { return id; }
  public String find(long id) { return ""; }
}""",
    )

    assert isinstance(DubboMethodDetector(), MethodDetector)
    assert {item.canonical_signature for item in facts.operations} == {
        "example.orders.OrderApi#find(java.lang.String):java.lang.String",
        "example.orders.OrderApi#find(long):java.lang.String",
    }
    assert {(item.group, item.version, item.alias) for item in facts.operations} == {("orders", "1.0", "primary")}
    assert len(facts.implementations) == len(facts.bindings) == 2
    assert all(item.implementation_id for item in facts.bindings)


def test_dubbo_method_detector_maps_proxy_call_to_enclosing_implementation(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
interface OrderApi { String find(String id); }
class Checkout {
  @DubboReference(group = "orders", version = "1.0") OrderApi orders;
  String checkout() { return orders.find("42"); }
  String helper(String value) { return value.toUpperCase(); }
}""",
    )

    assert len(facts.consumer_calls) == 1
    call = facts.consumer_calls[0]
    assert call.target_kind == "operation"
    assert call.caller_implementation_id == next(
        item.id for item in facts.implementations if item.method_name == "checkout"
    )
    assert "OrderApi#find(java.lang.String):java.lang.String" in call.target_reference
    assert not facts.unresolved


def test_dubbo_method_detector_marks_dynamic_and_orphan_proxy_calls_unresolved(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
interface OrderApi { String find(String id); }
class Checkout {
  @DubboReference(group = "${orders.group}", version = "1.0") OrderApi dynamic;
  @DubboReference(group = "orders", version = "1.0") OrderApi orders;
  String checkout(String id) { return dynamic.find(id); }
  { orders.find("42"); }
}""",
    )

    assert not facts.consumer_calls
    assert {item.reason_code for item in facts.unresolved} >= {"DYNAMIC_TARGET", "MISSING_IMPLEMENTATION"}
    assert all(item.evidence_ids for item in facts.unresolved)


def test_workspace_method_plan_resolves_only_exact_dubbo_signature_and_metadata(tmp_path: Path) -> None:
    provider = _detect(
        tmp_path / "provider",
        """package example.orders;
interface OrderApi { String find(String id); }
@DubboService(interfaceClass = OrderApi.class, group = "orders", version = "1.0")
class OrderService implements OrderApi { public String find(String id) { return id; } }""",
    )
    consumer = _detect(
        tmp_path / "consumer",
        """package example.orders;
interface OrderApi { String find(String id); }
class Checkout {
  @DubboReference(group = "orders", version = "1.0") OrderApi orders;
  String checkout() { return orders.find("42"); }
}""",
    )
    # Re-run under the consumer identity so generated implementation and evidence IDs stay coherent.
    snapshot = RepositorySnapshot("consumer", "rev-2", tmp_path / "consumer", frozenset({"java"}))
    consumer = DubboMethodDetector().detect_methods(
        snapshot, MethodDetectionContext("consumer", "consumer", "consumer", "rev-2", "gen-1")
    )
    generation = WorkspaceGeneration(
        "workspace",
        "gen-1",
        (
            WorkspaceRepositorySnapshot(
                "workspace",
                "orders",
                "main",
                "rev-1",
                WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, "https://example/orders"),
            ),
            WorkspaceRepositorySnapshot(
                "workspace",
                "consumer",
                "main",
                "rev-2",
                WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, "https://example/consumer"),
            ),
        ),
    )
    plan = MethodGraphWritePlan(MethodGraphScope("namespace", generation), (provider, consumer))
    call = consumer.consumer_calls[0]

    assert plan.operation_id_for(call.target_reference) == provider.operations[0].id
    with pytest.raises(ValueError, match="ambiguous"):
        plan.operation_id_for(call.target_reference.replace("group=orders", "group=other"))
    mismatch_call = call.__class__(
        call.repo_id,
        call.module_id,
        call.service_id,
        call.source_revision,
        call.generation_id,
        call.caller_implementation_id,
        call.target_reference.replace("group=orders", "group=other"),
        call.target_kind,
        call.evidence_ids,
    )
    mismatch_facts = consumer.__class__(
        consumer.detector_id,
        consumer.detector_version,
        consumer.repo_id,
        consumer.source_revision,
        consumer.generation_id,
        consumer.operations,
        consumer.implementations,
        (mismatch_call,),
        consumer.bindings,
        consumer.evidences,
        consumer.unresolved,
    )
    mismatch_plan = MethodGraphWritePlan(MethodGraphScope("mismatch", generation), (provider, mismatch_facts))
    assert any(item.reason_code == "IDENTITY_MISMATCH" for fact in mismatch_plan.facts for item in fact.unresolved)
