from __future__ import annotations

from pathlib import Path

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder
from ontoagent.parsing.service_graph.graph_writer import GraphWriter, InMemoryGraphSink
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


def _plan():
    batches = []
    for repo_id, revision in (("provider-orders", "p1"), ("consumer-checkout", "c1")):
        snapshot = RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
        facts = tuple(
            detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
        )
        batches.append(FactBatch(repo_id, revision, "generation-1", "main", facts))
    return GraphPlanBuilder().build(ServiceGraphResolver().resolve(tuple(batches)))


def test_in_memory_writer_returns_confirmed_exact_readback_receipt():
    plan = _plan()
    sink = InMemoryGraphSink()

    receipt = GraphWriter(sink).write(plan)

    assert receipt.confirmed
    assert receipt.node_count == len(plan.nodes)
    assert receipt.relation_count == len(plan.relations)
    assert receipt.readback == plan
    assert sink.readback() == plan
