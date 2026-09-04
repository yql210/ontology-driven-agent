from __future__ import annotations

from collections.abc import Mapping

import pytest

from ontoagent.parsing.service_graph.generation_manifest import (
    ActiveServiceGraphBinding,
    ManifestBlockReason,
    ManifestResolution,
    ManifestResolutionStatus,
    ManifestState,
    Neo4jNamespace,
    ServiceGraphManifest,
)
from ontoagent.parsing.service_graph.neo4j_graph_sink import Neo4jGraphSink
from ontoagent.parsing.service_graph.neo4j_query_adapter import Neo4jServiceGraphQueryAdapter
from ontoagent.parsing.service_graph.query import ServiceGraphQueryBlockReason, ServiceGraphQueryStatus


class _Session:
    def __init__(self, driver: _Driver) -> None:
        self._driver = driver

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def run(self, query: str, **params: object) -> list[Mapping[str, object]]:
        self._driver.calls.append((query, params))
        return self._driver.rows_by_query.get(query, [])


class _Driver:
    def __init__(self, rows_by_query: Mapping[str, list[Mapping[str, object]]] | None = None) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self.rows_by_query = dict(rows_by_query or {})
        self.session_calls = 0

    def session(self) -> _Session:
        self.session_calls += 1
        return _Session(self)


class _Resolver:
    def __init__(self, resolution: ManifestResolution) -> None:
        self.resolution = resolution
        self.calls: list[tuple[object, object, object]] = []

    def resolve(self, repo_id: object, generation_id: object, namespace: object) -> ManifestResolution:
        self.calls.append((repo_id, generation_id, namespace))
        return self.resolution


def _ready(namespace: str = "service-graph") -> ManifestResolution:
    manifest = ServiceGraphManifest("repo-1", "generation-1", "revision-1", Neo4jNamespace(namespace))
    return ManifestResolution(
        ManifestResolutionStatus.READY,
        ActiveServiceGraphBinding(manifest, ManifestState.READY),
        (),
    )


def _blocked(reason: ManifestBlockReason = ManifestBlockReason.MISSING_ACTIVE) -> ManifestResolution:
    return ManifestResolution(ManifestResolutionStatus.BLOCKED, None, (reason,))


def _node_row(node_id: str, node_type: str, props: Mapping[str, object]) -> Mapping[str, object]:
    return {
        "labels": [node_type],
        "properties": {
            "id": node_id,
            "_ontoagent_namespace": "service-graph",
            "_ontoagent_props": Neo4jGraphSink.encode_props(props),
        },
    }


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("service_directory", ()),
        ("find_endpoint_providers", ("HTTP GET /orders",)),
        ("find_endpoint_consumers", ("HTTP GET /orders",)),
        ("find_service_dependencies", ("service-1",)),
        ("get_evidence", ("evidence-1",)),
    ],
)
def test_blocked_manifest_prevents_any_neo4j_driver_call(method: str, args: tuple[str, ...]) -> None:
    driver = _Driver()
    resolver = _Resolver(_blocked(ManifestBlockReason.NOT_READY))
    adapter = Neo4jServiceGraphQueryAdapter(driver, resolver, Neo4jNamespace("service-graph"))

    result = getattr(adapter, method)("repo-1", "generation-1", *args)

    assert result.status is ServiceGraphQueryStatus.BLOCKED
    assert result.reasons == (ServiceGraphQueryBlockReason.NOT_READY,)
    assert resolver.calls == [("repo-1", "generation-1", Neo4jNamespace("service-graph"))]
    assert driver.session_calls == 0
    assert driver.calls == []


def test_ready_endpoint_provider_query_uses_namespace_parameter_and_decodes_properties() -> None:
    props = {
        "id": "endpoint-1",
        "repo_id": "repo-1",
        "generation_id": "generation-1",
        "source_revision": "revision-1",
        "canonical_key": "HTTP GET /orders",
        "role": "provider",
        "evidence_ids": ("evidence-1",),
    }
    driver = _Driver({Neo4jServiceGraphQueryAdapter.ENDPOINTS_QUERY: [_node_row("endpoint-1", "Endpoint", props)]})
    resolver = _Resolver(_ready())
    adapter = Neo4jServiceGraphQueryAdapter(driver, resolver, Neo4jNamespace("service-graph"))

    result = adapter.find_endpoint_providers("repo-1", "generation-1", "HTTP GET /orders")

    assert result.status is ServiceGraphQueryStatus.READY
    assert result.generation_id == "generation-1"
    assert result.nodes[0].id == "endpoint-1"
    assert result.nodes[0].properties == props
    assert driver.calls == [(Neo4jServiceGraphQueryAdapter.ENDPOINTS_QUERY, {"namespace": "service-graph"})]


def test_ready_dependency_query_maps_relation_evidence_and_provenance() -> None:
    consumer = _node_row(
        "consumer-endpoint",
        "Endpoint",
        {
            "id": "consumer-endpoint",
            "repo_id": "repo-1",
            "generation_id": "generation-1",
            "source_revision": "revision-1",
            "canonical_key": "topic:orders",
            "role": "consumer",
            "evidence_ids": ("consumer-evidence",),
        },
    )
    provider = _node_row(
        "provider-endpoint",
        "Endpoint",
        {
            "id": "provider-endpoint",
            "repo_id": "repo-2",
            "generation_id": "generation-1",
            "source_revision": "revision-2",
            "canonical_key": "topic:orders",
            "role": "provider",
            "evidence_ids": ("provider-evidence",),
        },
    )
    relation_props = {
        "generation_id": "generation-1",
        "source_revision": "revision-1",
        "canonical_key": "topic:orders",
        "evidence_ids": ("consumer-evidence", "provider-evidence"),
        "provider_repo_id": "repo-2",
        "consumer_repo_id": "repo-1",
    }
    row = {
        "source_labels": consumer["labels"],
        "source_properties": consumer["properties"],
        "target_labels": provider["labels"],
        "target_properties": provider["properties"],
        "relation_type": "DEPENDS_ON",
        "relation_id": "dependency-1",
        "relation_properties": {
            "_ontoagent_relation_id": "dependency-1",
            "_ontoagent_namespace": "service-graph",
            "_ontoagent_props": Neo4jGraphSink.encode_props(relation_props),
        },
    }
    driver = _Driver({Neo4jServiceGraphQueryAdapter.DEPENDENCIES_QUERY: [row]})
    adapter = Neo4jServiceGraphQueryAdapter(driver, _Resolver(_ready()), Neo4jNamespace("service-graph"))

    result = adapter.find_service_dependencies("repo-1", "generation-1", "consumer-endpoint")

    assert result.status is ServiceGraphQueryStatus.READY
    assert {node.id for node in result.nodes} == {"consumer-endpoint", "provider-endpoint"}
    assert result.relations[0].id == "dependency-1"
    assert result.relations[0].properties == relation_props
    assert driver.calls == [
        (
            Neo4jServiceGraphQueryAdapter.DEPENDENCIES_QUERY,
            {"namespace": "service-graph", "service_id": "consumer-endpoint"},
        )
    ]


def test_ready_directory_and_consumers_are_verified_empty_when_no_results_exist() -> None:
    driver = _Driver(
        {
            Neo4jServiceGraphQueryAdapter.SERVICE_DIRECTORY_QUERY: [],
            Neo4jServiceGraphQueryAdapter.ENDPOINTS_QUERY: [],
        }
    )
    resolver = _Resolver(_ready())
    adapter = Neo4jServiceGraphQueryAdapter(driver, resolver, Neo4jNamespace("service-graph"))

    directory = adapter.service_directory("repo-1", "generation-1")
    consumers = adapter.find_endpoint_consumers("repo-1", "generation-1", "missing")

    assert all(result.status is ServiceGraphQueryStatus.READY and not result.nodes for result in (directory, consumers))
    assert len(resolver.calls) == 2
    assert [query for query, _ in driver.calls] == [
        Neo4jServiceGraphQueryAdapter.SERVICE_DIRECTORY_QUERY,
        Neo4jServiceGraphQueryAdapter.ENDPOINTS_QUERY,
    ]


def test_get_evidence_blocks_when_no_node_or_relation_owner_matches() -> None:
    driver = _Driver(
        {
            Neo4jServiceGraphQueryAdapter.EVIDENCE_OWNER_NODE_QUERY: [],
            Neo4jServiceGraphQueryAdapter.EVIDENCE_OWNER_RELATION_QUERY: [],
        }
    )
    adapter = Neo4jServiceGraphQueryAdapter(driver, _Resolver(_ready()), Neo4jNamespace("service-graph"))

    result = adapter.get_evidence("repo-1", "generation-1", "missing")

    assert result.status is ServiceGraphQueryStatus.BLOCKED
    assert result.reasons == (ServiceGraphQueryBlockReason.ENTITY_OR_RELATION_NOT_FOUND,)


@pytest.mark.parametrize(
    ("owner_repo_id", "owner_generation_id", "expected_reason"),
    [
        ("other-repo", "generation-1", ServiceGraphQueryBlockReason.REPO_MISMATCH),
        ("repo-1", "other-generation", ServiceGraphQueryBlockReason.GENERATION_MISMATCH),
    ],
)
def test_get_evidence_blocks_when_node_owner_identity_does_not_match_request(
    owner_repo_id: str, owner_generation_id: str, expected_reason: ServiceGraphQueryBlockReason
) -> None:
    owner_props = {
        "id": "endpoint-1",
        "repo_id": owner_repo_id,
        "generation_id": owner_generation_id,
        "source_revision": "revision-1",
        "canonical_key": "HTTP GET /orders",
        "role": "provider",
        "evidence_ids": ("evidence-1",),
    }
    driver = _Driver(
        {Neo4jServiceGraphQueryAdapter.EVIDENCE_OWNER_NODE_QUERY: [_node_row("endpoint-1", "Endpoint", owner_props)]}
    )
    adapter = Neo4jServiceGraphQueryAdapter(driver, _Resolver(_ready()), Neo4jNamespace("service-graph"))

    result = adapter.get_evidence("repo-1", "generation-1", "endpoint-1")

    assert result.status is ServiceGraphQueryStatus.BLOCKED
    assert result.reasons == (expected_reason,)
    assert [query for query, _ in driver.calls] == [Neo4jServiceGraphQueryAdapter.EVIDENCE_OWNER_NODE_QUERY]


def test_get_evidence_blocks_when_relation_owner_repo_does_not_match_request() -> None:
    owner_props = {
        "id": "endpoint-1",
        "repo_id": "other-repo",
        "generation_id": "generation-1",
        "source_revision": "revision-1",
        "canonical_key": "HTTP GET /orders",
        "role": "provider",
        "evidence_ids": ("evidence-1",),
    }
    relation_row = {
        "source_labels": ["Endpoint"],
        "source_properties": _node_row("endpoint-1", "Endpoint", owner_props)["properties"],
        "relation_type": "SUPPORTED_BY_EVIDENCE",
        "relation_id": "relation-1",
        "relation_properties": {
            "_ontoagent_relation_id": "relation-1",
            "_ontoagent_namespace": "service-graph",
            "_ontoagent_props": Neo4jGraphSink.encode_props({"evidence_ids": ("evidence-1",)}),
        },
    }
    driver = _Driver({Neo4jServiceGraphQueryAdapter.EVIDENCE_OWNER_RELATION_QUERY: [relation_row]})
    adapter = Neo4jServiceGraphQueryAdapter(driver, _Resolver(_ready()), Neo4jNamespace("service-graph"))

    result = adapter.get_evidence("repo-1", "generation-1", "relation-1")

    assert result.status is ServiceGraphQueryStatus.BLOCKED
    assert result.reasons == (ServiceGraphQueryBlockReason.REPO_MISMATCH,)


def test_get_evidence_returns_verified_empty_when_authorized_owner_has_no_evidence_results() -> None:
    owner_props = {
        "id": "endpoint-1",
        "repo_id": "repo-1",
        "generation_id": "generation-1",
        "source_revision": "revision-1",
        "canonical_key": "HTTP GET /orders",
        "role": "provider",
        "evidence_ids": ("evidence-1",),
    }
    driver = _Driver(
        {
            Neo4jServiceGraphQueryAdapter.EVIDENCE_OWNER_NODE_QUERY: [_node_row("endpoint-1", "Endpoint", owner_props)],
            Neo4jServiceGraphQueryAdapter.EVIDENCE_NODES_QUERY: [],
        }
    )
    adapter = Neo4jServiceGraphQueryAdapter(driver, _Resolver(_ready()), Neo4jNamespace("service-graph"))

    result = adapter.get_evidence("repo-1", "generation-1", "endpoint-1")

    assert result.status is ServiceGraphQueryStatus.READY
    assert result.nodes == ()
