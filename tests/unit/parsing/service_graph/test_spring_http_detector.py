from pathlib import Path

from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.models import RepositorySnapshot

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


def test_spring_detector_extracts_provider_and_consumer_fixture():
    detector = SpringHttpDetector()
    provider = detector.detect(
        RepositorySnapshot("provider-orders", "rev", FIXTURE / "provider-orders", frozenset({"java"}))
    )
    assert {e.canonical_key for e in provider.http_endpoints} == {
        "HTTP|GET|/orders/{id}|orders:Order",
        "HTTP|POST|/orders|orders:Order",
    }
    consumer = detector.detect(
        RepositorySnapshot("consumer-checkout", "rev", FIXTURE / "consumer-checkout", frozenset({"java", "yaml"}))
    )
    assert any(e.role == "consumer" for e in consumer.http_endpoints)
    assert any(u.reason_code == "DYNAMIC_URL" for u in consumer.unresolved)
    assert all(e.evidence_id in {x.id for x in consumer.evidences} for e in consumer.http_endpoints)
    webclient = [e for e in consumer.http_endpoints if e.client_kind == "WebClient"]
    assert {(e.method, e.raw_target) for e in webclient} == {
        ("GET", "http://orders.internal/orders/2"),
        ("POST", "http://orders.internal/orders"),
        ("PUT", "http://orders.internal/orders/2"),
        ("DELETE", "http://orders.internal/orders/2"),
    }
    config_ids = {e.id for e in consumer.evidences if e.evidence_type == "service_config"}
    assert all(config_ids == set(linked) for _, linked in consumer.endpoint_evidence_links)
    assert consumer.to_dict()["endpoint_evidence_links"]


def test_fixture_detection_is_byte_stable_and_registry_dispatches():
    registry = DetectorRegistry([SpringHttpDetector()])
    snap = RepositorySnapshot("isolated-catalog", "rev", FIXTURE / "isolated-catalog", frozenset({"java"}))
    assert registry.detect(snap).to_dict() == registry.detect(snap).to_dict()
    assert not registry.detect(snap).http_endpoints
