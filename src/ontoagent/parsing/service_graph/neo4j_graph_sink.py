from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import uuid4

from .graph_plan import GraphNode, GraphRelation, GraphWritePlan
from .graph_writer import GraphSink


class Neo4jDriver(Protocol):
    def session(self) -> object: ...


class Neo4jGraphSink(GraphSink):
    """A fail-closed, I3b-only Neo4j persistence adapter for graph write plans."""

    NODE_LABELS = frozenset({"ServiceDefinition", "Endpoint", "Evidence"})
    RELATION_TYPES = frozenset({"PROVIDES_ENDPOINT", "CONSUMES_ENDPOINT", "DEPENDS_ON", "SUPPORTED_BY_EVIDENCE"})
    _NODE_QUERIES = {
        label: (
            "UNWIND $rows AS row "
            f"MERGE (n:{label} {{id: row.id, _ontoagent_namespace: $namespace}}) "
            "SET n = {id: row.id, _ontoagent_props: row.encoded_props, _ontoagent_namespace: $namespace}"
        )
        for label in NODE_LABELS
    }
    _RELATION_QUERIES = {
        relation_type: (
            "UNWIND $rows AS row "
            "MATCH (source {id: row.source_id, _ontoagent_namespace: $namespace}) "
            "MATCH (target {id: row.target_id, _ontoagent_namespace: $namespace}) "
            f"MERGE (source)-[r:{relation_type} {{_ontoagent_relation_id: row.id, _ontoagent_namespace: $namespace}}]->(target) "
            "SET r = {_ontoagent_relation_id: row.id, _ontoagent_props: row.encoded_props, "
            "_ontoagent_namespace: $namespace}"
        )
        for relation_type in RELATION_TYPES
    }
    READ_NODES_QUERY = (
        "MATCH (n) WHERE n.id IN $node_ids AND n._ontoagent_namespace = $namespace "
        "RETURN labels(n) AS labels, properties(n) AS properties"
    )
    READ_RELATIONS_QUERY = (
        "MATCH (source)-[r]->(target) "
        "WHERE r._ontoagent_relation_id IN $relation_ids AND r._ontoagent_namespace = $namespace "
        "AND source._ontoagent_namespace = $namespace AND target._ontoagent_namespace = $namespace "
        "RETURN type(r) AS type, source.id AS source_id, target.id AS target_id, "
        "source._ontoagent_namespace AS source_namespace, target._ontoagent_namespace AS target_namespace, "
        "properties(r) AS properties"
    )
    _NODE_STORAGE_KEYS = frozenset({"id", "_ontoagent_props", "_ontoagent_namespace"})
    _RELATION_STORAGE_KEYS = frozenset({"_ontoagent_relation_id", "_ontoagent_props", "_ontoagent_namespace"})

    def __init__(self, driver: Neo4jDriver, *, namespace: str | None = None) -> None:
        self._driver = driver
        self._namespace = namespace or str(uuid4())
        self._node_ids: tuple[str, ...] = ()
        self._relation_ids: tuple[str, ...] = ()
        self._has_submitted_plan = False

    @property
    def graph_namespace(self) -> str:
        """The exact namespace used for both writes and readback queries."""
        return self._namespace

    @staticmethod
    def encode_props(props: Mapping[str, object]) -> str:
        """Encode arbitrary plan property values into a tagged, reversible JSON document."""
        return json.dumps(Neo4jGraphSink._encode_value(props), separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _encode_value(value: object) -> object:
        if value is None:
            return {"@ontoagent": "none"}
        if isinstance(value, tuple):
            return {"@ontoagent": "tuple", "items": [Neo4jGraphSink._encode_value(item) for item in value]}
        if isinstance(value, list):
            return {"@ontoagent": "list", "items": [Neo4jGraphSink._encode_value(item) for item in value]}
        if isinstance(value, Mapping):
            if not all(isinstance(key, str) for key in value):
                raise ValueError("plan property mappings require string keys")
            return {
                "@ontoagent": "mapping",
                "items": {key: Neo4jGraphSink._encode_value(item) for key, item in value.items()},
            }
        if isinstance(value, (str, int, float, bool)):
            return value
        raise ValueError(f"unsupported plan property value: {type(value).__name__}")

    @staticmethod
    def _decode_props(encoded: object) -> Mapping[str, object]:
        if not isinstance(encoded, str):
            raise ValueError("malformed stored properties")
        try:
            decoded = Neo4jGraphSink._decode_value(json.loads(encoded))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("malformed stored properties") from exc
        if not isinstance(decoded, Mapping) or not all(isinstance(key, str) for key in decoded):
            raise ValueError("stored properties must decode to a mapping")
        return decoded

    @staticmethod
    def _decode_value(value: object) -> object:
        if isinstance(value, list):
            raise ValueError("untagged list in encoded properties")
        if not isinstance(value, Mapping):
            if isinstance(value, (str, int, float, bool)):
                return value
            raise ValueError("unsupported encoded scalar")
        tag = value.get("@ontoagent")
        if tag == "none" and set(value) == {"@ontoagent"}:
            return None
        items = value.get("items")
        if tag in {"tuple", "list"} and set(value) == {"@ontoagent", "items"} and isinstance(items, list):
            decoded = [Neo4jGraphSink._decode_value(item) for item in items]
            return tuple(decoded) if tag == "tuple" else decoded
        if tag == "mapping" and set(value) == {"@ontoagent", "items"} and isinstance(items, Mapping):
            if not all(isinstance(key, str) for key in items):
                raise ValueError("encoded mapping has non-string key")
            return {key: Neo4jGraphSink._decode_value(item) for key, item in items.items()}
        raise ValueError("invalid encoded value tag")

    def write(self, plan: GraphWritePlan) -> None:
        self._validate_plan(plan)
        with self._driver.session() as session:  # type: ignore[union-attr]
            for label in self.NODE_LABELS:
                rows = [
                    {"id": node.id, "encoded_props": self.encode_props(node.props)}
                    for node in plan.nodes
                    if node.node_type == label
                ]
                if rows:
                    session.run(self._NODE_QUERIES[label], rows=rows, namespace=self._namespace)  # type: ignore[union-attr]
            for relation_type in self.RELATION_TYPES:
                rows = [
                    {
                        "id": relation.id,
                        "source_id": relation.source_id,
                        "target_id": relation.target_id,
                        "encoded_props": self.encode_props(relation.props),
                    }
                    for relation in plan.relations
                    if relation.relation_type == relation_type
                ]
                if rows:
                    session.run(self._RELATION_QUERIES[relation_type], rows=rows, namespace=self._namespace)  # type: ignore[union-attr]
        self._node_ids = tuple(node.id for node in plan.nodes)
        self._relation_ids = tuple(relation.id for relation in plan.relations)
        self._has_submitted_plan = True

    def readback(self) -> GraphWritePlan:
        if not self._has_submitted_plan:
            raise ValueError("readback requires a submitted plan")
        with self._driver.session() as session:  # type: ignore[union-attr]
            node_rows = list(session.run(self.READ_NODES_QUERY, node_ids=self._node_ids, namespace=self._namespace))  # type: ignore[union-attr]
            relation_rows = list(  # type: ignore[union-attr]
                session.run(self.READ_RELATIONS_QUERY, relation_ids=self._relation_ids, namespace=self._namespace)
            )
        nodes_by_id = {node.id: node for node in (self._node_from_row(row) for row in node_rows)}
        if set(nodes_by_id) != set(self._node_ids) or len(nodes_by_id) != len(node_rows):
            raise ValueError("readback node IDs are missing or duplicated")
        relations_by_id = {
            relation.id: relation
            for relation in (self._relation_from_row(row, set(nodes_by_id)) for row in relation_rows)
        }
        if set(relations_by_id) != set(self._relation_ids) or len(relations_by_id) != len(relation_rows):
            raise ValueError("readback relation IDs are missing or duplicated")
        return GraphWritePlan(
            tuple(nodes_by_id[node_id] for node_id in self._node_ids),
            tuple(relations_by_id[relation_id] for relation_id in self._relation_ids),
        )

    def _node_from_row(self, row: object) -> GraphNode:
        labels = self._record_value(row, "labels")
        properties = self._record_value(row, "properties")
        if (
            not isinstance(labels, Sequence)
            or isinstance(labels, str)
            or len(labels) != 1
            or labels[0] not in self.NODE_LABELS
        ):
            raise ValueError("unexpected or malformed node labels")
        if not isinstance(properties, Mapping) or set(properties) != self._NODE_STORAGE_KEYS:
            raise ValueError("unexpected node properties")
        node_id = properties.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("malformed node id")
        if properties.get("_ontoagent_namespace") != self._namespace:
            raise ValueError("node is outside sink namespace")
        return GraphNode(node_id, labels[0], self._decode_props(properties.get("_ontoagent_props")))

    def _relation_from_row(self, row: object, node_ids: set[str]) -> GraphRelation:
        relation_type = self._record_value(row, "type")
        source_id = self._record_value(row, "source_id")
        target_id = self._record_value(row, "target_id")
        source_namespace = self._record_value(row, "source_namespace")
        target_namespace = self._record_value(row, "target_namespace")
        properties = self._record_value(row, "properties")
        if (
            not isinstance(relation_type, str)
            or relation_type not in self.RELATION_TYPES
            or not isinstance(source_id, str)
            or not isinstance(target_id, str)
            or source_namespace != self._namespace
            or target_namespace != self._namespace
        ):
            raise ValueError("unexpected or malformed relationship")
        if source_id not in node_ids or target_id not in node_ids:
            raise ValueError("relationship endpoint is outside submitted nodes")
        if not isinstance(properties, Mapping) or set(properties) != self._RELATION_STORAGE_KEYS:
            raise ValueError("unexpected relationship properties")
        relation_id = properties.get("_ontoagent_relation_id")
        if not isinstance(relation_id, str) or not relation_id:
            raise ValueError("malformed relationship id")
        if properties.get("_ontoagent_namespace") != self._namespace:
            raise ValueError("relationship is outside sink namespace")
        return GraphRelation(
            relation_id, relation_type, source_id, target_id, self._decode_props(properties.get("_ontoagent_props"))
        )

    @staticmethod
    def _record_value(row: object, key: str) -> object:
        if isinstance(row, Mapping):
            return row.get(key)
        getter = getattr(row, "get", None)
        if callable(getter):
            return getter(key)
        raise ValueError("malformed Neo4j result row")

    def _validate_plan(self, plan: GraphWritePlan) -> None:
        node_ids = [node.id for node in plan.nodes]
        relation_ids = [relation.id for relation in plan.relations]
        if len(set(node_ids)) != len(node_ids) or len(set(relation_ids)) != len(relation_ids):
            raise ValueError("plan contains duplicate IDs")
        if any(
            not isinstance(node.id, str) or not node.id or node.node_type not in self.NODE_LABELS for node in plan.nodes
        ):
            raise ValueError("plan contains an unexpected node")
        if any(
            not isinstance(relation.id, str) or not relation.id or relation.relation_type not in self.RELATION_TYPES
            for relation in plan.relations
        ):
            raise ValueError("plan contains an unexpected relationship")
        known_nodes = set(node_ids)
        if any(
            relation.source_id not in known_nodes or relation.target_id not in known_nodes
            for relation in plan.relations
        ):
            raise ValueError("plan relationship endpoint is missing")
        for node in plan.nodes:
            self._validate_node_provenance(node)
        for relation in plan.relations:
            self._validate_relation_provenance(relation)
        for item in (*plan.nodes, *plan.relations):
            self.encode_props(item.props)

    @staticmethod
    def _validate_node_provenance(node: GraphNode) -> None:
        if node.props.get("id") != node.id:
            raise ValueError("node provenance id must match node id")
        if not all(
            Neo4jGraphSink._is_nonblank_string(node.props.get(name))
            for name in ("repo_id", "generation_id", "source_revision", "canonical_key")
        ):
            raise ValueError("node provenance is incomplete")
        if not Neo4jGraphSink._has_evidence_ids(node.props.get("evidence_ids")):
            raise ValueError("node provenance evidence IDs are invalid")

    @staticmethod
    def _validate_relation_provenance(relation: GraphRelation) -> None:
        if not all(
            Neo4jGraphSink._is_nonblank_string(relation.props.get(name))
            for name in ("canonical_key", "generation_id", "source_revision")
        ):
            raise ValueError("relationship provenance is incomplete")
        if not Neo4jGraphSink._has_evidence_ids(relation.props.get("evidence_ids")):
            raise ValueError("relationship provenance evidence IDs are invalid")

    @staticmethod
    def _is_nonblank_string(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _has_evidence_ids(value: object) -> bool:
        return (
            isinstance(value, tuple)
            and bool(value)
            and all(Neo4jGraphSink._is_nonblank_string(evidence_id) for evidence_id in value)
        )
