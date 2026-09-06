from __future__ import annotations

from pathlib import Path

from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.models import RepositorySnapshot

CONFIGURED_FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/configured_messaging_three_repo"


def test_messaging_detector_expands_annotations_and_static_producers(tmp_path):
    source = """
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
class Messages {
  KafkaTemplate<String, Object> kafka;
  RabbitTemplate rabbit;
  @KafkaListener(topics = {"orders", "payments"}, groupId = "checkout") void consume() {}
  @RabbitListener(queues = "orders.queue", group = "worker") void receive() {}
  void send(Object payload) {
    kafka.send("events", payload);
    rabbit.convertAndSend("exchange", "route", payload);
  }
}
"""
    (tmp_path / "Messages.java").write_text(source, encoding="utf-8")
    facts = MessagingDetector().detect(RepositorySnapshot("repo", "rev", tmp_path, frozenset({"java"})))
    assert sorted((x.broker, x.role, x.topic_or_queue, x.consumer_group) for x in facts.message_endpoints) == sorted(
        [
            ("kafka", "consumer", "orders", "checkout"),
            ("kafka", "consumer", "payments", "checkout"),
            ("kafka", "producer", "events", "-"),
            ("rabbitmq", "consumer", "orders.queue", "worker"),
            ("rabbitmq", "producer", "exchange", "-"),
        ]
    )
    assert not facts.unresolved


def test_messaging_detector_marks_dynamic_and_empty_targets_unresolved(tmp_path):
    source = """
class Messages {
  KafkaTemplate kafka;
  @KafkaListener(topics = topicName) void consume() {}
  @RabbitListener(queues = {}) void receive() {}
  void send(Object p) { kafka.send(topicName, p); }
}
"""
    (tmp_path / "Messages.java").write_text(source, encoding="utf-8")
    facts = MessagingDetector().detect(RepositorySnapshot("repo", "rev", tmp_path, frozenset({"java"})))
    assert len(facts.unresolved) == 3
    assert {x.reason_code for x in facts.unresolved} == {"UNSUPPORTED_CALL_SHAPE", "DYNAMIC_URL"}


def test_messaging_detector_covers_every_configured_workspace_snapshot() -> None:
    revisions = {
        "provider-orders": "fixture-provider-v1",
        "consumer-checkout": "fixture-consumer-v1",
        "isolated-catalog": "fixture-isolated-v1",
    }

    facts = {
        repo_id: MessagingDetector().detect(
            RepositorySnapshot(repo_id, revision, CONFIGURED_FIXTURE / repo_id, frozenset({"java", "yaml"}))
        )
        for repo_id, revision in revisions.items()
    }

    assert {repo_id for repo_id, fact in facts.items() if fact.message_endpoints} == set(revisions)
