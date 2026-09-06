from __future__ import annotations

from collections.abc import Mapping

from ontoagent.domain.workspace_authorization import AuthorizedRepositorySet, WorkspaceQueryAuthorization
from ontoagent.parsing.service_graph.graph_plan import GraphNode, GraphRelation, GraphWritePlan
from ontoagent.parsing.service_graph.neo4j_graph_sink import Neo4jGraphSink
from ontoagent.parsing.service_graph.neo4j_manifest_repository import Neo4jServiceGraphManifestRepository
from ontoagent.parsing.service_graph.workspace.neo4j_query_repository import Neo4jWorkspaceServiceGraphQueryRepository
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import WorkspaceServiceGraphPublishOrchestrator


class _Session:
    def __init__(self, driver: _Driver) -> None:
        self._driver = driver

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def run(self, query: str, **_: object) -> list[Mapping[str, object]]:
        return self._driver.rows_by_query.get(query, [])


class _Driver:
    def __init__(self, rows_by_query: Mapping[str, list[Mapping[str, object]]]) -> None:
        self.rows_by_query = dict(rows_by_query)

    def session(self) -> _Session:
        return _Session(self)


def test_query_plans_are_label_scoped_without_unbounded_node_matches() -> None:
    queries = (
        Neo4jWorkspaceServiceGraphQueryRepository.NODES_QUERY,
        Neo4jWorkspaceServiceGraphQueryRepository.RELATIONS_QUERY,
        Neo4jWorkspaceServiceGraphQueryRepository.METHOD_NODES_QUERY,
        Neo4jWorkspaceServiceGraphQueryRepository.METHOD_RELATIONS_QUERY,
    )

    assert all("MATCH (n) WHERE" not in query for query in queries)
    assert "ServiceDefinition" in Neo4jWorkspaceServiceGraphQueryRepository.NODES_QUERY
    assert "ServiceOperation" in Neo4jWorkspaceServiceGraphQueryRepository.METHOD_NODES_QUERY


def _node_row(node: GraphNode, namespace: str) -> Mapping[str, object]:
    return {
        "labels": [node.node_type],
        "properties": {
            "id": node.id,
            "_ontoagent_props": Neo4jGraphSink.encode_props(node.props),
            "_ontoagent_namespace": namespace,
        },
    }


def _relation_row(relation: GraphRelation, namespace: str) -> Mapping[str, object]:
    return {
        "relation_type": relation.relation_type,
        "relation_id": relation.id,
        "source_id": relation.source_id,
        "target_id": relation.target_id,
        "properties": {
            "_ontoagent_relation_id": relation.id,
            "_ontoagent_props": Neo4jGraphSink.encode_props(relation.props),
            "_ontoagent_namespace": namespace,
        },
    }


def test_query_receipt_verification_round_trips_neo4j_encoded_graph_plan_fingerprint() -> None:
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")
    node_props = {
        "repo_id": "repo-1",
        "source_revision": "revision-1",
        "generation_id": "generation-1",
        "canonical_key": "endpoint-1",
        "evidence_ids": ("evidence-1",),
        "repo_ids": ("repo-1",),
        "nested": {"optional": None, "items": ["one", "two"]},
        "workspace_generation_namespace": namespace,
    }
    relation_props = {
        "source_revision": "revision-1",
        "generation_id": "generation-1",
        "canonical_key": "relation-1",
        "evidence_ids": ("evidence-1",),
        "repo_ids": ("repo-1",),
        "nested": {"optional": None, "items": ["one", "two"]},
        "workspace_generation_namespace": namespace,
    }
    plan = GraphWritePlan(
        (
            GraphNode("endpoint-1", "Endpoint", {"id": "endpoint-1", **node_props}),
            GraphNode("evidence-1", "Evidence", {"id": "evidence-1", **node_props}),
        ),
        (GraphRelation("relation-1", "SUPPORTED_BY_EVIDENCE", "endpoint-1", "evidence-1", relation_props),),
    )
    driver = _Driver(
        {
            Neo4jWorkspaceServiceGraphQueryRepository.RECEIPT_QUERY: [
                {
                    "confirmed": True,
                    "node_count": len(plan.nodes),
                    "relation_count": len(plan.relations),
                    "fingerprint": Neo4jServiceGraphManifestRepository.receipt_fingerprint(plan),
                }
            ],
            Neo4jWorkspaceServiceGraphQueryRepository.NODES_QUERY: [
                _node_row(node, namespace) for node in reversed(plan.nodes)
            ],
            Neo4jWorkspaceServiceGraphQueryRepository.RELATIONS_QUERY: [
                _relation_row(relation, namespace) for relation in reversed(plan.relations)
            ],
            Neo4jWorkspaceServiceGraphQueryRepository.METHOD_NODES_QUERY: [],
            Neo4jWorkspaceServiceGraphQueryRepository.METHOD_RELATIONS_QUERY: [],
            Neo4jWorkspaceServiceGraphQueryRepository.TASKS_QUERY: [],
        }
    )
    repository = Neo4jWorkspaceServiceGraphQueryRepository(driver)

    nodes, edges = repository.read(
        WorkspaceQueryAuthorization("workspace-1", "generation-1", AuthorizedRepositorySet(frozenset({"repo-1"}), True))
    )

    assert {node["id"] for node in nodes} == {node.id for node in plan.nodes}
    assert {edge["id"] for edge in edges} == {relation.id for relation in plan.relations}


def test_query_response_does_not_expose_method_fact_payload() -> None:
    driver = _Driver(
        {
            Neo4jWorkspaceServiceGraphQueryRepository.RECEIPT_QUERY: [
                {
                    "confirmed": True,
                    "node_count": 0,
                    "relation_count": 0,
                    "fingerprint": Neo4jServiceGraphManifestRepository.receipt_fingerprint(GraphWritePlan((), ())),
                }
            ],
            Neo4jWorkspaceServiceGraphQueryRepository.NODES_QUERY: [],
            Neo4jWorkspaceServiceGraphQueryRepository.RELATIONS_QUERY: [],
            Neo4jWorkspaceServiceGraphQueryRepository.METHOD_NODES_QUERY: [
                {
                    "labels": ["ConsumerMethodCall"],
                    "properties": {
                        "id": "consumer-call-1",
                        "repoId": "consumer-repo",
                        "factPayload": '{"target_reference":"provider-operation-1"}',
                    },
                }
            ],
            Neo4jWorkspaceServiceGraphQueryRepository.METHOD_RELATIONS_QUERY: [],
            Neo4jWorkspaceServiceGraphQueryRepository.TASKS_QUERY: [],
        }
    )

    nodes, _ = Neo4jWorkspaceServiceGraphQueryRepository(driver).read(
        WorkspaceQueryAuthorization(
            "workspace-1", "generation-1", AuthorizedRepositorySet(frozenset({"consumer-repo"}), True)
        )
    )

    assert nodes == (
        {
            "id": "consumer-call-1",
            "node_type": "ConsumerMethodCall",
            "repo_id": "consumer-repo",
            "repoId": "consumer-repo",
        },
    )
