"""Durable-manifest-gated, read-only queries over persisted service graphs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .generation_manifest import (
    ManifestBlockReason,
    ManifestResolution,
    ManifestResolutionStatus,
    Neo4jNamespace,
)
from .neo4j_graph_sink import Neo4jGraphSink
from .query import (
    ServiceGraphNodeResult,
    ServiceGraphQueryBlockReason,
    ServiceGraphQueryResult,
    ServiceGraphQueryStatus,
    ServiceGraphRelationResult,
)


class Neo4jDriver(Protocol):
    def session(self) -> object: ...


class ServiceGraphManifestResolver(Protocol):
    """The durable ACTIVE manifest gate required before graph reads."""

    def resolve(self, repo_id: str, generation_id: str, namespace: Neo4jNamespace) -> ManifestResolution: ...


class Neo4jServiceGraphQueryAdapter:
    """Read persisted service graph data only after an exact ACTIVE READY resolution."""

    SERVICE_DIRECTORY_QUERY = (
        "MATCH (n:ServiceDefinition) WHERE n._ontoagent_namespace = $namespace "
        "RETURN labels(n) AS labels, properties(n) AS properties"
    )
    ENDPOINTS_QUERY = (
        "MATCH (n:Endpoint) WHERE n._ontoagent_namespace = $namespace "
        "RETURN labels(n) AS labels, properties(n) AS properties"
    )
    DEPENDENCIES_QUERY = (
        "MATCH (source)-[r:DEPENDS_ON]->(target) "
        "WHERE r._ontoagent_namespace = $namespace "
        "AND source._ontoagent_namespace = $namespace AND target._ontoagent_namespace = $namespace "
        "AND (source.id = $service_id OR EXISTS { "
        "MATCH (:ServiceDefinition {id: $service_id, _ontoagent_namespace: $namespace})-[:PROVIDES_ENDPOINT|CONSUMES_ENDPOINT]->(source) "
        "}) "
        "RETURN labels(source) AS source_labels, properties(source) AS source_properties, "
        "labels(target) AS target_labels, properties(target) AS target_properties, type(r) AS relation_type, "
        "r._ontoagent_relation_id AS relation_id, properties(r) AS relation_properties"
    )
    EVIDENCE_OWNER_NODE_QUERY = (
        "MATCH (n) WHERE n.id = $entity_or_relation_id AND n._ontoagent_namespace = $namespace "
        "RETURN labels(n) AS labels, properties(n) AS properties"
    )
    EVIDENCE_OWNER_RELATION_QUERY = (
        "MATCH (source)-[r]->(target) WHERE r._ontoagent_relation_id = $entity_or_relation_id "
        "AND r._ontoagent_namespace = $namespace AND source._ontoagent_namespace = $namespace "
        "AND target._ontoagent_namespace = $namespace "
        "RETURN labels(source) AS source_labels, properties(source) AS source_properties, type(r) AS relation_type, "
        "r._ontoagent_relation_id AS relation_id, properties(r) AS relation_properties"
    )
    EVIDENCE_NODES_QUERY = (
        "MATCH (n:Evidence) WHERE n.id IN $evidence_ids AND n._ontoagent_namespace = $namespace "
        "RETURN labels(n) AS labels, properties(n) AS properties"
    )

    def __init__(
        self, driver: Neo4jDriver, manifest_resolver: ServiceGraphManifestResolver, namespace: Neo4jNamespace
    ) -> None:
        if type(namespace) is not Neo4jNamespace:
            raise ValueError("namespace must be a Neo4jNamespace")
        self._driver = driver
        self._manifest_resolver = manifest_resolver
        self._namespace = namespace

    def service_directory(self, repo_id: object, generation_id: object) -> ServiceGraphQueryResult:
        request, namespace, blocked = self._authorize(repo_id, generation_id)
        if blocked is not None:
            return blocked
        assert request is not None and namespace is not None
        try:
            nodes = tuple(
                node
                for node in self._read_nodes(self.SERVICE_DIRECTORY_QUERY, {"namespace": namespace})
                if _matches_identity(node, *request)
            )
        except ValueError:
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_GRAPH)
        return _ready(*request, nodes=nodes)

    def find_endpoint_providers(
        self, repo_id: object, generation_id: object, endpoint_key: object
    ) -> ServiceGraphQueryResult:
        return self._find_endpoints(repo_id, generation_id, endpoint_key, "provider")

    def find_endpoint_consumers(
        self, repo_id: object, generation_id: object, endpoint_key: object
    ) -> ServiceGraphQueryResult:
        return self._find_endpoints(repo_id, generation_id, endpoint_key, "consumer")

    def find_service_dependencies(
        self, repo_id: object, generation_id: object, service_id: object
    ) -> ServiceGraphQueryResult:
        request, namespace, blocked = self._authorize(repo_id, generation_id)
        if blocked is not None:
            return blocked
        if not _is_nonblank(service_id):
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_REQUEST)
        assert request is not None and namespace is not None
        try:
            relations, nodes = self._read_dependencies(namespace, service_id.strip())
        except ValueError:
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_GRAPH)
        selected = tuple(relation for relation in relations if _matches_identity(nodes[relation.source_id], *request))
        selected_node_ids = {node_id for relation in selected for node_id in (relation.source_id, relation.target_id)}
        return _ready(*request, nodes=tuple(nodes[node_id] for node_id in selected_node_ids), relations=selected)

    def get_evidence(
        self, repo_id: object, generation_id: object, entity_or_relation_id: object
    ) -> ServiceGraphQueryResult:
        request, namespace, blocked = self._authorize(repo_id, generation_id)
        if blocked is not None:
            return blocked
        if not _is_nonblank(entity_or_relation_id):
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_REQUEST)
        assert request is not None and namespace is not None
        item_id = entity_or_relation_id.strip()
        try:
            node_rows = self._run(
                self.EVIDENCE_OWNER_NODE_QUERY, {"namespace": namespace, "entity_or_relation_id": item_id}
            )
            if node_rows:
                owner = _node_from_row(node_rows[0])
                if not _matches_identity(owner, *request):
                    return _blocked(
                        ServiceGraphQueryBlockReason.REPO_MISMATCH
                        if owner.properties.get("repo_id") != request[0]
                        else ServiceGraphQueryBlockReason.GENERATION_MISMATCH
                    )
                evidence_ids = _evidence_ids(owner.properties)
            else:
                relation_rows = self._run(
                    self.EVIDENCE_OWNER_RELATION_QUERY,
                    {"namespace": namespace, "entity_or_relation_id": item_id},
                )
                if not relation_rows:
                    return _blocked(ServiceGraphQueryBlockReason.ENTITY_OR_RELATION_NOT_FOUND)
                owner, relation = _relation_owner_from_row(relation_rows[0])
                if not _matches_identity(owner, *request):
                    return _blocked(
                        ServiceGraphQueryBlockReason.REPO_MISMATCH
                        if owner.properties.get("repo_id") != request[0]
                        else ServiceGraphQueryBlockReason.GENERATION_MISMATCH
                    )
                evidence_ids = _evidence_ids(relation.properties)
            nodes = self._read_nodes(self.EVIDENCE_NODES_QUERY, {"namespace": namespace, "evidence_ids": evidence_ids})
        except ValueError:
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_GRAPH)
        return _ready(*request, nodes=tuple(node for node in nodes if node.id in evidence_ids))

    def _find_endpoints(
        self, repo_id: object, generation_id: object, endpoint_key: object, role: str
    ) -> ServiceGraphQueryResult:
        request, namespace, blocked = self._authorize(repo_id, generation_id)
        if blocked is not None:
            return blocked
        if not _is_nonblank(endpoint_key):
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_REQUEST)
        assert request is not None and namespace is not None
        try:
            nodes = tuple(
                node
                for node in self._read_nodes(self.ENDPOINTS_QUERY, {"namespace": namespace})
                if _matches_identity(node, *request)
                and node.properties.get("canonical_key") == endpoint_key.strip()
                and node.properties.get("role") == role
            )
        except ValueError:
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_GRAPH)
        return _ready(*request, nodes=nodes)

    def _authorize(
        self, repo_id: object, generation_id: object
    ) -> tuple[tuple[str, str] | None, str | None, ServiceGraphQueryResult | None]:
        if not _is_nonblank(repo_id) or not _is_nonblank(generation_id):
            return None, None, _blocked(ServiceGraphQueryBlockReason.MALFORMED_REQUEST)
        request = (repo_id.strip(), generation_id.strip())
        resolution = self._manifest_resolver.resolve(*request, self._namespace)
        if resolution.status is ManifestResolutionStatus.BLOCKED:
            return None, None, _blocked(*(_map_manifest_reason(reason) for reason in resolution.reasons))
        if resolution.status is not ManifestResolutionStatus.READY or resolution.binding is None:
            return None, None, _blocked(ServiceGraphQueryBlockReason.MALFORMED_RECORD)
        manifest = resolution.binding.manifest
        if manifest.repo_id != request[0]:
            return None, None, _blocked(ServiceGraphQueryBlockReason.REPO_MISMATCH)
        if manifest.generation_id != request[1]:
            return None, None, _blocked(ServiceGraphQueryBlockReason.GENERATION_MISMATCH)
        if manifest.graph_namespace != self._namespace:
            return None, None, _blocked(ServiceGraphQueryBlockReason.NAMESPACE_MISMATCH)
        return request, manifest.graph_namespace.value, None

    def _read_nodes(self, query: str, params: Mapping[str, object]) -> tuple[ServiceGraphNodeResult, ...]:
        return tuple(_node_from_row(row) for row in self._run(query, params))

    def _read_dependencies(
        self, namespace: str, service_id: str
    ) -> tuple[tuple[ServiceGraphRelationResult, ...], dict[str, ServiceGraphNodeResult]]:
        relations: list[ServiceGraphRelationResult] = []
        nodes: dict[str, ServiceGraphNodeResult] = {}
        for row in self._run(self.DEPENDENCIES_QUERY, {"namespace": namespace, "service_id": service_id}):
            source, target, relation = _dependency_from_row(row)
            if (source.id in nodes and nodes[source.id] != source) or (
                target.id in nodes and nodes[target.id] != target
            ):
                raise ValueError("inconsistent node rows")
            nodes[source.id] = source
            nodes[target.id] = target
            relations.append(relation)
        return tuple(relations), nodes

    def _run(self, query: str, params: Mapping[str, object]) -> list[object]:
        with self._driver.session() as session:  # type: ignore[union-attr]
            return list(session.run(query, **params))  # type: ignore[union-attr]


def _node_from_row(row: object) -> ServiceGraphNodeResult:
    values = _as_mapping(row)
    labels = values.get("labels")
    if (
        not isinstance(labels, (list, tuple))
        or len(labels) != 1
        or labels[0] not in {"ServiceDefinition", "Endpoint", "Evidence"}
    ):
        raise ValueError("malformed node labels")
    properties = _as_mapping(values.get("properties"))
    node_id = properties.get("id")
    if not _is_nonblank(node_id):
        raise ValueError("malformed node id")
    decoded = Neo4jGraphSink._decode_props(properties.get("_ontoagent_props"))
    if decoded.get("id") != node_id:
        raise ValueError("node identity mismatch")
    return ServiceGraphNodeResult(node_id, labels[0], decoded)


def _dependency_from_row(
    row: object,
) -> tuple[ServiceGraphNodeResult, ServiceGraphNodeResult, ServiceGraphRelationResult]:
    values = _as_mapping(row)
    source = _node_from_row({"labels": values.get("source_labels"), "properties": values.get("source_properties")})
    target = _node_from_row({"labels": values.get("target_labels"), "properties": values.get("target_properties")})
    relation = _relation_from_parts(values, source.id, target.id)
    return source, target, relation


def _relation_owner_from_row(row: object) -> tuple[ServiceGraphNodeResult, ServiceGraphRelationResult]:
    values = _as_mapping(row)
    source = _node_from_row({"labels": values.get("source_labels"), "properties": values.get("source_properties")})
    return source, _relation_from_parts(values, source.id, source.id)


def _relation_from_parts(values: Mapping[str, object], source_id: str, target_id: str) -> ServiceGraphRelationResult:
    relation_id = values.get("relation_id")
    relation_type = values.get("relation_type")
    if not _is_nonblank(relation_id) or relation_type not in {
        "PROVIDES_ENDPOINT",
        "CONSUMES_ENDPOINT",
        "DEPENDS_ON",
        "SUPPORTED_BY_EVIDENCE",
    }:
        raise ValueError("malformed relation")
    properties = _as_mapping(values.get("relation_properties"))
    if properties.get("_ontoagent_relation_id") != relation_id:
        raise ValueError("relation identity mismatch")
    return ServiceGraphRelationResult(
        relation_id,
        relation_type,
        source_id,
        target_id,
        Neo4jGraphSink._decode_props(properties.get("_ontoagent_props")),
    )


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    raise ValueError("Neo4j row is not mapping-like")


def _matches_identity(node: ServiceGraphNodeResult, repo_id: str, generation_id: str) -> bool:
    return node.properties.get("repo_id") == repo_id and node.properties.get("generation_id") == generation_id


def _evidence_ids(properties: Mapping[str, object]) -> tuple[str, ...]:
    value = properties.get("evidence_ids")
    if not isinstance(value, tuple) or not value or not all(_is_nonblank(item) for item in value):
        raise ValueError("malformed evidence ids")
    return value


def _map_manifest_reason(reason: ManifestBlockReason) -> ServiceGraphQueryBlockReason:
    try:
        return ServiceGraphQueryBlockReason(reason.value)
    except ValueError:
        return ServiceGraphQueryBlockReason.MALFORMED_RECORD


def _ready(
    repo_id: str,
    generation_id: str,
    *,
    nodes: tuple[ServiceGraphNodeResult, ...] = (),
    relations: tuple[ServiceGraphRelationResult, ...] = (),
) -> ServiceGraphQueryResult:
    return ServiceGraphQueryResult(ServiceGraphQueryStatus.READY, repo_id, generation_id, (), nodes, relations)


def _blocked(*reasons: ServiceGraphQueryBlockReason) -> ServiceGraphQueryResult:
    return ServiceGraphQueryResult(ServiceGraphQueryStatus.BLOCKED, "service-graph", None, reasons, (), ())


def _is_nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
