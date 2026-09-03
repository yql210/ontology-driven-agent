from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .graph_plan import GraphRelation, GraphWritePlan


class GraphSink(Protocol):
    @property
    def graph_namespace(self) -> str: ...

    def write(self, plan: GraphWritePlan) -> None: ...

    def readback(self) -> GraphWritePlan: ...


@dataclass(frozen=True)
class WriteReceipt:
    confirmed: bool
    node_count: int
    relation_count: int
    readback: GraphWritePlan
    graph_namespace: str


class InMemoryGraphSink:
    def __init__(self, *, namespace: str) -> None:
        self._graph_namespace = namespace
        self._nodes = {}
        self._relations = {}

    @property
    def graph_namespace(self) -> str:
        return self._graph_namespace

    def write(self, plan: GraphWritePlan) -> None:
        self._nodes = {node.id: node for node in plan.nodes}
        self._relations = {relation.id: relation for relation in plan.relations}

    def readback(self) -> GraphWritePlan:
        return GraphWritePlan(
            tuple(sorted(self._nodes.values(), key=lambda node: node.id)),
            tuple(sorted(self._relations.values(), key=lambda relation: relation.id)),
        )


class GraphWriter:
    def __init__(self, sink: GraphSink) -> None:
        self._sink = sink

    def write(self, plan: GraphWritePlan) -> WriteReceipt:
        self._sink.write(plan)
        try:
            readback = self._sink.readback()
        except Exception:
            readback = GraphWritePlan((), ())
        namespace = self._sink.graph_namespace
        confirmed = (
            isinstance(namespace, str)
            and bool(namespace.strip())
            and readback == plan
            and self._matches(plan.relations, readback.relations)
        )
        return WriteReceipt(confirmed, len(readback.nodes), len(readback.relations), readback, namespace)

    @staticmethod
    def _matches(expected: tuple[GraphRelation, ...], actual: tuple[GraphRelation, ...]) -> bool:
        return expected == actual
