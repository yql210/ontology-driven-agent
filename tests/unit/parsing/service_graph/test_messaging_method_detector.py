from __future__ import annotations

from pathlib import Path

from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext, MethodDetector
from ontoagent.parsing.service_graph.method_graph_writer import MethodGraphScope, MethodGraphWritePlan
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.workspace.models import (
    WorkspaceGeneration,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


def _detect(tmp_path: Path, repo_id: str, source: str):
    path = tmp_path / "src/main/java/example/Messages.java"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    snapshot = RepositorySnapshot(repo_id, f"{repo_id}-rev", tmp_path, frozenset({"java"}))
    from ontoagent.parsing.service_graph.detectors.messaging_method import MessagingMethodDetector

    return MessagingMethodDetector().detect_methods(
        snapshot, MethodDetectionContext(repo_id, repo_id, repo_id, snapshot.source_revision, "gen-1")
    )


def _plan(*facts):
    snapshots = tuple(
        WorkspaceRepositorySnapshot(
            "workspace",
            fact.repo_id,
            "main",
            fact.source_revision,
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example/{fact.repo_id}"),
        )
        for fact in facts
    )
    return MethodGraphWritePlan(
        MethodGraphScope("namespace", WorkspaceGeneration("workspace", "gen-1", snapshots)), facts
    )


def test_messaging_method_detector_links_kafka_and_rabbit_producers_to_listener_methods(tmp_path: Path) -> None:
    provider = _detect(
        tmp_path / "provider",
        "provider",
        """package example;
        class Producer { KafkaTemplate<String, Object> kafka; RabbitTemplate rabbit;
          void publish(String id) { kafka.send("orders", id); rabbit.convertAndSend("orders.queue", "created", id); }
        }""",
    )
    consumer = _detect(
        tmp_path / "consumer",
        "consumer",
        """package example;
        class Consumer {
          @KafkaListener(topics = "orders", groupId = "checkout") void consume(String id) {}
          @RabbitListener(queues = "orders.queue", group = "workers") void receive(String id) {}
        }""",
    )
    plan = _plan(provider, consumer)

    assert isinstance(
        __import__(
            "ontoagent.parsing.service_graph.detectors.messaging_method", fromlist=["MessagingMethodDetector"]
        ).MessagingMethodDetector(),
        MethodDetector,
    )
    assert {item.declaring_interface_fqcn for item in consumer.operations} == {
        "messaging-operation:kafka|destination=orders|group=checkout",
        "messaging-operation:rabbitmq|destination=orders.queue|group=workers",
    }
    assert {plan.operation_id_for(call.target_reference) for call in provider.consumer_calls} == {
        item.id for item in consumer.operations
    }


def test_messaging_method_detector_links_kafka_and_rabbit_across_neutral_three_repo_fixture() -> None:
    from ontoagent.parsing.service_graph.detectors.messaging_method import MessagingMethodDetector

    detector = MessagingMethodDetector()
    provider_snapshot = RepositorySnapshot(
        "provider-orders", "fixture-provider-v1", FIXTURE / "provider-orders", frozenset({"java"})
    )
    consumer_snapshot = RepositorySnapshot(
        "consumer-checkout", "fixture-consumer-v1", FIXTURE / "consumer-checkout", frozenset({"java"})
    )
    provider = detector.detect_methods(
        provider_snapshot,
        MethodDetectionContext("provider-orders", "provider-orders", "provider-orders", "fixture-provider-v1", "gen-1"),
    )
    consumer = detector.detect_methods(
        consumer_snapshot,
        MethodDetectionContext(
            "consumer-checkout", "consumer-checkout", "consumer-checkout", "fixture-consumer-v1", "gen-1"
        ),
    )
    plan = _plan(provider, consumer)

    assert {call.target_reference for call in provider.consumer_calls} == {
        "messaging-operation:kafka|destination=order-events",
        "messaging-operation:rabbitmq|destination=order.queue",
    }
    assert {plan.operation_id_for(call.target_reference) for call in provider.consumer_calls} == {
        item.id
        for item in consumer.operations
        if item.declaring_interface_fqcn.endswith(("order-events|group=checkout", "order.queue|group=checkout-workers"))
    }


def test_messaging_method_detector_expands_literals_and_marks_dynamic_or_orphan_shapes_unresolved(
    tmp_path: Path,
) -> None:
    facts = _detect(
        tmp_path,
        "consumer",
        """package example;
        class Messages { KafkaTemplate<String, Object> kafka; RabbitTemplate rabbit;
          @KafkaListener(topics = {"orders", "payments"}, groupId = "checkout") void consume() {}
          @RabbitListener(queues = "${queue.name}") void broken() {}
          @KafkaListener() void malformed() {}
          void send() { kafka.send(topic, "x"); }
          void routed() { rabbit.convertAndSend("orders.queue", routingKey, "x"); }
          String helper(String value) { return value.toUpperCase(); }
          { kafka.send("orphan", "x"); }
        }""",
    )

    assert [item.operation_name for item in facts.operations] == ["consume", "consume"]
    assert not facts.consumer_calls
    assert {item.reason_code for item in facts.unresolved} >= {
        "DYNAMIC_TARGET",
        "MISSING_IMPLEMENTATION",
        "UNSUPPORTED_TARGET_SHAPE",
    }
    helper = next(item for item in facts.implementations if item.method_name == "helper")
    assert all(call.caller_implementation_id != helper.id for call in facts.consumer_calls)


def test_messaging_method_plan_rejects_group_mismatch_and_only_fans_out_exact_listener_group(tmp_path: Path) -> None:
    producer = _detect(
        tmp_path / "producer", "producer", 'class P { KafkaTemplate k; void send() { k.send("orders", "x"); } }'
    )
    matching = _detect(
        tmp_path / "matching",
        "matching",
        """class C { @KafkaListener(topics = "orders", groupId = "checkout") void one() {}
        @KafkaListener(topics = "orders", groupId = "checkout") void two() {} }""",
    )
    other_group = _detect(
        tmp_path / "other",
        "other",
        'class C { @KafkaListener(topics = "orders", groupId = "payments") void three() {} }',
    )
    exact_plan = _plan(producer, matching)
    plan = _plan(producer, matching, other_group)
    call = producer.consumer_calls[0]

    assert len(exact_plan.operation_ids_for(call.target_reference)) == 2
    assert not any(item.reason_code == "AMBIGUOUS_TARGET" for fact in exact_plan.facts for item in fact.unresolved)
    assert plan.operation_ids_for(call.target_reference) == ()
    mismatch = call.target_reference + "|group=missing"
    assert plan.operation_ids_for(mismatch) == ()
    assert any(item.reason_code == "IDENTITY_MISMATCH" for fact in plan.facts for item in fact.unresolved)
