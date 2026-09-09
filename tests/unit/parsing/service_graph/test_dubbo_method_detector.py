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


def _detect_xml(tmp_path: Path, files: dict[str, str], repo_id: str = "orders") -> object:
    for relative, source in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    snapshot = RepositorySnapshot(repo_id, "rev-1", tmp_path, frozenset({"java", "xml"}))
    return DubboMethodDetector().detect_methods(
        snapshot, MethodDetectionContext(repo_id, repo_id, repo_id, "rev-1", "gen-1")
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
    assert len(facts.retained_source_calls) == 1
    retained = facts.retained_source_calls[0]
    assert (retained.start_line, retained.start_column, retained.end_line, retained.end_column) == (5, 30, 5, 47)
    assert retained.receiver_declaration == '@DubboReference(group = "orders", version = "1.0") OrderApi orders'
    assert retained.receiver_type == "example.orders.OrderApi"
    assert retained.method_name == "find"
    assert retained.argument_summaries == ('"42"',)
    assert retained.argument_types == ("java.lang.String",)
    assert retained.protocol_settings == (("group", "orders"), ("version", "1.0"))
    assert retained.resolution_stage == "SOURCE_CAPTURE"
    assert retained.resolution_status == "CAPTURED"
    assert retained.resolution_reason is None
    assert len(retained.receiver_evidence_ids) == 1
    assert len(retained.argument_evidence_ids) == 1


@pytest.mark.parametrize(
    ("argument", "expected_type"),
    (
        ("id", "java.lang.String"),
        ("localId", "java.lang.String"),
        ("fieldId", "java.lang.String"),
        ("this.fieldId", "java.lang.String"),
        ('"literal"', "java.lang.String"),
        ("1", "int"),
        ("1L", "long"),
        ("(String) value", "java.lang.String"),
        ("new RequestDto()", "example.orders.RequestDto"),
    ),
)
def test_dubbo_method_detector_resolves_p0_source_argument_types(
    tmp_path: Path, argument: str, expected_type: str
) -> None:
    facts = _detect(
        tmp_path,
        f"""package example.orders;
import java.lang.String;
interface OrderApi {{
  String find(String id);
  String find(int id);
  String find(long id);
  String find(RequestDto request);
}}
class RequestDto {{}}
class Checkout {{
  String fieldId;
  @DubboReference OrderApi orders;
  String checkout(String id, Object value) {{
    String localId = "local";
    return orders.find({argument});
  }}
}}""",
    )

    assert len(facts.retained_source_calls) == 1
    retained = facts.retained_source_calls[0]
    assert retained.argument_types == (expected_type,)
    assert retained.resolution_status == "CAPTURED"
    assert len(facts.consumer_calls) == 1


def test_dubbo_method_detector_prefers_shadowing_local_over_class_field(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
interface OrderApi { String find(String id); }
class Checkout {
  Long id;
  @DubboReference OrderApi orders;
  String checkout(String ignored) {
    String id = "local";
    return orders.find(id);
  }
}""",
    )

    assert facts.retained_source_calls[0].argument_types == ("java.lang.String",)
    assert len(facts.consumer_calls) == 1


def test_dubbo_method_detector_resolves_explicit_import_and_rejects_star_import(tmp_path: Path) -> None:
    facts = _detect_xml(
        tmp_path,
        {
            "src/main/java/example/dto/RequestDto.java": "package example.dto; class RequestDto {}",
            "src/main/java/example/orders/Checkout.java": """package example.orders;
import example.dto.RequestDto;
interface OrderApi { String find(RequestDto request); }
class Checkout { @DubboReference OrderApi orders; String load() { return orders.find(new RequestDto()); } }""",
            "src/main/java/example/orders/StarCheckout.java": """package example.orders;
import example.dto.*;
interface StarApi { String find(Object request); }
class StarCheckout { @DubboReference StarApi orders; String load() { return orders.find(new RequestDto()); } }""",
        },
    )

    retained = {item.receiver_type: item for item in facts.retained_source_calls}
    assert retained["example.orders.OrderApi"].argument_types == ("example.dto.RequestDto",)
    assert retained["example.orders.StarApi"].argument_types == (None,)
    assert retained["example.orders.StarApi"].resolution_reason == "ARGUMENT_TYPE_UNKNOWN"


def test_dubbo_method_detector_ignores_call_shaped_comments_and_strings(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
interface OrderApi { String find(String id); }
class Checkout {
  @DubboReference OrderApi orders;
  String checkout() {
    // orders.find("comment");
    String text = "orders.find(string)";
    /* orders.find("block"); */
    return text;
  }
}""",
    )

    assert not facts.consumer_calls
    assert not facts.retained_source_calls


@pytest.mark.parametrize(
    "argument", ("value.toString()", "factory()", 'value + "suffix"', 'Class.forName("example.Dto")')
)
def test_dubbo_method_detector_retains_dynamic_argument_expressions_as_unknown(tmp_path: Path, argument: str) -> None:
    facts = _detect(
        tmp_path,
        f"""package example.orders;
interface OrderApi {{ String find(String id); }}
class Checkout {{
  @DubboReference OrderApi orders;
  String checkout(Object value) {{ return orders.find({argument}); }}
}}""",
    )

    retained = facts.retained_source_calls[0]
    assert retained.argument_types == (None,)
    assert retained.resolution_reason == "ARGUMENT_TYPE_UNKNOWN"
    assert not facts.consumer_calls


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
    retained = next(item for item in facts.retained_source_calls if item.method_name == "find")
    assert retained.receiver_type == "example.orders.OrderApi"
    assert retained.protocol_settings == (("group", "${orders.group}"), ("version", "1.0"))
    assert retained.resolution_status == "UNRESOLVED"
    assert retained.resolution_reason == "DYNAMIC_TARGET"


def test_dubbo_method_detector_retains_missing_contract_with_typed_parameter(tmp_path: Path) -> None:
    facts = _detect(
        tmp_path,
        """package example.orders;
class Checkout {
  @DubboReference(group = "orders", version = "1.0", alias = "primary") MissingApi orders;
  String checkout(Object id) { return orders.find(id); }
}""",
    )

    assert not facts.consumer_calls
    assert len(facts.retained_source_calls) == 1
    retained = facts.retained_source_calls[0]
    assert retained.receiver_declaration == (
        '@DubboReference(group = "orders", version = "1.0", alias = "primary") MissingApi orders'
    )
    assert retained.receiver_type == "example.orders.MissingApi"
    assert retained.argument_summaries == ("id",)
    assert retained.argument_types == ("java.lang.Object",)
    assert len(retained.argument_evidence_ids) == 1
    assert retained.protocol_settings == (("group", "orders"), ("version", "1.0"), ("alias", "primary"))
    assert retained.resolution_stage == "SOURCE_CAPTURE"
    assert retained.resolution_status == "UNRESOLVED"
    assert retained.resolution_reason == "CONTRACT_MISSING"


def test_dubbo_method_detector_links_literal_xml_provider_and_consumer_to_java_methods(tmp_path: Path) -> None:
    provider = _detect_xml(
        tmp_path / "provider",
        {
            "src/main/java/example/orders/OrderApi.java": """package example.orders;
interface OrderApi { String find(String id); String find(long id); }""",
            "src/main/java/example/orders/OrderService.java": """package example.orders;
class OrderService implements OrderApi {
  public String find(String id) { return id; }
  public String find(long id) { return \"\"; }
}""",
            "src/main/resources/dubbo-provider.xml": """<beans xmlns:dubbo="http://dubbo.apache.org/schema/dubbo">
  <bean id="orderService" class="example.orders.OrderService" />
  <dubbo:service interface="example.orders.OrderApi" ref="orderService" group="orders" version="1.0" />
</beans>""",
        },
        "provider",
    )
    consumer = _detect_xml(
        tmp_path / "consumer",
        {
            "src/main/java/example/orders/OrderApi.java": """package example.orders;
interface OrderApi { String find(String id); String find(long id); }""",
            "src/main/java/example/checkout/Checkout.java": """package example.checkout;
import example.orders.OrderApi;
class Checkout {
  private OrderApi orders;
  String load() { return orders.find(\"42\"); }
  String load(long id) { return orders.find(42L); }
}""",
            "src/main/resources/dubbo-consumer.xml": """<beans xmlns:dubbo="http://dubbo.apache.org/schema/dubbo">
  <dubbo:reference id="orders" interface="example.orders.OrderApi" group="orders" version="1.0" />
</beans>""",
        },
        "consumer",
    )

    assert {item.canonical_signature for item in provider.operations} == {
        "example.orders.OrderApi#find(java.lang.String):java.lang.String",
        "example.orders.OrderApi#find(long):java.lang.String",
    }
    assert len(provider.implementations) == len(provider.bindings) == 2
    assert {item.binding_identity for item in provider.operations} == {"xml-service-ref:orderService"}
    assert all(
        {evidence.file_path for evidence in provider.evidences if evidence.id in operation.evidence_ids}
        == {"src/main/java/example/orders/OrderService.java", "src/main/resources/dubbo-provider.xml"}
        for operation in provider.operations
    )
    assert {item.target_reference for item in consumer.consumer_calls} == {
        "dubbo-operation:example.orders.OrderApi#find(java.lang.String):java.lang.String|group=orders|version=1.0"
        "|alias=|origin=xml|xml-reference-id=orders",
        "dubbo-operation:example.orders.OrderApi#find(long):java.lang.String|group=orders|version=1.0"
        "|alias=|origin=xml|xml-reference-id=orders",
    }
    assert len(consumer.retained_source_calls) == 2
    assert {item.receiver_declaration for item in consumer.retained_source_calls} == {"private OrderApi orders;"}
    assert all(len(item.receiver_evidence_ids) == 2 for item in consumer.retained_source_calls)


def test_dubbo_method_detector_marks_xml_placeholder_mismatch_and_malformed_declarations_unresolved(
    tmp_path: Path,
) -> None:
    facts = _detect_xml(
        tmp_path,
        {
            "src/main/java/example/orders/OrderApi.java": """package example.orders;
interface OrderApi { String find(String id); }""",
            "src/main/java/example/orders/OrderService.java": """package example.orders;
class OrderService implements OrderApi { public String find(String id) { return id; } }""",
            "src/main/java/example/checkout/Checkout.java": """package example.checkout;
import example.orders.OrderApi;
class Checkout {
  private OrderApi orders;
  private OrderApi mismatch;
  String load() { return orders.find(\"42\"); }
  String mismatch() { return mismatch.find(\"42\"); }
}""",
            "src/main/resources/dubbo.xml": """<beans xmlns:dubbo="http://dubbo.apache.org/schema/dubbo">
  <dubbo:reference id="orders" interface="example.orders.OrderApi" group="${orders.group}" version="1.0" />
  <dubbo:reference id="mismatch" interface="example.orders.OtherApi" group="orders" version="1.0" />
  <bean id="duplicate" class="example.orders.OrderService" />
  <bean id="duplicate" class="example.orders.OtherOrderService" />
  <dubbo:service interface="example.orders.OrderApi" ref="missing" group="orders" version="1.0" />
  <dubbo:service interface="example.orders.OrderApi" ref="duplicate" group="orders" version="1.0" />
</beans>""",
            "src/main/resources/broken.xml": '<beans><dubbo:service interface="example.orders.OrderApi"',
            "src/main/resources/ordinary.xml": '<beans><service interface="example.orders.OrderApi" /></beans>',
        },
    )

    assert not facts.operations
    assert not facts.consumer_calls
    assert {item.reason_code for item in facts.unresolved} >= {
        "AMBIGUOUS_TARGET",
        "DYNAMIC_TARGET",
        "MISSING_DECLARATION",
        "UNSUPPORTED_TARGET_SHAPE",
    }
    assert any(
        {evidence.file_path for evidence in facts.evidences if evidence.id in item.evidence_ids}
        == {"src/main/resources/dubbo.xml", "src/main/java/example/checkout/Checkout.java"}
        for item in facts.unresolved
        if item.reason_code == "DYNAMIC_TARGET"
    )
    assert any(
        {evidence.file_path for evidence in facts.evidences if evidence.id in item.evidence_ids}
        == {"src/main/resources/dubbo.xml", "src/main/java/example/checkout/Checkout.java"}
        for item in facts.unresolved
        if item.subject == "mismatch"
    )
    assert all("ordinary.xml" not in evidence.file_path for evidence in facts.evidences)


def test_workspace_method_plan_does_not_cross_link_xml_call_to_annotation_operation(tmp_path: Path) -> None:
    xml_provider = _detect_xml(
        tmp_path / "xml-provider",
        {
            "src/main/java/example/orders/OrderApi.java": """package example.orders;
interface OrderApi { String find(String id); }""",
            "src/main/java/example/orders/OrderService.java": """package example.orders;
class OrderService implements OrderApi { public String find(String id) { return id; } }""",
            "src/main/resources/dubbo-provider.xml": """<beans xmlns:dubbo="http://dubbo.apache.org/schema/dubbo">
  <bean id="orderService" class="example.orders.OrderService" />
  <dubbo:service interface="example.orders.OrderApi" ref="orderService" group="orders" version="1.0" />
</beans>""",
        },
        "xml-provider",
    )
    xml_consumer = _detect_xml(
        tmp_path / "xml-consumer",
        {
            "src/main/java/example/orders/OrderApi.java": """package example.orders;
interface OrderApi { String find(String id); }""",
            "src/main/java/example/checkout/Checkout.java": """package example.checkout;
import example.orders.OrderApi;
class Checkout { private OrderApi orderApi; String load() { return orderApi.find("42"); } }""",
            "src/main/resources/dubbo-consumer.xml": """<beans xmlns:dubbo="http://dubbo.apache.org/schema/dubbo">
  <dubbo:reference id="orderApi" interface="example.orders.OrderApi" group="orders" version="1.0" />
</beans>""",
        },
        "xml-consumer",
    )
    annotation_provider = _detect_xml(
        tmp_path / "annotation-provider",
        {
            "src/main/java/example/orders/OrderApi.java": """package example.orders;
interface OrderApi { String find(String id); }""",
            "src/main/java/example/orders/OrderService.java": """package example.orders;
@DubboService(interfaceClass = OrderApi.class, group = "orders", version = "1.0")
class OrderService implements OrderApi { public String find(String id) { return id; } }""",
        },
        "annotation-provider",
    )
    generation = WorkspaceGeneration(
        "workspace",
        "gen-1",
        tuple(
            WorkspaceRepositorySnapshot(
                "workspace",
                repo_id,
                "main",
                "rev-1",
                WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example/{repo_id}"),
            )
            for repo_id in ("xml-provider", "xml-consumer", "annotation-provider")
        ),
    )
    plan = MethodGraphWritePlan(
        MethodGraphScope("namespace", generation), (annotation_provider, xml_consumer, xml_provider)
    )
    call = xml_consumer.consumer_calls[0]

    assert plan.operation_ids_for(call.target_reference) == (xml_provider.operations[0].id,)


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
