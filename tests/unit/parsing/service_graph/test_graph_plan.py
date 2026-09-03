from __future__ import annotations

from pathlib import Path

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


def _batch(repo_id: str, revision: str) -> FactBatch:
    snapshot = RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
    facts = tuple(
        detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
    )
    return FactBatch(repo_id, revision, "generation-1", "main", facts)


def test_graph_plan_is_deterministic_and_preserves_cross_repo_provenance():
    result = ServiceGraphResolver().resolve((_batch("provider-orders", "p1"), _batch("consumer-checkout", "c1")))
    builder = GraphPlanBuilder()

    plan = builder.build(result)

    assert plan == builder.build(result)
    assert {node.node_type for node in plan.nodes} == {"ServiceDefinition", "Endpoint", "Evidence"}
    assert {relation.relation_type for relation in plan.relations} >= {
        "PROVIDES_ENDPOINT",
        "CONSUMES_ENDPOINT",
        "DEPENDS_ON",
        "SUPPORTED_BY_EVIDENCE",
    }
    assert all(
        {"id", "repo_id", "source_revision", "generation_id", "protocol", "canonical_key", "role", "evidence_ids"}
        <= node.props.keys()
        for node in plan.nodes
    )
    dependencies = [relation for relation in plan.relations if relation.relation_type == "DEPENDS_ON"]
    assert dependencies
    assert all(relation.props["provider_repo_id"] == "provider-orders" for relation in dependencies)
    assert all(relation.props["consumer_repo_id"] == "consumer-checkout" for relation in dependencies)
    assert all(relation.props["evidence_ids"] for relation in dependencies)
