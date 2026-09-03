from __future__ import annotations

import json
from pathlib import Path

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.models import RepositorySnapshot

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


_REVISIONS = {
    "provider-orders": "fixture-provider-v1",
    "consumer-checkout": "fixture-consumer-v1",
    "isolated-catalog": "fixture-isolated-v1",
}


def _snapshot(repo_id: str) -> RepositorySnapshot:
    return RepositorySnapshot(
        repo_id,
        _REVISIONS[repo_id],
        FIXTURE / repo_id,
        frozenset({"java"}),
    )


def _assert_evidence_references(facts) -> None:
    evidence_ids = {evidence.id for evidence in facts.evidences}
    referenced = {
        *(endpoint.evidence_id for endpoint in facts.rpc_endpoints),
        *(endpoint.evidence_id for endpoint in facts.message_endpoints),
        *(item.evidence_id for item in facts.unresolved),
    }
    assert referenced
    assert referenced <= evidence_ids
    assert all(evidence.repo_id == facts.repo_id for evidence in facts.evidences)


def test_i2_neutral_three_repo_dubbo_fixture_isolated_and_stable():
    detector = DubboDetector()
    results = {repo_id: detector.detect(_snapshot(repo_id)) for repo_id in _REVISIONS}

    provider = results["provider-orders"]
    provider_endpoints = {
        (endpoint.role, endpoint.interface_name, endpoint.method) for endpoint in provider.rpc_endpoints
    }
    assert {
        ("provider", "example.orders.OrderApi", "getOrder"),
        ("provider", "example.orders.OrderApi", "cancelOrder"),
    } <= provider_endpoints
    assert {endpoint.group for endpoint in provider.rpc_endpoints} == {"orders"}
    assert {endpoint.version for endpoint in provider.rpc_endpoints} == {"1.0"}

    consumer = results["consumer-checkout"]
    assert any(
        endpoint.role == "consumer"
        and endpoint.interface_name == "example.orders.OrderApi"
        and endpoint.method == "getOrder"
        for endpoint in consumer.rpc_endpoints
    )
    assert any(item.reason_code == "UNSUPPORTED_CALL_SHAPE" for item in consumer.unresolved)

    isolated = results["isolated-catalog"]
    assert any(
        endpoint.interface_name == "example.catalog.CatalogApi" and endpoint.method == "lookup"
        for endpoint in isolated.rpc_endpoints
    )
    assert not any(endpoint.interface_name == "example.orders.OrderApi" for endpoint in isolated.rpc_endpoints)

    for facts in results.values():
        _assert_evidence_references(facts)
        assert json.dumps(facts.to_dict(), sort_keys=True) == json.dumps(facts.to_dict(), sort_keys=True)


def test_i2_neutral_three_repo_messaging_fixture_has_static_dynamic_and_isolated_shapes():
    detector = MessagingDetector()
    results = {repo_id: detector.detect(_snapshot(repo_id)) for repo_id in _REVISIONS}

    provider = results["provider-orders"]
    assert {
        (
            endpoint.broker,
            endpoint.role,
            endpoint.topic_or_queue,
        )
        for endpoint in provider.message_endpoints
    } >= {
        ("kafka", "producer", "order-events"),
        ("rabbitmq", "producer", "order.exchange"),
    }

    consumer = results["consumer-checkout"]
    assert {
        (
            endpoint.broker,
            endpoint.role,
            endpoint.topic_or_queue,
            endpoint.consumer_group,
        )
        for endpoint in consumer.message_endpoints
    } >= {
        ("kafka", "consumer", "order-events", "checkout"),
        ("kafka", "consumer", "payments", "checkout"),
        ("rabbitmq", "consumer", "order.queue", "checkout-workers"),
        ("rabbitmq", "consumer", "audit.queue", "checkout-workers"),
    }
    assert any(item.reason_code == "DYNAMIC_URL" for item in consumer.unresolved)

    isolated = results["isolated-catalog"]
    assert {
        (
            endpoint.broker,
            endpoint.role,
            endpoint.topic_or_queue,
        )
        for endpoint in isolated.message_endpoints
    } >= {
        ("kafka", "producer", "catalog-events"),
        ("rabbitmq", "producer", "catalog.exchange"),
    }
    assert not any(endpoint.topic_or_queue == "order-events" for endpoint in isolated.message_endpoints)

    for facts in results.values():
        _assert_evidence_references(facts)
        assert json.dumps(facts.to_dict(), sort_keys=True) == json.dumps(facts.to_dict(), sort_keys=True)
