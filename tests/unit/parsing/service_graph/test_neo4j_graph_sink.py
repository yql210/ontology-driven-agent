from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.graph_plan import GraphNode, GraphPlanBuilder, GraphRelation, GraphWritePlan
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.neo4j_graph_sink import Neo4jGraphSink
from ontoagent.parsing.service_graph.resolver import FactBatch, ServiceGraphResolver

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/neutral_three_repo"


class FakeSession:
    def __init__(self, driver: StatefulFakeDriver) -> None:
        self._driver = driver

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def run(self, query: str, **parameters: object) -> list[dict[str, object]]:
        self._driver.calls.append((query, parameters))
        if query == Neo4jGraphSink.READ_NODES_QUERY:
            return self._driver.read_nodes(parameters)
        if query == Neo4jGraphSink.READ_RELATIONS_QUERY:
            return self._driver.read_relations(parameters)
        self._driver.apply_write(query, parameters)
        return []


class StatefulFakeDriver:
    """Fake Neo4j driver whose reads are derived solely from prior write rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.session_calls = 0
        self.nodes: dict[tuple[str, str], dict[str, object]] = {}
        self.relations: dict[tuple[str, str], dict[str, object]] = {}
        self.node_row_override: Callable[[list[dict[str, object]]], list[dict[str, object]]] | None = None
        self.relation_row_override: Callable[[list[dict[str, object]]], list[dict[str, object]]] | None = None

    def session(self) -> FakeSession:
        self.session_calls += 1
        return FakeSession(self)

    def apply_write(self, query: str, parameters: dict[str, object]) -> None:
        rows = parameters["rows"]
        namespace = parameters["namespace"]
        assert isinstance(rows, list)
        assert isinstance(namespace, str)
        if query in Neo4jGraphSink._NODE_QUERIES.values():
            label = next(label for label, statement in Neo4jGraphSink._NODE_QUERIES.items() if statement == query)
            for row in rows:
                assert isinstance(row, dict)
                self.nodes[(namespace, row["id"])] = {
                    "labels": [label],
                    "properties": {
                        "id": row["id"],
                        "_ontoagent_props": row["encoded_props"],
                        "_ontoagent_namespace": namespace,
                    },
                }
            return
        if query in Neo4jGraphSink._RELATION_QUERIES.values():
            relation_type = next(
                kind for kind, statement in Neo4jGraphSink._RELATION_QUERIES.items() if statement == query
            )
            for row in rows:
                assert isinstance(row, dict)
                self.relations[(namespace, row["id"])] = {
                    "type": relation_type,
                    "source_id": row["source_id"],
                    "target_id": row["target_id"],
                    "source_namespace": namespace,
                    "target_namespace": namespace,
                    "properties": {
                        "_ontoagent_relation_id": row["id"],
                        "_ontoagent_props": row["encoded_props"],
                        "_ontoagent_namespace": namespace,
                    },
                }
            return
        raise AssertionError("unexpected write query")

    def read_nodes(self, parameters: dict[str, object]) -> list[dict[str, object]]:
        namespace = parameters["namespace"]
        node_ids = parameters["node_ids"]
        assert isinstance(namespace, str)
        assert isinstance(node_ids, tuple)
        rows = [self.nodes[(namespace, node_id)] for node_id in node_ids if (namespace, node_id) in self.nodes]
        return self.node_row_override(rows) if self.node_row_override else rows

    def read_relations(self, parameters: dict[str, object]) -> list[dict[str, object]]:
        namespace = parameters["namespace"]
        relation_ids = parameters["relation_ids"]
        assert isinstance(namespace, str)
        assert isinstance(relation_ids, tuple)
        rows = [
            self.relations[(namespace, relation_id)]
            for relation_id in relation_ids
            if (namespace, relation_id) in self.relations
        ]
        return self.relation_row_override(rows) if self.relation_row_override else rows


def _plan() -> GraphWritePlan:
    node_provenance = {
        "repo_id": "repo",
        "source_revision": "revision",
        "generation_id": "generation",
        "canonical_key": "canonical",
        "evidence_ids": ("e1",),
    }
    relation_provenance = {
        "source_revision": "revision",
        "generation_id": "generation",
        "canonical_key": "canonical",
        "evidence_ids": ("e1",),
    }
    return GraphWritePlan(
        nodes=(
            GraphNode(
                "service",
                "ServiceDefinition",
                {"id": "service", "optional": None, **node_provenance},
            ),
            GraphNode("endpoint", "Endpoint", {"id": "endpoint", "nested": (None, "value"), **node_provenance}),
            GraphNode("evidence", "Evidence", {"id": "evidence", "source": None, **node_provenance}),
        ),
        relations=(
            GraphRelation("r1", "PROVIDES_ENDPOINT", "service", "endpoint", {"none": None, **relation_provenance}),
            GraphRelation("r2", "PROVIDES_ENDPOINT", "service", "endpoint", {"none": None, **relation_provenance}),
            GraphRelation("r3", "SUPPORTED_BY_EVIDENCE", "endpoint", "evidence", {"none": None, **relation_provenance}),
        ),
    )


def _neutral_three_repo_plan() -> GraphWritePlan:
    batches = []
    for repo_id, revision in (("provider-orders", "p1"), ("consumer-checkout", "c1"), ("isolated-inventory", "i1")):
        snapshot = RepositorySnapshot(repo_id, revision, FIXTURE / repo_id, frozenset({"java", "yaml"}))
        facts = tuple(
            detector.detect(snapshot) for detector in (SpringHttpDetector(), DubboDetector(), MessagingDetector())
        )
        batches.append(FactBatch(repo_id, revision, "generation-1", "main", facts))
    return GraphPlanBuilder().build(ServiceGraphResolver().resolve(tuple(batches)))


def test_neo4j_sink_writes_and_reads_exact_plan_from_stateful_driver() -> None:
    plan = _plan()
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver, namespace="test")

    sink.write(plan)

    assert sink.readback() == plan
    assert set(driver.nodes) == {("test", node.id) for node in plan.nodes}
    assert set(driver.relations) == {("test", relation.id) for relation in plan.relations}
    encoded_rows = [params["rows"] for _, params in driver.calls if "rows" in params]
    assert all(isinstance(row, list) and row and "encoded_props" in row[0] for row in encoded_rows)
    write_queries = [query for query, params in driver.calls if "rows" in params]
    assert all("$rows" in query for query in write_queries)
    assert all("service" not in query and "r1" not in query for query in write_queries)
    assert all("$node_ids" in query or "$relation_ids" in query for query, _ in driver.calls if "RETURN" in query)


def test_neo4j_sink_readback_is_scoped_to_written_namespace_and_ids() -> None:
    plan = _plan()
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver, namespace="test")
    sink.write(plan)
    driver.nodes[("other", "foreign")] = {"labels": ["Endpoint"], "properties": {}}
    driver.relations[("other", "foreign")] = {"type": "DEPENDS_ON", "properties": {}}

    assert sink.readback() == plan


def test_neo4j_sink_empty_submitted_plan_round_trips() -> None:
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver, namespace="test")
    plan = GraphWritePlan((), ())

    sink.write(plan)

    assert sink.readback() == plan


def test_neo4j_sink_writes_neutral_three_repo_plan_with_provider_consumer_provenance() -> None:
    plan = _neutral_three_repo_plan()
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver, namespace="test")

    sink.write(plan)

    assert sink.readback() == plan


@pytest.mark.parametrize(
    "invalid_plan",
    [
        lambda plan: replace(plan, nodes=(replace(plan.nodes[0], props={**plan.nodes[0].props, "id": "other"}),)),
        lambda plan: replace(
            plan,
            nodes=(
                replace(
                    plan.nodes[0], props={key: value for key, value in plan.nodes[0].props.items() if key != "repo_id"}
                ),
            ),
        ),
        lambda plan: replace(plan, nodes=(replace(plan.nodes[0], props={**plan.nodes[0].props, "generation_id": ""}),)),
        lambda plan: replace(
            plan, nodes=(replace(plan.nodes[0], props={**plan.nodes[0].props, "source_revision": None}),)
        ),
        lambda plan: replace(
            plan, nodes=(replace(plan.nodes[0], props={**plan.nodes[0].props, "canonical_key": " "}),)
        ),
        lambda plan: replace(plan, nodes=(replace(plan.nodes[0], props={**plan.nodes[0].props, "evidence_ids": ()}),)),
        lambda plan: replace(
            plan, nodes=(replace(plan.nodes[0], props={**plan.nodes[0].props, "evidence_ids": ["e1"]}),)
        ),
        lambda plan: replace(
            plan, nodes=(replace(plan.nodes[0], props={**plan.nodes[0].props, "evidence_ids": (" ",)}),)
        ),
    ],
)
def test_neo4j_sink_rejects_invalid_node_provenance_before_opening_driver_session(
    invalid_plan: Callable[[GraphWritePlan], GraphWritePlan],
) -> None:
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver)

    with pytest.raises(ValueError):
        sink.write(invalid_plan(GraphWritePlan((_plan().nodes[0],), ())))

    assert driver.session_calls == 0
    assert driver.calls == []


@pytest.mark.parametrize(
    "invalid_plan",
    [
        lambda plan: replace(
            plan,
            relations=(
                replace(
                    plan.relations[0], props={"generation_id": "g", "source_revision": "r", "evidence_ids": ("e1",)}
                ),
            ),
        ),
        lambda plan: replace(
            plan, relations=(replace(plan.relations[0], props={**plan.relations[0].props, "generation_id": " "}),)
        ),
        lambda plan: replace(
            plan, relations=(replace(plan.relations[0], props={**plan.relations[0].props, "source_revision": None}),)
        ),
        lambda plan: replace(
            plan, relations=(replace(plan.relations[0], props={**plan.relations[0].props, "evidence_ids": ()}),)
        ),
        lambda plan: replace(
            plan, relations=(replace(plan.relations[0], props={**plan.relations[0].props, "evidence_ids": ["e1"]}),)
        ),
        lambda plan: replace(
            plan, relations=(replace(plan.relations[0], props={**plan.relations[0].props, "evidence_ids": (" ",)}),)
        ),
    ],
)
def test_neo4j_sink_rejects_invalid_relation_provenance_before_opening_driver_session(
    invalid_plan: Callable[[GraphWritePlan], GraphWritePlan],
) -> None:
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver)
    plan = _plan()

    with pytest.raises(ValueError):
        sink.write(invalid_plan(GraphWritePlan(plan.nodes[:2], (plan.relations[0],))))

    assert driver.session_calls == 0
    assert driver.calls == []


def test_neo4j_sink_relation_readback_scopes_both_endpoints_to_namespace() -> None:
    plan = _plan()
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver, namespace="test")
    sink.write(plan)

    assert "source._ontoagent_namespace = $namespace" in Neo4jGraphSink.READ_RELATIONS_QUERY
    assert "target._ontoagent_namespace = $namespace" in Neo4jGraphSink.READ_RELATIONS_QUERY


@pytest.mark.parametrize(
    ("node_override", "relation_override"),
    [
        (
            lambda rows: [
                {**row, "properties": {**row["properties"], "_ontoagent_namespace": "other"}} for row in rows
            ],
            None,
        ),
        (
            None,
            lambda rows: [
                {**row, "properties": {**row["properties"], "_ontoagent_namespace": "other"}} for row in rows
            ],
        ),
        (None, lambda rows: [{**row, "source_namespace": "other"} for row in rows]),
        (None, lambda rows: [{**row, "target_namespace": "other"} for row in rows]),
    ],
)
def test_neo4j_sink_rejects_readback_rows_outside_its_namespace(
    node_override: Callable[[list[dict[str, object]]], list[dict[str, object]]] | None,
    relation_override: Callable[[list[dict[str, object]]], list[dict[str, object]]] | None,
) -> None:
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver, namespace="test")
    sink.write(_plan())
    driver.node_row_override = node_override
    driver.relation_row_override = relation_override

    with pytest.raises(ValueError):
        sink.readback()


@pytest.mark.parametrize(
    ("node_override", "relation_override"),
    [
        (lambda _: [{"labels": ["Unexpected"], "properties": {"id": "service", "_ontoagent_props": "{}"}}], None),
        (
            lambda _: [{"labels": ["Endpoint", "Evidence"], "properties": {"id": "service", "_ontoagent_props": "{}"}}],
            None,
        ),
        (
            lambda _: [
                {"labels": ["Endpoint"], "properties": {"id": "service", "unexpected": 1, "_ontoagent_props": "{}"}}
            ],
            None,
        ),
        (
            None,
            lambda _: [
                {
                    "type": "BAD",
                    "source_id": "service",
                    "target_id": "endpoint",
                    "properties": {"_ontoagent_relation_id": "r1", "_ontoagent_props": "{}"},
                }
            ],
        ),
        (
            None,
            lambda _: [
                {
                    "type": "PROVIDES_ENDPOINT",
                    "source_id": "missing",
                    "target_id": "endpoint",
                    "properties": {"_ontoagent_relation_id": "r1", "_ontoagent_props": "{}"},
                }
            ],
        ),
    ],
)
def test_neo4j_sink_readback_fails_closed_for_malformed_or_unexpected_rows(
    node_override: Callable[[list[dict[str, object]]], list[dict[str, object]]] | None,
    relation_override: Callable[[list[dict[str, object]]], list[dict[str, object]]] | None,
) -> None:
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver, namespace="test")
    sink.write(_plan())
    driver.node_row_override = node_override
    driver.relation_row_override = relation_override

    with pytest.raises(ValueError):
        sink.readback()


def test_neo4j_sink_rejects_injected_types_and_duplicate_ids_before_querying() -> None:
    driver = StatefulFakeDriver()
    sink = Neo4jGraphSink(driver)
    plan = _plan()

    with pytest.raises(ValueError):
        sink.write(replace(plan, nodes=(replace(plan.nodes[0], node_type="Endpoint) DELETE n //"),)))
    with pytest.raises(ValueError):
        sink.write(replace(plan, relations=(plan.relations[0], replace(plan.relations[0], id="r1"))))

    assert driver.calls == []
