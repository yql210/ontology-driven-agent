from __future__ import annotations

from pathlib import Path

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import GraphSink, GraphWriter, InMemoryGraphSink
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


def test_in_memory_writer_confirms_explicit_empty_plan() -> None:
    plan = GraphWritePlan((), ())

    receipt = GraphWriter(InMemoryGraphSink()).write(plan)

    assert receipt.confirmed
    assert receipt.node_count == 0
    assert receipt.relation_count == 0
    assert receipt.readback == plan


class ReadbackSink(GraphSink):
    def __init__(self, readback: GraphWritePlan | Exception) -> None:
        self._readback = readback

    def write(self, plan: GraphWritePlan) -> None:
        return None

    def readback(self) -> GraphWritePlan:
        if isinstance(self._readback, Exception):
            raise self._readback
        return self._readback


def test_writer_returns_unconfirmed_receipt_with_exact_mismatched_readback() -> None:
    plan = _plan()
    actual = GraphWritePlan(plan.nodes[:-1], plan.relations)

    receipt = GraphWriter(ReadbackSink(actual)).write(plan)

    assert not receipt.confirmed
    assert receipt.node_count == len(actual.nodes)
    assert receipt.relation_count == len(actual.relations)
    assert receipt.readback == actual


def test_writer_returns_unconfirmed_empty_receipt_when_readback_fails() -> None:
    receipt = GraphWriter(ReadbackSink(RuntimeError("backend unavailable"))).write(_plan())

    assert not receipt.confirmed
    assert receipt.node_count == 0
    assert receipt.relation_count == 0
    assert receipt.readback == GraphWritePlan((), ())
