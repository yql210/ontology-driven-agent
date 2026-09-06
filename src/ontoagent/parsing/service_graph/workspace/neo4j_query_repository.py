"""Neo4j repository for receipt-gated workspace service graph reads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ontoagent.domain.workspace_authorization import WorkspaceQueryAuthorization
from ontoagent.execution.workspace_service_graph_query import (
    WorkspaceServiceGraphReadError,
    WorkspaceServiceGraphReadRepository,
)

from ..graph_plan import GraphNode, GraphRelation, GraphWritePlan
from ..neo4j_graph_sink import Neo4jGraphSink
from ..neo4j_manifest_repository import Neo4jServiceGraphManifestRepository
from .publish_orchestrator import WorkspaceServiceGraphPublishOrchestrator


class Neo4jDriver(Protocol):
    def session(self) -> object: ...


class WorkspaceServiceGraphGateError(WorkspaceServiceGraphReadError):
    """The trusted generation namespace, manifest, or receipt is not readable."""


class Neo4jWorkspaceServiceGraphQueryRepository(WorkspaceServiceGraphReadRepository):
    """Read only a durable receipt-confirmed workspace generation namespace."""

    GRAPH_NODE_LABELS = ("ServiceDefinition", "Endpoint", "Evidence")
    METHOD_NODE_LABELS = (
        "ServiceOperation",
        "ImplementationMethod",
        "ConsumerMethodCall",
        "OperationBinding",
        "MethodEvidence",
        "MethodUnresolved",
        "MethodCallTarget",
    )
    RECEIPT_QUERY = (
        "MATCH (receipt:OntoAgentWorkspaceServiceGraphReceipt {workspaceId: $workspace_id, "
        "generationId: $generation_id, namespace: $namespace}) "
        "RETURN receipt.confirmed AS confirmed, receipt.nodeCount AS node_count, receipt.relationCount AS relation_count, "
        "receipt.fingerprint AS fingerprint"
    )
    NODES_QUERY = " UNION ".join(
        "MATCH (n:" + label + " {_ontoagent_namespace: $namespace}) "
        "RETURN labels(n) AS labels, properties(n) AS properties"
        for label in GRAPH_NODE_LABELS
    )
    RELATIONS_QUERY = " UNION ".join(
        "MATCH (source:"
        + label
        + " {_ontoagent_namespace: $namespace})-[r]->(target {_ontoagent_namespace: $namespace}) "
        "WHERE r._ontoagent_namespace = $namespace "
        "AND type(r) IN ['PROVIDES_ENDPOINT', 'CONSUMES_ENDPOINT', 'DEPENDS_ON', 'SUPPORTED_BY_EVIDENCE'] "
        "RETURN type(r) AS relation_type, r._ontoagent_relation_id AS relation_id, source.id AS source_id, "
        "target.id AS target_id, properties(r) AS properties"
        for label in GRAPH_NODE_LABELS
    )
    METHOD_NODES_QUERY = " UNION ".join(
        "MATCH (n:" + label + " {workspaceId: $workspace_id, generationId: $generation_id, namespace: $namespace}) "
        "RETURN labels(n) AS labels, properties(n) AS properties"
        for label in METHOD_NODE_LABELS
    )
    METHOD_RELATIONS_QUERY = " UNION ".join(
        "MATCH (source:"
        + label
        + " {workspaceId: $workspace_id, generationId: $generation_id, namespace: $namespace})-[r]->"
        "(target {workspaceId: $workspace_id, generationId: $generation_id, namespace: $namespace}) "
        "WHERE r.namespace = $namespace "
        "RETURN type(r) AS relation_type, r.id AS relation_id, r.sourceId AS source_id, r.targetId AS target_id, "
        "properties(r) AS properties"
        for label in METHOD_NODE_LABELS
    )
    TASKS_QUERY = (
        "MATCH (task:OntoAgentWorkspaceBuildTask {workspaceId: $workspace_id}) "
        "OPTIONAL MATCH (generation:OntoAgentWorkspaceGeneration {workspaceId: $workspace_id, generationId: task.generationId}) "
        "RETURN task.taskId AS task_id, task.workspaceId AS workspace_id, task.generationId AS generation_id, "
        "generation.state AS status"
    )

    def __init__(self, driver: Neo4jDriver) -> None:
        self._driver = driver

    def read(
        self, authorization: WorkspaceQueryAuthorization
    ) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
        namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(
            authorization.workspace_id, authorization.generation_id
        )
        with self._driver.session() as session:  # type: ignore[union-attr]
            receipt_rows = list(
                session.run(  # type: ignore[union-attr]
                    self.RECEIPT_QUERY,
                    workspace_id=authorization.workspace_id,
                    generation_id=authorization.generation_id,
                    namespace=namespace,
                )
            )
            receipt = _mapping(receipt_rows[0]) if len(receipt_rows) == 1 else {}
            if receipt.get("confirmed") is not True:
                raise WorkspaceServiceGraphGateError("workspace generation has no trusted confirmed receipt")
            node_rows = list(session.run(self.NODES_QUERY, namespace=namespace))  # type: ignore[union-attr]
            relation_rows = list(session.run(self.RELATIONS_QUERY, namespace=namespace))  # type: ignore[union-attr]
            method_node_rows = list(  # type: ignore[union-attr]
                session.run(
                    self.METHOD_NODES_QUERY,
                    workspace_id=authorization.workspace_id,
                    generation_id=authorization.generation_id,
                    namespace=namespace,
                )
            )
            method_relation_rows = list(  # type: ignore[union-attr]
                session.run(
                    self.METHOD_RELATIONS_QUERY,
                    workspace_id=authorization.workspace_id,
                    generation_id=authorization.generation_id,
                    namespace=namespace,
                )
            )
            task_rows = list(session.run(self.TASKS_QUERY, workspace_id=authorization.workspace_id))  # type: ignore[union-attr]
        graph_plan = _graph_plan(node_rows, relation_rows)
        _verify_receipt(receipt, graph_plan)
        graph_nodes = tuple(_graph_node(node) for node in graph_plan.nodes)
        graph_edges = tuple(
            _graph_edge(relation, {str(node["id"]): node for node in graph_nodes}) for relation in graph_plan.relations
        )
        nodes = graph_nodes + tuple(_method_node(row) for row in method_node_rows)
        nodes += tuple(_task_node(row) for row in task_rows)
        edges = graph_edges
        edges += tuple(_method_edge(row, {str(node["id"]): node for node in nodes}) for row in method_relation_rows)
        return tuple(sorted(nodes, key=lambda item: str(item["id"]))), tuple(
            sorted(edges, key=lambda item: str(item["id"]))
        )


def _graph_plan(node_rows: list[object], relation_rows: list[object]) -> GraphWritePlan:
    """Decode stored graph rows into the same canonical plan shape used for receipt fingerprints."""
    try:
        return GraphWritePlan(
            tuple(sorted((_stored_graph_node(row) for row in node_rows), key=lambda node: node.id)),
            tuple(sorted((_stored_graph_relation(row) for row in relation_rows), key=lambda relation: relation.id)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise WorkspaceServiceGraphGateError("workspace graph readback is malformed") from error


def _stored_graph_node(row: object) -> GraphNode:
    values = _mapping(row)
    labels, properties = values.get("labels"), _mapping(values.get("properties"))
    if not isinstance(labels, list) or len(labels) != 1:
        raise WorkspaceServiceGraphGateError("malformed graph node")
    props = Neo4jGraphSink._decode_props(properties.get("_ontoagent_props"))
    node_id = props.get("id")
    if type(node_id) is not str or properties.get("id") != node_id:
        raise WorkspaceServiceGraphGateError("malformed graph node identity")
    return GraphNode(node_id, labels[0], props)


def _graph_node(node: GraphNode) -> dict[str, object]:
    return {"id": node.id, "node_type": node.node_type, **node.props}


def _stored_graph_relation(row: object) -> GraphRelation:
    values, properties = _mapping(row), _mapping(_mapping(row).get("properties"))
    props = Neo4jGraphSink._decode_props(properties.get("_ontoagent_props"))
    relation_id = values.get("relation_id")
    relation_type = values.get("relation_type")
    source_id = values.get("source_id")
    target_id = values.get("target_id")
    if not all(type(item) is str and item for item in (relation_id, relation_type, source_id, target_id)):
        raise WorkspaceServiceGraphGateError("malformed graph relation")
    if properties.get("_ontoagent_relation_id") != relation_id:
        raise WorkspaceServiceGraphGateError("malformed graph relation identity")
    return GraphRelation(relation_id, relation_type, source_id, target_id, props)


def _graph_edge(relation: GraphRelation, nodes: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    return _edge(
        {
            "relation_id": relation.id,
            "relation_type": relation.relation_type,
            "source_id": relation.source_id,
            "target_id": relation.target_id,
        },
        relation.props,
        nodes,
    )


def _method_node(row: object) -> dict[str, object]:
    values = _mapping(row)
    labels, props = values.get("labels"), _mapping(values.get("properties"))
    if not isinstance(labels, list) or len(labels) != 1 or type(props.get("id")) is not str:
        raise WorkspaceServiceGraphGateError("malformed method graph node")
    return {"id": props["id"], "node_type": labels[0], "repo_id": props.get("repoId"), **_public_props(props)}


def _method_edge(row: object, nodes: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    values = _mapping(row)
    return _edge(values, _mapping(values.get("properties")), nodes)


def _edge(
    values: Mapping[str, object], props: Mapping[str, object], nodes: Mapping[str, Mapping[str, object]]
) -> dict[str, object]:
    edge_id, source_id, target_id = values.get("relation_id"), values.get("source_id"), values.get("target_id")
    relation_type = values.get("relation_type")
    if not all(type(item) is str and item for item in (edge_id, source_id, target_id, relation_type)):
        raise WorkspaceServiceGraphGateError("malformed graph relation")
    source = str(source_id)
    target = str(target_id)
    repos = tuple(
        sorted(
            {str(node.get("repo_id", node.get("repoId"))) for node in (nodes.get(source), nodes.get(target)) if node}
        )
    )
    return {
        "id": edge_id,
        "relation_type": relation_type,
        "source_id": source_id,
        "target_id": target_id,
        "repo_ids": repos,
        **_public_props(props),
    }


def _public_props(props: Mapping[str, object]) -> dict[str, object]:
    """Exclude persistence payloads that can embed references outside the visible graph."""
    return {key: value for key, value in props.items() if key != "factPayload"}


def _task_node(row: object) -> dict[str, object]:
    values = _mapping(row)
    task_id, workspace_id = values.get("task_id"), values.get("workspace_id")
    if type(task_id) is not str or type(workspace_id) is not str:
        raise WorkspaceServiceGraphGateError("malformed workspace build task")
    return {
        "id": task_id,
        "node_type": "BuildTask",
        "workspace_id": workspace_id,
        "generation_id": values.get("generation_id"),
        "status": values.get("status"),
    }


def _verify_receipt(receipt: Mapping[str, object], plan: GraphWritePlan) -> None:
    if receipt.get("node_count") != len(plan.nodes) or receipt.get("relation_count") != len(plan.relations):
        raise WorkspaceServiceGraphGateError("workspace receipt count mismatch")
    if receipt.get("fingerprint") != Neo4jServiceGraphManifestRepository.receipt_fingerprint(plan):
        raise WorkspaceServiceGraphGateError("workspace receipt fingerprint mismatch")


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    try:
        return dict(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise WorkspaceServiceGraphGateError("malformed Neo4j row") from error
