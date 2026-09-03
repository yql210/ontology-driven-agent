from __future__ import annotations

from pathlib import Path

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"
REVISIONS = {
    "provider-orders": "fixture-provider-v1",
    "consumer-checkout": "fixture-consumer-v1",
    "isolated-catalog": "fixture-isolated-v1",
}


def _batch(repo_id: str, generation_id: str | None = "generation-1") -> FactBatch:
    snapshot = RepositorySnapshot(repo_id, REVISIONS[repo_id], FIXTURE / repo_id, frozenset({"java", "yaml"}))
    facts = tuple(
        detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
    )
    return FactBatch(repo_id, REVISIONS[repo_id], generation_id, "main", facts)


def test_resolver_matches_three_protocols_exactly_and_is_stable():
    resolver = ServiceGraphResolver()
    batches = tuple(_batch(repo_id) for repo_id in REVISIONS)

    result = resolver.resolve(batches)

    assert {(link.protocol, link.match_rule) for link in result.resolved_links} >= {
        ("HTTP", "exact_http_match_key"),
        ("DUBBO", "exact_dubbo_canonical_key"),
        ("MQ", "exact_mq_destination_key"),
    }
    assert all(link.provider_repo_id == "provider-orders" for link in result.resolved_links)
    assert all(link.consumer_repo_id == "consumer-checkout" for link in result.resolved_links)
    assert all(link.confidence == 1.0 and link.evidence_ids for link in result.resolved_links)
    assert all(
        "isolated-catalog" not in (link.provider_repo_id, link.consumer_repo_id) for link in result.resolved_links
    )
    assert result == resolver.resolve(batches)


def test_resolver_is_fail_closed_for_missing_generation_and_missing_provider():
    resolver = ServiceGraphResolver()
    missing_generation = resolver.resolve((_batch("provider-orders"), _batch("consumer-checkout", None)))
    assert "MISSING_GENERATION" in {item.reason_code for item in missing_generation.unresolved}

    missing_provider_generation = resolver.resolve((_batch("provider-orders", None), _batch("consumer-checkout")))
    assert "MISSING_GENERATION" in {item.reason_code for item in missing_provider_generation.unresolved}

    provider_only = resolver.resolve((_batch("provider-orders"),))
    assert "NO_CONSUMER_MATCH" in {item.reason_code for item in provider_only.unresolved}

    without_provider = resolver.resolve((_batch("consumer-checkout"), _batch("isolated-catalog")))
    assert "NO_PROVIDER_MATCH" in {item.reason_code for item in without_provider.unresolved}
    assert all(item.reason_code != "UNSUPPORTED_CALL_SHAPE" for item in without_provider.unresolved)


def test_resolver_uses_http_alias_and_mq_destination_not_fact_identity():
    result = ServiceGraphResolver().resolve((_batch("provider-orders"), _batch("consumer-checkout")))
    http_link = next(link for link in result.resolved_links if link.protocol == "HTTP")
    mq_link = next(link for link in result.resolved_links if link.protocol == "MQ")
    assert http_link.match_key.endswith("|orders")
    assert "|checkout" not in mq_link.match_key
    assert mq_link.consumer_group == "checkout"
