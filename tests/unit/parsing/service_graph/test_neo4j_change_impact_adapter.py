from __future__ import annotations

from dataclasses import replace

import pytest

from ontoagent.parsing.service_graph.change_analysis import (
    ServiceGraphChangeAnalysisBlockReason,
    ServiceGraphChangeAnalysisStatus,
)
from ontoagent.parsing.service_graph.generation_manifest import ManifestState, Neo4jNamespace, ServiceGraphManifest
from ontoagent.parsing.service_graph.graph_plan import GraphNode, GraphRelation, GraphWritePlan
from ontoagent.parsing.service_graph.neo4j_change_impact_adapter import Neo4jServiceGraphChangeImpactAdapter
from ontoagent.parsing.service_graph.neo4j_graph_sink import Neo4jGraphSink
from ontoagent.parsing.service_graph.neo4j_manifest_repository import (
    DurableServiceGraphManifest,
    Neo4jServiceGraphManifestRepository,
)


class _Session:
    def __init__(self, driver: _Driver) -> None:
        self._driver = driver

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def run(self, query: str, **params: object) -> list[dict[str, object]]:
        self._driver.calls.append((query, params))
        return self._driver.rows.get(query, [])


class _Driver:
    def __init__(self, rows: dict[str, list[dict[str, object]]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.session_calls = 0

    def session(self) -> _Session:
        self.session_calls += 1
        return _Session(self)


class _Manifests:
    def __init__(self, records: dict[str, DurableServiceGraphManifest | None]) -> None:
        self.records = records
        self.calls: list[tuple[str, Neo4jNamespace, str]] = []

    def get(self, repo_id: str, namespace: Neo4jNamespace, generation_id: str) -> DurableServiceGraphManifest | None:
        self.calls.append((repo_id, namespace, generation_id))
        return self.records[generation_id]


def _node(node: GraphNode) -> dict[str, object]:
    return {
        "labels": [node.node_type],
        "properties": {
            "id": node.id,
            "_ontoagent_namespace": "service-graph",
            "_ontoagent_props": Neo4jGraphSink.encode_props(node.props),
        },
    }


def _relation(relation: GraphRelation) -> dict[str, object]:
    return {
        "relation_type": relation.relation_type,
        "relation_id": relation.id,
        "source_id": relation.source_id,
        "target_id": relation.target_id,
        "relation_properties": {
            "_ontoagent_relation_id": relation.id,
            "_ontoagent_namespace": "service-graph",
            "_ontoagent_props": Neo4jGraphSink.encode_props(relation.props),
        },
    }


def _plan(
    generation: str,
    provider_revision: str,
    *,
    include_provider_endpoint: bool = True,
    include_consumer_endpoint: bool = True,
) -> GraphWritePlan:
    nodes: list[GraphNode] = []
    for repo_id, revision, role in (("provider", provider_revision, "provider"), ("consumer", "c1", "consumer")):
        evidence_id = f"evidence-{repo_id}-{generation}"
        endpoint_id = f"endpoint-{repo_id}-{generation}"
        nodes.extend(
            (
                GraphNode(
                    f"service-{repo_id}-{generation}",
                    "ServiceDefinition",
                    {
                        "id": f"service-{repo_id}-{generation}",
                        "repo_id": repo_id,
                        "generation_id": generation,
                        "source_revision": revision,
                        "protocol": "HTTP",
                        "canonical_key": repo_id,
                        "role": role,
                        "evidence_ids": (evidence_id,),
                    },
                ),
                GraphNode(
                    evidence_id,
                    "Evidence",
                    {
                        "id": evidence_id,
                        "repo_id": repo_id,
                        "generation_id": generation,
                        "source_revision": revision,
                        "protocol": "HTTP",
                        "canonical_key": f"evidence:{repo_id}",
                        "role": role,
                        "evidence_ids": (evidence_id,),
                    },
                ),
            )
        )
        if (repo_id == "provider" and include_provider_endpoint) or (
            repo_id == "consumer" and include_consumer_endpoint
        ):
            nodes.append(
                GraphNode(
                    endpoint_id,
                    "Endpoint",
                    {
                        "id": endpoint_id,
                        "repo_id": repo_id,
                        "generation_id": generation,
                        "source_revision": revision,
                        "protocol": "HTTP",
                        "canonical_key": "GET /orders",
                        "role": role,
                        "evidence_ids": (evidence_id,),
                    },
                )
            )
    relations: list[GraphRelation] = []
    for endpoint in (node for node in nodes if node.node_type == "Endpoint"):
        evidence_id = f"evidence-{endpoint.props['repo_id']}-{generation}"
        relation_type = "PROVIDES_ENDPOINT" if endpoint.props["role"] == "provider" else "CONSUMES_ENDPOINT"
        relations.append(
            GraphRelation(
                f"support-{endpoint.id}",
                "SUPPORTED_BY_EVIDENCE",
                endpoint.id,
                evidence_id,
                {
                    "generation_id": generation,
                    "source_revision": endpoint.props["source_revision"],
                    "canonical_key": endpoint.props["canonical_key"],
                    "evidence_ids": (evidence_id,),
                },
            )
        )
        relations.append(
            GraphRelation(
                f"membership-{endpoint.id}",
                relation_type,
                f"service-{endpoint.props['repo_id']}-{generation}",
                endpoint.id,
                {
                    "generation_id": generation,
                    "source_revision": endpoint.props["source_revision"],
                    "canonical_key": endpoint.props["canonical_key"],
                    "evidence_ids": (evidence_id,),
                },
            )
        )
    if include_provider_endpoint:
        relations.append(
            GraphRelation(
                f"depends-{generation}",
                "DEPENDS_ON",
                f"endpoint-consumer-{generation}",
                f"endpoint-provider-{generation}",
                {
                    "generation_id": generation,
                    "source_revision": "c1",
                    "canonical_key": "GET /orders",
                    "evidence_ids": (f"evidence-consumer-{generation}", f"evidence-provider-{generation}"),
                    "provider_repo_id": "provider",
                    "provider_generation_id": generation,
                    "provider_source_revision": provider_revision,
                    "consumer_repo_id": "consumer",
                    "consumer_generation_id": generation,
                    "consumer_source_revision": "c1",
                },
            )
        )
    return GraphWritePlan(
        tuple(sorted(nodes, key=lambda node: node.id)), tuple(sorted(relations, key=lambda item: item.id))
    )


def _record(
    plan: GraphWritePlan, generation: str, *, state: ManifestState = ManifestState.READY, confirmed: bool = True
) -> DurableServiceGraphManifest:
    return DurableServiceGraphManifest(
        ServiceGraphManifest(
            "provider", generation, "p1" if generation == "g1" else "p2", Neo4jNamespace("service-graph")
        ),
        state,
        confirmed,
        len(plan.nodes),
        len(plan.relations),
        Neo4jServiceGraphManifestRepository.receipt_fingerprint(plan),
    )


def _adapter(
    from_plan: GraphWritePlan, to_plan: GraphWritePlan, records: dict[str, DurableServiceGraphManifest | None]
):
    driver = _Driver(
        {
            Neo4jServiceGraphChangeImpactAdapter.NODES_QUERY: [
                _node(node) for node in (*from_plan.nodes, *to_plan.nodes)
            ],
            Neo4jServiceGraphChangeImpactAdapter.RELATIONS_QUERY: [
                _relation(relation) for relation in (*from_plan.relations, *to_plan.relations)
            ],
        }
    )
    return Neo4jServiceGraphChangeImpactAdapter(driver, _Manifests(records), Neo4jNamespace("service-graph")), driver


@pytest.mark.parametrize(
    ("record_factory", "expected_reason"),
    [
        (lambda plan: None, ServiceGraphChangeAnalysisBlockReason.MISSING_DURABLE_MANIFEST),
        (
            lambda plan: _record(plan, "g1", state=ManifestState.BUILDING, confirmed=False),
            ServiceGraphChangeAnalysisBlockReason.DURABLE_MANIFEST_NOT_READY,
        ),
        (lambda plan: _record(plan, "other"), ServiceGraphChangeAnalysisBlockReason.DURABLE_MANIFEST_MISMATCH),
        (
            lambda plan: replace(_record(plan, "g1"), node_count=-1),
            ServiceGraphChangeAnalysisBlockReason.DURABLE_MANIFEST_MISMATCH,
        ),
    ],
)
def test_bad_durable_manifest_blocks_before_driver(
    record_factory: object, expected_reason: ServiceGraphChangeAnalysisBlockReason
) -> None:
    plan = _plan("g1", "p1")
    record = record_factory(plan)  # type: ignore[operator]
    adapter, driver = _adapter(plan, plan, {"g1": record, "g2": _record(plan, "g2")})

    result = adapter.analyze("provider", "g1", "g2")

    assert result.status is ServiceGraphChangeAnalysisStatus.BLOCKED
    assert result.reasons == (expected_reason,)
    assert driver.session_calls == 0
    assert driver.calls == []


def test_parameterized_rows_decode_and_deleted_provider_retains_from_generation_consumer_impact() -> None:
    from_plan = _plan("g1", "p1")
    to_plan = _plan("g2", "p2", include_provider_endpoint=False)
    adapter, driver = _adapter(from_plan, to_plan, {"g1": _record(from_plan, "g1"), "g2": _record(to_plan, "g2")})

    result = adapter.analyze("provider", "g1", "g2")

    assert result.status is ServiceGraphChangeAnalysisStatus.READY
    assert [item.contract.canonical_key for item in result.endpoint_deletions] == ["GET /orders"]
    assert [(item.changed_endpoint.repo_id, item.impacted_endpoint.repo_id) for item in result.direct_impacts] == [
        ("provider", "consumer")
    ]
    assert [params for _, params in driver.calls] == [{"namespace": "service-graph"}] * 4


def test_source_revision_change_is_fact_revision_and_empty_readback_is_ready_not_blocked() -> None:
    from_plan = _plan("g1", "p1")
    to_plan = _plan("g2", "p2")
    adapter, _ = _adapter(from_plan, to_plan, {"g1": _record(from_plan, "g1"), "g2": _record(to_plan, "g2")})

    result = adapter.analyze("provider", "g1", "g2")

    assert result.status is ServiceGraphChangeAnalysisStatus.READY
    assert result.contract_changes == ()
    assert result.fact_revisions
    no_endpoints_from = _plan("g1", "p1", include_provider_endpoint=False, include_consumer_endpoint=False)
    no_endpoints_to = _plan("g2", "p2", include_provider_endpoint=False, include_consumer_endpoint=False)
    empty_adapter, _ = _adapter(
        no_endpoints_from,
        no_endpoints_to,
        {"g1": _record(no_endpoints_from, "g1"), "g2": _record(no_endpoints_to, "g2")},
    )
    assert empty_adapter.analyze("provider", "g1", "g2").status is ServiceGraphChangeAnalysisStatus.READY
