"""Dedicated Neo4j adapter for the method-fact vertical slice."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Protocol

from .method_graph_writer import MethodGraphScope, MethodGraphWritePlan, fact_from_dict
from .methods import MethodFacts


class Neo4jDriver(Protocol):
    def session(self) -> object: ...


class Neo4jMethodGraphSink:
    """Persist method facts independently of the Endpoint graph writer and manifests."""

    NODE_LABELS = frozenset(
        {
            "ServiceOperation",
            "ImplementationMethod",
            "ConsumerMethodCall",
            "OperationBinding",
            "MethodEvidence",
            "MethodUnresolved",
            "MethodCallTarget",
        }
    )
    RELATION_TYPES = frozenset(
        {
            "METHOD_EVIDENCE",
            "OPERATION_BINDING",
            "BINDS_IMPLEMENTATION",
            "CALLER_METHOD",
            "CALLS_OPERATION",
            "CALLS_ENDPOINT_TARGET",
        }
    )
    _NODE_QUERIES = {
        label: (f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id, namespace: $namespace}}) SET n = row")
        for label in NODE_LABELS
    }
    _RELATION_QUERIES = {
        relation_type: (
            "UNWIND $rows AS row "
            "MATCH (source {id: row.sourceId, namespace: $namespace}) "
            "MATCH (target {id: row.targetId, namespace: $namespace}) "
            f"MERGE (source)-[r:{relation_type} {{id: row.id, namespace: $namespace}}]->(target) "
            "SET r = row"
        )
        for relation_type in RELATION_TYPES
    }
    READ_NODES_QUERY = (
        "MATCH (n) WHERE n.id IN $ids AND n.namespace = $namespace "
        "RETURN labels(n) AS labels, properties(n) AS properties"
    )
    READ_RELATIONS_QUERY = (
        "MATCH (source)-[r]->(target) WHERE r.id IN $ids AND r.namespace = $namespace "
        "AND source.namespace = $namespace AND target.namespace = $namespace "
        "RETURN type(r) AS type, properties(r) AS properties"
    )

    def __init__(self, driver: Neo4jDriver, scope: MethodGraphScope) -> None:
        self._driver = driver
        self._scope = scope
        self._node_ids: tuple[str, ...] = ()
        self._relation_ids: tuple[str, ...] = ()
        self._submitted = False

    @property
    def scope(self) -> MethodGraphScope:
        return self._scope

    def write(self, plan: MethodGraphWritePlan) -> None:
        if plan.scope != self._scope:
            raise ValueError("method graph namespace or scope mismatch")
        nodes, relations = self._records(plan)
        with self._driver.session() as session:  # type: ignore[union-attr]
            for label in self.NODE_LABELS:
                rows = [row for row in nodes if row["label"] == label]
                if rows:
                    session.run(
                        self._NODE_QUERIES[label], rows=[row["props"] for row in rows], namespace=self._scope.namespace
                    )  # type: ignore[union-attr]
            for relation_type in self.RELATION_TYPES:
                rows = [row for row in relations if row["type"] == relation_type]
                if rows:
                    session.run(
                        self._RELATION_QUERIES[relation_type],
                        rows=[row["props"] for row in rows],
                        namespace=self._scope.namespace,
                    )  # type: ignore[union-attr]
        self._node_ids = tuple(row["props"]["id"] for row in nodes)
        self._relation_ids = tuple(row["props"]["id"] for row in relations)
        self._submitted = True

    def readback(self, scope: MethodGraphScope) -> MethodGraphWritePlan:
        if scope != self._scope:
            raise ValueError("method graph namespace or scope mismatch")
        if not self._submitted:
            raise ValueError("method graph has not been written")
        if not self._node_ids:
            return MethodGraphWritePlan(scope, ())
        with self._driver.session() as session:  # type: ignore[union-attr]
            node_rows = list(session.run(self.READ_NODES_QUERY, ids=self._node_ids, namespace=scope.namespace))  # type: ignore[union-attr]
            relation_rows = list(
                session.run(self.READ_RELATIONS_QUERY, ids=self._relation_ids, namespace=scope.namespace)
            )  # type: ignore[union-attr]
        if len(node_rows) != len(self._node_ids) or len(relation_rows) != len(self._relation_ids):
            raise ValueError("method graph readback receipt mismatch")
        payloads: dict[str, Mapping[str, object]] = {}
        stored_nodes: dict[str, Mapping[str, object]] = {}
        for row in node_rows:
            labels, props = self._value(row, "labels"), self._value(row, "properties")
            if (
                not isinstance(labels, list)
                or len(labels) != 1
                or labels[0] not in self.NODE_LABELS
                or not isinstance(props, Mapping)
            ):
                raise ValueError("malformed method graph node")
            self._validate_scope(props)
            node_id = props.get("id")
            if not isinstance(node_id, str) or node_id in stored_nodes:
                raise ValueError("malformed or duplicate method graph node id")
            stored_nodes[node_id] = props
            fact_id, payload = props.get("factId"), props.get("factPayload")
            if not isinstance(fact_id, str) or not isinstance(payload, str):
                raise ValueError("malformed method graph payload")
            decoded = json.loads(payload)
            if not isinstance(decoded, Mapping):
                raise ValueError("malformed method graph payload")
            existing = payloads.setdefault(fact_id, decoded)
            if existing != decoded:
                raise ValueError("inconsistent method graph payload")
        stored_relations: dict[str, Mapping[str, object]] = {}
        for row in relation_rows:
            relation_type, props = self._value(row, "type"), self._value(row, "properties")
            if relation_type not in self.RELATION_TYPES or not isinstance(props, Mapping):
                raise ValueError("malformed method graph relationship")
            self._validate_scope(props)
            relation_id = props.get("id")
            if not isinstance(relation_id, str) or relation_id in stored_relations:
                raise ValueError("malformed or duplicate method graph relationship id")
            stored_relations[relation_id] = props
        facts = tuple(fact_from_dict(payload) for _, payload in sorted(payloads.items()))
        result = MethodGraphWritePlan(scope, facts)
        expected_nodes, expected_relations = self._records(result)
        expected_node_props = {str(row["props"]["id"]): row["props"] for row in expected_nodes}
        expected_relation_props = {str(row["props"]["id"]): row["props"] for row in expected_relations}
        if (
            tuple(expected_node_props) != self._node_ids
            or tuple(expected_relation_props) != self._relation_ids
            or stored_nodes != expected_node_props
            or stored_relations != expected_relation_props
        ):
            raise ValueError("method graph readback does not reconstruct receipt")
        return result

    def _records(self, plan: MethodGraphWritePlan) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        nodes: list[dict[str, object]] = []
        relations: list[dict[str, object]] = []
        node_ids: set[str] = set()

        def append_node(label: str, node_id: str, fact: MethodFacts, fact_id: str, payload: str) -> None:
            if node_id not in node_ids:
                nodes.append(self._node(label, node_id, fact, fact_id, payload))
                node_ids.add(node_id)

        for fact in plan.facts:
            fact_id = plan._fact_id(fact)
            payload = json.dumps(fact.to_dict(), sort_keys=True, separators=(",", ":"))
            for label, items in (
                ("ServiceOperation", fact.operations),
                ("ImplementationMethod", fact.implementations),
                ("ConsumerMethodCall", fact.consumer_calls),
                ("OperationBinding", fact.bindings),
                ("MethodEvidence", fact.evidences),
                ("MethodUnresolved", fact.unresolved),
            ):
                actual_label = "MethodUnresolved" if label == "MethodUnresolved" else label
                for item in items:
                    append_node(actual_label, item.id, fact, fact_id, payload)
            for call in fact.consumer_calls:
                if call.target_kind == "endpoint":
                    target_id = f"endpoint-target:{call.id}"
                    append_node("MethodCallTarget", target_id, fact, fact_id, payload)
                    relations.append(self._relation("CALLS_ENDPOINT_TARGET", call.id, target_id, fact, fact_id))
                else:
                    try:
                        operation_id = plan.operation_id_for(call.target_reference)
                    except ValueError:
                        target_id = f"endpoint-target:{call.id}"
                        append_node("MethodCallTarget", target_id, fact, fact_id, payload)
                        relations.append(self._relation("CALLS_ENDPOINT_TARGET", call.id, target_id, fact, fact_id))
                    else:
                        relations.append(self._relation("CALLS_OPERATION", call.id, operation_id, fact, fact_id))
                relations.append(self._relation("CALLER_METHOD", call.caller_implementation_id, call.id, fact, fact_id))
            for binding in fact.bindings:
                relations.append(self._relation("OPERATION_BINDING", binding.id, binding.operation_id, fact, fact_id))
                if binding.implementation_id is not None:
                    relations.append(
                        self._relation("BINDS_IMPLEMENTATION", binding.id, binding.implementation_id, fact, fact_id)
                    )
            for item in (
                *fact.operations,
                *fact.implementations,
                *fact.consumer_calls,
                *fact.bindings,
                *fact.unresolved,
            ):
                for evidence_id in item.evidence_ids:
                    relations.append(self._relation("METHOD_EVIDENCE", item.id, evidence_id, fact, fact_id))
        nodes.sort(key=lambda row: str(row["props"]["id"]))
        relations.sort(key=lambda row: str(row["props"]["id"]))
        return nodes, relations

    def _node(self, label: str, node_id: str, fact: MethodFacts, fact_id: str, payload: str) -> dict[str, object]:
        if label not in self.NODE_LABELS:
            raise ValueError("unsupported method graph node label")
        return {"label": label, "props": self._props(node_id, fact, fact_id, payload)}

    def _relation(
        self, relation_type: str, source_id: str, target_id: str, fact: MethodFacts, fact_id: str
    ) -> dict[str, object]:
        relation_id = hashlib.sha256(f"{relation_type}:{source_id}:{target_id}:{fact_id}".encode()).hexdigest()
        return {
            "type": relation_type,
            "props": {**self._props(relation_id, fact, fact_id, ""), "sourceId": source_id, "targetId": target_id},
        }

    def _props(self, item_id: str, fact: MethodFacts, fact_id: str, payload: str) -> dict[str, object]:
        return {
            "id": item_id,
            "workspaceId": self._scope.workspace_id,
            "generationId": self._scope.generation_id,
            "namespace": self._scope.namespace,
            "repoId": fact.repo_id,
            "sourceRevision": fact.source_revision,
            "factId": fact_id,
            "factPayload": payload,
        }

    def _validate_scope(self, props: Mapping[str, object]) -> None:
        if (props.get("workspaceId"), props.get("generationId"), props.get("namespace")) != (
            self._scope.workspace_id,
            self._scope.generation_id,
            self._scope.namespace,
        ):
            raise ValueError("method graph row is outside workspace namespace or generation")

    @staticmethod
    def _value(row: object, key: str) -> object:
        if isinstance(row, Mapping):
            return row.get(key)
        getter = getattr(row, "get", None)
        if callable(getter):
            return getter(key)
        raise ValueError("malformed Neo4j result")
