from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .generation_manifest import (
    ActiveServiceGraphBinding,
    ManifestBlockReason,
    ManifestResolution,
    ManifestResolutionStatus,
    ManifestState,
    Neo4jNamespace,
    ServiceGraphManifest,
    ServiceGraphManifestRegistry,
)
from .graph_plan import GraphWritePlan
from .graph_writer import WriteReceipt


class Neo4jDriver(Protocol):
    def session(self) -> object: ...


class ManifestPublicationStatus(StrEnum):
    PUBLISHED = "published"
    REJECTED_STALE_ACTIVE = "rejected_stale_active"
    REJECTED_CANDIDATE_NOT_READY = "rejected_candidate_not_ready"


@dataclass(frozen=True)
class ManifestPublicationResult:
    status: ManifestPublicationStatus
    active_generation_id: str | None


@dataclass(frozen=True)
class DurableServiceGraphManifest:
    """The persisted receipt-confirmed state for one service graph generation."""

    manifest: ServiceGraphManifest
    state: ManifestState
    receipt_confirmed: bool
    node_count: int | None
    relation_count: int | None
    receipt_fingerprint: str | None

    def __post_init__(self) -> None:
        if self.state is ManifestState.READY and (
            not self.receipt_confirmed
            or type(self.node_count) is not int
            or type(self.relation_count) is not int
            or not _is_nonblank(self.receipt_fingerprint)
        ):
            raise ValueError("READY manifests require a confirmed receipt fingerprint and counts")


class Neo4jServiceGraphManifestRepository:
    """Durable Neo4j manifest store with an atomic ACTIVE generation pointer."""

    MANIFEST_LABEL = "OntoAgentServiceGraphManifest"
    ACTIVE_LABEL = "OntoAgentServiceGraphActive"
    ENSURE_MANIFEST_CONSTRAINT = (
        "CREATE CONSTRAINT ontoagent_service_graph_manifest_identity IF NOT EXISTS "
        "FOR (n:OntoAgentServiceGraphManifest) REQUIRE (n.repoId, n.namespace, n.generationId) IS UNIQUE"
    )
    ENSURE_ACTIVE_CONSTRAINT = (
        "CREATE CONSTRAINT ontoagent_service_graph_active_identity IF NOT EXISTS "
        "FOR (n:OntoAgentServiceGraphActive) REQUIRE (n.repoId, n.namespace) IS UNIQUE"
    )
    UPSERT_BUILDING_QUERY = (
        "MERGE (manifest:OntoAgentServiceGraphManifest "
        "{repoId: $repo_id, namespace: $namespace, generationId: $generation_id}) "
        "ON CREATE SET manifest.sourceRevision = $source_revision, manifest.state = $state, manifest.receiptConfirmed = false "
        "WITH manifest WHERE manifest.sourceRevision = $source_revision "
        "RETURN manifest.repoId AS repo_id"
    )
    UPSERT_VERIFIED_QUERY = (
        "MERGE (manifest:OntoAgentServiceGraphManifest "
        "{repoId: $repo_id, namespace: $namespace, generationId: $generation_id}) "
        "ON CREATE SET manifest.sourceRevision = $source_revision "
        "WITH manifest "
        "WHERE manifest.sourceRevision = $source_revision "
        "SET manifest.state = $state, manifest.receiptConfirmed = $receipt_confirmed, "
        "manifest.nodeCount = $node_count, manifest.relationCount = $relation_count, "
        "manifest.receiptFingerprint = $receipt_fingerprint "
        "RETURN manifest.repoId AS repo_id"
    )
    GET_QUERY = (
        "MATCH (manifest:OntoAgentServiceGraphManifest "
        "{repoId: $repo_id, namespace: $namespace, generationId: $generation_id}) "
        "RETURN manifest.repoId AS repo_id, manifest.namespace AS namespace, "
        "manifest.generationId AS generation_id, manifest.sourceRevision AS source_revision, "
        "manifest.state AS state, manifest.receiptConfirmed AS receipt_confirmed, "
        "manifest.nodeCount AS node_count, manifest.relationCount AS relation_count, "
        "manifest.receiptFingerprint AS receipt_fingerprint"
    )
    RESOLVE_QUERY = (
        "MATCH (active:OntoAgentServiceGraphActive {repoId: $repo_id, namespace: $namespace}) "
        "OPTIONAL MATCH (manifest:OntoAgentServiceGraphManifest "
        "{repoId: $repo_id, namespace: $namespace, generationId: active.activeGenerationId}) "
        "RETURN active.activeGenerationId AS active_generation_id, manifest.repoId AS repo_id, "
        "manifest.namespace AS namespace, manifest.generationId AS generation_id, "
        "manifest.sourceRevision AS source_revision, manifest.state AS state, "
        "manifest.receiptConfirmed AS receipt_confirmed, manifest.nodeCount AS node_count, "
        "manifest.relationCount AS relation_count, manifest.receiptFingerprint AS receipt_fingerprint"
    )
    PUBLISH_ACTIVE_QUERY = (
        "MERGE (active:OntoAgentServiceGraphActive {repoId: $repo_id, namespace: $namespace}) "
        "ON CREATE SET active._ontoagentCreatedForCas = true "
        "WITH active, coalesce(active._ontoagentCreatedForCas, false) AS created_for_cas "
        "WITH active, ((created_for_cas AND $expected_active_generation_id IS NULL) "
        "OR (NOT created_for_cas AND active.activeGenerationId = $expected_active_generation_id)) AS expected_matches "
        "OPTIONAL MATCH (candidate:OntoAgentServiceGraphManifest "
        "{repoId: $repo_id, namespace: $namespace, generationId: $candidate_generation_id}) "
        "WITH active, expected_matches, candidate, "
        "(candidate.state = 'ready' AND candidate.receiptConfirmed = true "
        "AND candidate.receiptFingerprint IS NOT NULL) AS candidate_verified "
        "FOREACH (_ IN CASE WHEN expected_matches AND candidate_verified THEN [1] ELSE [] END | "
        "SET active.activeGenerationId = $candidate_generation_id, active._ontoagentCreatedForCas = null) "
        "RETURN active.activeGenerationId AS active_generation_id, expected_matches, candidate_verified"
    )

    def __init__(self, driver: Neo4jDriver) -> None:
        self._driver = driver
        self.ensure_schema()

    def ensure_schema(self) -> None:
        """Create uniqueness constraints required for pointer-level CAS serialization."""
        with self._driver.session() as session:  # type: ignore[union-attr]
            session.run(self.ENSURE_MANIFEST_CONSTRAINT)  # type: ignore[union-attr]
            session.run(self.ENSURE_ACTIVE_CONSTRAINT)  # type: ignore[union-attr]

    def persist_building(self, manifest: ServiceGraphManifest) -> DurableServiceGraphManifest:
        """Persist a candidate that is not eligible for ACTIVE publication yet."""
        params = self._identity_params(manifest) | {"state": ManifestState.BUILDING.value}
        with self._driver.session() as session:  # type: ignore[union-attr]
            rows = list(session.run(self.UPSERT_BUILDING_QUERY, **params))  # type: ignore[union-attr]
        if not rows:
            raise ValueError("manifest identity already has a different source_revision")
        return DurableServiceGraphManifest(manifest, ManifestState.BUILDING, False, None, None, None)

    def persist_verified(
        self, manifest: ServiceGraphManifest, plan: GraphWritePlan, receipt: WriteReceipt
    ) -> ManifestResolution:
        """Persist only a locally receipt/readback-verified READY candidate."""
        resolution = ServiceGraphManifestRegistry().publish(manifest, plan, receipt)
        if resolution.status is ManifestResolutionStatus.BLOCKED:
            return resolution
        params = self._identity_params(manifest) | {
            "state": ManifestState.READY.value,
            "receipt_confirmed": True,
            "node_count": receipt.node_count,
            "relation_count": receipt.relation_count,
            "receipt_fingerprint": self.receipt_fingerprint(receipt.readback),
        }
        with self._driver.session() as session:  # type: ignore[union-attr]
            rows = list(session.run(self.UPSERT_VERIFIED_QUERY, **params))  # type: ignore[union-attr]
        if not rows:
            return _blocked(ManifestBlockReason.MALFORMED_RECORD)
        return resolution

    def get(self, repo_id: str, namespace: Neo4jNamespace, generation_id: str) -> DurableServiceGraphManifest | None:
        """Read one generation by its durable identity."""
        _validate_identity(repo_id, namespace, generation_id)
        with self._driver.session() as session:  # type: ignore[union-attr]
            rows = list(
                session.run(self.GET_QUERY, repo_id=repo_id, namespace=namespace.value, generation_id=generation_id)
            )  # type: ignore[union-attr]
        return None if not rows else self._record_from_row(rows[0])

    def resolve(self, repo_id: str, generation_id: str, namespace: Neo4jNamespace) -> ManifestResolution:
        """Resolve an exact, verified ACTIVE generation or return a typed blocked result."""
        if not _valid_identity(repo_id, namespace, generation_id):
            return _blocked(ManifestBlockReason.MALFORMED_REQUEST)
        with self._driver.session() as session:  # type: ignore[union-attr]
            rows = list(session.run(self.RESOLVE_QUERY, repo_id=repo_id, namespace=namespace.value))  # type: ignore[union-attr]
        if not rows:
            return _blocked(ManifestBlockReason.MISSING_ACTIVE)
        row = _as_mapping(rows[0])
        if row.get("active_generation_id") is None:
            return _blocked(ManifestBlockReason.MISSING_ACTIVE)
        if row.get("active_generation_id") != generation_id:
            return _blocked(ManifestBlockReason.GENERATION_MISMATCH)
        try:
            record = self._record_from_row(row)
        except ValueError:
            return _blocked(ManifestBlockReason.MALFORMED_RECORD)
        if record.state is not ManifestState.READY:
            return _blocked(ManifestBlockReason.NOT_READY)
        if not record.receipt_confirmed:
            return _blocked(ManifestBlockReason.UNCONFIRMED_RECEIPT)
        return ManifestResolution(ManifestResolutionStatus.READY, ActiveServiceGraphBinding(record.manifest), ())

    def publish_active(
        self,
        repo_id: str,
        namespace: Neo4jNamespace,
        expected_active_generation_id: str | None,
        candidate_generation_id: str,
    ) -> ManifestPublicationResult:
        """Atomically publish a verified candidate only when the expected pointer still matches."""
        _validate_identity(repo_id, namespace, candidate_generation_id)
        if expected_active_generation_id is not None and not _is_nonblank(expected_active_generation_id):
            raise ValueError("expected_active_generation_id must be a nonblank string or None")
        params = {
            "repo_id": repo_id,
            "namespace": namespace.value,
            "expected_active_generation_id": expected_active_generation_id,
            "candidate_generation_id": candidate_generation_id,
        }
        with self._driver.session() as session:  # type: ignore[union-attr]
            rows = list(session.run(self.PUBLISH_ACTIVE_QUERY, **params))  # type: ignore[union-attr]
        if not rows:
            raise RuntimeError("Neo4j CAS publication returned no result")
        row = _as_mapping(rows[0])
        active_generation_id = row.get("active_generation_id")
        if not isinstance(active_generation_id, str) and active_generation_id is not None:
            raise RuntimeError("Neo4j CAS publication returned a malformed active generation")
        if row.get("expected_matches") is not True:
            return ManifestPublicationResult(ManifestPublicationStatus.REJECTED_STALE_ACTIVE, active_generation_id)
        if row.get("candidate_verified") is not True:
            return ManifestPublicationResult(
                ManifestPublicationStatus.REJECTED_CANDIDATE_NOT_READY, active_generation_id
            )
        return ManifestPublicationResult(ManifestPublicationStatus.PUBLISHED, active_generation_id)

    @staticmethod
    def receipt_fingerprint(plan: GraphWritePlan) -> str:
        """Return a deterministic fingerprint of the receipt readback graph plan."""
        payload = {
            "nodes": [{"id": node.id, "type": node.node_type, "props": node.props} for node in plan.nodes],
            "relations": [
                {
                    "id": relation.id,
                    "type": relation.relation_type,
                    "source_id": relation.source_id,
                    "target_id": relation.target_id,
                    "props": relation.props,
                }
                for relation in plan.relations
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, default=_json_default, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _identity_params(manifest: ServiceGraphManifest) -> dict[str, object]:
        return {
            "repo_id": manifest.repo_id,
            "namespace": manifest.graph_namespace.value,
            "generation_id": manifest.generation_id,
            "source_revision": manifest.source_revision,
        }

    @staticmethod
    def _record_from_row(row: object) -> DurableServiceGraphManifest:
        values = _as_mapping(row)
        try:
            manifest = ServiceGraphManifest(
                _required_string(values, "repo_id"),
                _required_string(values, "generation_id"),
                _required_string(values, "source_revision"),
                Neo4jNamespace(_required_string(values, "namespace")),
            )
            state = ManifestState(_required_string(values, "state"))
            confirmed = values.get("receipt_confirmed")
            if type(confirmed) is not bool:
                raise ValueError("receipt_confirmed must be a bool")
            return DurableServiceGraphManifest(
                manifest,
                state,
                confirmed,
                _optional_count(values.get("node_count")),
                _optional_count(values.get("relation_count")),
                _optional_string(values.get("receipt_fingerprint")),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("malformed persisted service graph manifest") from exc


def _blocked(reason: ManifestBlockReason) -> ManifestResolution:
    return ManifestResolution(ManifestResolutionStatus.BLOCKED, None, (reason,))


def _as_mapping(row: object) -> Mapping[str, object]:
    if isinstance(row, Mapping):
        return row
    try:
        values = dict(row)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("Neo4j row is not mapping-like") from exc
    return values


def _json_default(value: object) -> object:
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"unsupported fingerprint value: {type(value).__name__}")


def _required_string(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    if not _is_nonblank(value):
        raise ValueError(f"{key} must be a nonblank string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not _is_nonblank(value):
        raise ValueError("optional string must be nonblank")
    return value


def _optional_count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("count must be a nonnegative integer")
    return value


def _validate_identity(repo_id: str, namespace: Neo4jNamespace, generation_id: str) -> None:
    if not _valid_identity(repo_id, namespace, generation_id):
        raise ValueError("repo_id, namespace, and generation_id must be valid")


def _valid_identity(repo_id: object, namespace: object, generation_id: object) -> bool:
    return _is_nonblank(repo_id) and type(namespace) is Neo4jNamespace and _is_nonblank(generation_id)


def _is_nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
