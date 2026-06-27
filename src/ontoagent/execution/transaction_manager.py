"""Transaction manager for atomic Neo4j operations with whitelist validation."""

from __future__ import annotations

from typing import Any

from ontoagent.domain.schema import RELATION_CONSTRAINTS, VALID_ENTITY_LABELS


class TransactionManager:
    """Wraps a Neo4j driver to execute multiple Cypher operations atomically.

    All operations run inside a single transaction: if any fails, the entire
    batch is rolled back.
    """

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    def run_atomic(self, operations: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Execute a list of Cypher operations in a single transaction.

        Args:
            operations: Each dict must have ``cypher`` (str) and may have
                ``params`` (dict).

        Returns:
            List of result-sets, one per operation.

        Raises:
            Exception: Re-raises any driver error after rollback.
        """
        with self._driver.session() as session:
            tx = session.begin_transaction()
            results: list[list[dict[str, Any]]] = []
            try:
                for op in operations:
                    result = tx.run(op["cypher"], op.get("params", {}))
                    results.append([record.data() for record in result])
                tx.commit()
                return results
            except Exception:
                tx.rollback()
                raise


class Neo4jTransaction:
    """Whitelist-validated wrapper around a raw Neo4j transaction.

    Only entity labels from ``VALID_ENTITY_LABELS`` (plus internal labels) and
    relation types from ``RELATION_CONSTRAINTS`` are allowed.  Labels and
    relation types are escaped with backticks in the generated Cypher to
    prevent injection.
    """

    _EXTRA_LABELS = frozenset({"TempRequest", "TempDiagnosis", "SagaExecution"})

    def __init__(self, tx: Any) -> None:
        self._tx = tx
        self._valid_labels = set(VALID_ENTITY_LABELS) | self._EXTRA_LABELS
        self._valid_rels = set(RELATION_CONSTRAINTS.keys())

    def create_entity(self, label: str, properties: dict[str, Any]) -> Any:
        """Create a node with *label* and *properties*.

        Raises:
            ValueError: If *label* is not in the whitelist.
        """
        if label not in self._valid_labels:
            raise ValueError(f"Invalid label: {label}")
        return self._tx.run(f"CREATE (n:`{label}` $props) RETURN n", {"props": properties})

    def create_relation(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> Any:
        """Create a relation of *rel_type* between two nodes matched by id.

        Raises:
            ValueError: If *rel_type* is not in the whitelist.
        """
        if rel_type not in self._valid_rels:
            raise ValueError(f"Invalid relation type: {rel_type}")
        params: dict[str, Any] = {"from": from_id, "to": to_id}
        if properties:
            params["props"] = properties
            return self._tx.run(
                f"MATCH (a {{id: $from}}), (b {{id: $to}}) CREATE (a)-[r:`{rel_type}` $props]->(b) RETURN r",
                params,
            )
        return self._tx.run(
            f"MATCH (a {{id: $from}}), (b {{id: $to}}) CREATE (a)-[r:`{rel_type}`]->(b) RETURN r",
            params,
        )
