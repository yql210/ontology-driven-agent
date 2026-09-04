from __future__ import annotations

from ontoagent.parsing.service_graph.generation_manifest import (
    ManifestResolutionStatus,
    Neo4jNamespace,
    ServiceGraphManifest,
)
from ontoagent.parsing.service_graph.graph_plan import GraphNode, GraphRelation, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import WriteReceipt
from ontoagent.parsing.service_graph.neo4j_manifest_repository import (
    ManifestPublicationStatus,
    Neo4jServiceGraphManifestRepository,
)


class _Session:
    def __init__(self, driver: _Driver) -> None:
        self._driver = driver

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def run(self, query: str, **parameters: object) -> list[dict[str, object]]:
        self._driver.calls.append((query, parameters))
        if query.startswith("CREATE CONSTRAINT"):
            return []
        return self._driver.results.pop(0) if self._driver.results else []


class _Driver:
    def __init__(self, results: list[list[dict[str, object]]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.results = list(results or [])

    def session(self) -> _Session:
        return _Session(self)


def _manifest(generation_id: str = "generation-1") -> ServiceGraphManifest:
    return ServiceGraphManifest("repo-1", generation_id, "revision-1", Neo4jNamespace("namespace-1"))


def _plan(generation_id: str = "generation-1") -> GraphWritePlan:
    node = GraphNode(
        "endpoint-1",
        "Endpoint",
        {
            "id": "endpoint-1",
            "repo_id": "repo-1",
            "generation_id": generation_id,
            "source_revision": "revision-1",
            "canonical_key": "endpoint-1",
            "evidence_ids": ("evidence-1",),
        },
    )
    relation = GraphRelation(
        "relation-1",
        "PROVIDES_ENDPOINT",
        node.id,
        node.id,
        {
            "generation_id": generation_id,
            "source_revision": "revision-1",
            "canonical_key": "relation-1",
            "evidence_ids": ("evidence-1",),
        },
    )
    return GraphWritePlan((node,), (relation,))


def _receipt(plan: GraphWritePlan) -> WriteReceipt:
    return WriteReceipt(True, len(plan.nodes), len(plan.relations), plan, "namespace-1")


def _repository(driver: _Driver) -> Neo4jServiceGraphManifestRepository:
    repository = Neo4jServiceGraphManifestRepository(driver)
    driver.calls.clear()
    return repository


def test_persist_verified_serializes_identity_confirmation_and_fingerprint() -> None:
    driver = _Driver([[{"repo_id": "repo-1"}]])
    repository = _repository(driver)
    plan = _plan()

    resolution = repository.persist_verified(_manifest(), plan, _receipt(plan))

    assert resolution.status is ManifestResolutionStatus.READY
    query, params = driver.calls[-1]
    assert "OntoAgentServiceGraphManifest" in query
    assert "$repo_id" in query and "repo-1" not in query
    assert params == {
        "repo_id": "repo-1",
        "namespace": "namespace-1",
        "generation_id": "generation-1",
        "source_revision": "revision-1",
        "state": "ready",
        "receipt_confirmed": True,
        "node_count": 1,
        "relation_count": 1,
        "receipt_fingerprint": repository.receipt_fingerprint(plan),
    }


def test_publish_active_uses_single_parameterized_compare_and_set_query() -> None:
    driver = _Driver([[{"active_generation_id": "generation-2", "expected_matches": True, "candidate_verified": True}]])
    repository = _repository(driver)

    result = repository.publish_active("repo-1", Neo4jNamespace("namespace-1"), "generation-1", "generation-2")

    assert result.status is ManifestPublicationStatus.PUBLISHED
    query, params = driver.calls[-1]
    assert "OntoAgentServiceGraphActive" in query
    assert "FOREACH" in query
    assert "$expected_active_generation_id" in query
    assert "generation-2" not in query
    assert params == {
        "repo_id": "repo-1",
        "namespace": "namespace-1",
        "expected_active_generation_id": "generation-1",
        "candidate_generation_id": "generation-2",
    }


def test_publish_active_returns_typed_rejection_without_overwrite_for_stale_or_unready_candidate() -> None:
    driver = _Driver(
        [
            [{"active_generation_id": "generation-1", "expected_matches": False, "candidate_verified": True}],
            [{"active_generation_id": "generation-1", "expected_matches": True, "candidate_verified": False}],
        ]
    )
    repository = _repository(driver)

    stale = repository.publish_active("repo-1", Neo4jNamespace("namespace-1"), "old", "generation-2")
    unready = repository.publish_active("repo-1", Neo4jNamespace("namespace-1"), "generation-1", "generation-3")

    assert stale.status is ManifestPublicationStatus.REJECTED_STALE_ACTIVE
    assert stale.active_generation_id == "generation-1"
    assert unready.status is ManifestPublicationStatus.REJECTED_CANDIDATE_NOT_READY
    assert unready.active_generation_id == "generation-1"


def test_get_and_resolve_deserialize_verified_active_manifest() -> None:
    row = {
        "repo_id": "repo-1",
        "namespace": "namespace-1",
        "generation_id": "generation-1",
        "source_revision": "revision-1",
        "state": "ready",
        "receipt_confirmed": True,
        "node_count": 1,
        "relation_count": 1,
        "receipt_fingerprint": "abc",
    }
    driver = _Driver([[row], [{**row, "active_generation_id": "generation-1"}]])
    repository = _repository(driver)

    record = repository.get("repo-1", Neo4jNamespace("namespace-1"), "generation-1")
    resolution = repository.resolve("repo-1", "generation-1", Neo4jNamespace("namespace-1"))

    assert record is not None
    assert record.manifest == _manifest()
    assert record.node_count == 1
    assert resolution.status is ManifestResolutionStatus.READY
