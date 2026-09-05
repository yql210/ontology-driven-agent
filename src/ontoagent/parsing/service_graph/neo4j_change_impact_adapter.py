"""Durable, historical Neo4j service-graph change and impact analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from .change_analysis import (
    ServiceGraphChangeAnalysis,
    ServiceGraphChangeAnalysisBlockReason,
    ServiceGraphChangeAnalysisResult,
    ServiceGraphChangeAnalysisStatus,
)
from .generation_manifest import ManifestState, Neo4jNamespace
from .graph_plan import GraphNode, GraphRelation, GraphWritePlan
from .graph_writer import WriteReceipt
from .neo4j_graph_sink import Neo4jGraphSink
from .neo4j_manifest_repository import DurableServiceGraphManifest, Neo4jServiceGraphManifestRepository


class Neo4jDriver(Protocol):
    def session(self) -> object: ...


class DurableServiceGraphManifestResolver(Protocol):
    """Resolve a named durable generation without consulting ACTIVE state."""

    def get(
        self, repo_id: str, namespace: Neo4jNamespace, generation_id: str
    ) -> DurableServiceGraphManifest | None: ...


class Neo4jServiceGraphChangeImpactAdapter:
    """Analyze two receipt-verified generations that coexist in one namespace."""

    NODES_QUERY = (
        "MATCH (n) WHERE n._ontoagent_namespace = $namespace "
        "AND (n:ServiceDefinition OR n:Endpoint OR n:Evidence) "
        "RETURN labels(n) AS labels, properties(n) AS properties"
    )
    RELATIONS_QUERY = (
        "MATCH (source)-[r]->(target) WHERE r._ontoagent_namespace = $namespace "
        "AND source._ontoagent_namespace = $namespace AND target._ontoagent_namespace = $namespace "
        "AND type(r) IN ['PROVIDES_ENDPOINT', 'CONSUMES_ENDPOINT', 'DEPENDS_ON', 'SUPPORTED_BY_EVIDENCE'] "
        "RETURN type(r) AS relation_type, r._ontoagent_relation_id AS relation_id, source.id AS source_id, "
        "target.id AS target_id, properties(r) AS relation_properties"
    )
    _NODE_TYPES = frozenset({"ServiceDefinition", "Endpoint", "Evidence"})
    _RELATION_TYPES = frozenset({"PROVIDES_ENDPOINT", "CONSUMES_ENDPOINT", "DEPENDS_ON", "SUPPORTED_BY_EVIDENCE"})

    def __init__(
        self,
        driver: Neo4jDriver,
        manifest_resolver: DurableServiceGraphManifestResolver,
        namespace: Neo4jNamespace,
    ) -> None:
        if type(namespace) is not Neo4jNamespace:
            raise ValueError("namespace must be a Neo4jNamespace")
        self._driver = driver
        self._manifest_resolver = manifest_resolver
        self._namespace = namespace

    def analyze(
        self, repo_id: object, from_generation: object, to_generation: object
    ) -> ServiceGraphChangeAnalysisResult:
        """Return a JSON-safe change outcome for two exact durable generations."""
        if not all(_is_nonblank(value) for value in (repo_id, from_generation, to_generation)):
            return _blocked(ServiceGraphChangeAnalysisBlockReason.MALFORMED_REQUEST)
        request = (repo_id.strip(), from_generation.strip(), to_generation.strip())
        manifests: list[DurableServiceGraphManifest] = []
        for generation in request[1:]:
            try:
                record = self._manifest_resolver.get(request[0], self._namespace, generation)
            except (TypeError, ValueError):
                return _blocked(ServiceGraphChangeAnalysisBlockReason.DURABLE_MANIFEST_MISMATCH)
            reason = _manifest_failure(record, request[0], generation, self._namespace)
            if reason is not None:
                return _blocked(reason)
            assert record is not None
            manifests.append(record)
        try:
            from_plan = self._read_generation(request[1])
            to_plan = self._read_generation(request[2])
        except ValueError:
            return _blocked(ServiceGraphChangeAnalysisBlockReason.MALFORMED_GRAPH)
        for plan, record in zip((from_plan, to_plan), manifests, strict=True):
            if not _receipt_matches(plan, record):
                return _blocked(ServiceGraphChangeAnalysisBlockReason.DURABLE_RECEIPT_MISMATCH)
        receipt_from = _receipt(from_plan, self._namespace.value)
        receipt_to = _receipt(to_plan, self._namespace.value)
        return ServiceGraphChangeAnalysis(from_plan, receipt_from, to_plan, receipt_to).analyze(*request)

    def _read_generation(self, generation_id: str) -> GraphWritePlan:
        nodes = tuple(
            node
            for node in (_node_from_row(row) for row in self._run(self.NODES_QUERY))
            if node.props.get("generation_id") == generation_id
        )
        node_ids = {node.id for node in nodes}
        relations = tuple(
            relation
            for relation in (_relation_from_row(row) for row in self._run(self.RELATIONS_QUERY))
            if relation.props.get("generation_id") == generation_id
        )
        if any(relation.source_id not in node_ids or relation.target_id not in node_ids for relation in relations):
            raise ValueError("generation relation endpoint is missing")
        return GraphWritePlan(
            tuple(sorted(nodes, key=lambda node: node.id)), tuple(sorted(relations, key=lambda item: item.id))
        )

    def _run(self, query: str) -> list[object]:
        with self._driver.session() as session:  # type: ignore[union-attr]
            return list(session.run(query, namespace=self._namespace.value))  # type: ignore[union-attr]


def _manifest_failure(
    record: object, repo_id: str, generation_id: str, namespace: Neo4jNamespace
) -> ServiceGraphChangeAnalysisBlockReason | None:
    if record is None:
        return ServiceGraphChangeAnalysisBlockReason.MISSING_DURABLE_MANIFEST
    if type(record) is not DurableServiceGraphManifest:
        return ServiceGraphChangeAnalysisBlockReason.DURABLE_MANIFEST_MISMATCH
    manifest = record.manifest
    if (manifest.repo_id, manifest.generation_id, manifest.graph_namespace) != (repo_id, generation_id, namespace):
        return ServiceGraphChangeAnalysisBlockReason.DURABLE_MANIFEST_MISMATCH
    if record.state is not ManifestState.READY:
        return ServiceGraphChangeAnalysisBlockReason.DURABLE_MANIFEST_NOT_READY
    if not record.receipt_confirmed:
        return ServiceGraphChangeAnalysisBlockReason.DURABLE_RECEIPT_UNCONFIRMED
    if (
        type(record.node_count) is not int
        or type(record.relation_count) is not int
        or record.node_count < 0
        or record.relation_count < 0
        or not _is_nonblank(record.receipt_fingerprint)
    ):
        return ServiceGraphChangeAnalysisBlockReason.DURABLE_MANIFEST_MISMATCH
    return None


def _receipt_matches(plan: GraphWritePlan, record: DurableServiceGraphManifest) -> bool:
    return (
        record.node_count == len(plan.nodes)
        and record.relation_count == len(plan.relations)
        and record.receipt_fingerprint == Neo4jServiceGraphManifestRepository.receipt_fingerprint(plan)
    )


def _receipt(plan: GraphWritePlan, namespace: str) -> WriteReceipt:
    return WriteReceipt(True, len(plan.nodes), len(plan.relations), plan, namespace)


def _node_from_row(row: object) -> GraphNode:
    values = _as_mapping(row)
    labels = values.get("labels")
    properties = _as_mapping(values.get("properties"))
    if (
        not isinstance(labels, Sequence)
        or isinstance(labels, str)
        or len(labels) != 1
        or labels[0] not in Neo4jServiceGraphChangeImpactAdapter._NODE_TYPES
    ):
        raise ValueError("malformed node labels")
    node_id = properties.get("id")
    if not _is_nonblank(node_id) or properties.get("_ontoagent_namespace") is None:
        raise ValueError("malformed node storage")
    props = Neo4jGraphSink._decode_props(properties.get("_ontoagent_props"))
    if props.get("id") != node_id:
        raise ValueError("node identity mismatch")
    return GraphNode(node_id, labels[0], props)


def _relation_from_row(row: object) -> GraphRelation:
    values = _as_mapping(row)
    relation_id = values.get("relation_id")
    relation_type = values.get("relation_type")
    source_id = values.get("source_id")
    target_id = values.get("target_id")
    properties = _as_mapping(values.get("relation_properties"))
    if (
        not _is_nonblank(relation_id)
        or relation_type not in Neo4jServiceGraphChangeImpactAdapter._RELATION_TYPES
        or not _is_nonblank(source_id)
        or not _is_nonblank(target_id)
        or properties.get("_ontoagent_relation_id") != relation_id
    ):
        raise ValueError("malformed relation storage")
    return GraphRelation(
        relation_id,
        relation_type,
        source_id,
        target_id,
        Neo4jGraphSink._decode_props(properties.get("_ontoagent_props")),
    )


def _as_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("Neo4j row is not mapping-like")
    return value


def _blocked(reason: ServiceGraphChangeAnalysisBlockReason) -> ServiceGraphChangeAnalysisResult:
    return ServiceGraphChangeAnalysisResult(
        ServiceGraphChangeAnalysisStatus.BLOCKED, "unknown", None, None, (reason,), (), (), (), (), ()
    )


def _is_nonblank(value: object) -> bool:
    return type(value) is str and bool(value.strip())
