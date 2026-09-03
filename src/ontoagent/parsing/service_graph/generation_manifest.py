from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from .graph_plan import GraphNode, GraphRelation, GraphWritePlan
from .graph_writer import WriteReceipt


class ManifestState(StrEnum):
    BUILDING = "building"
    READY = "ready"


class ManifestResolutionStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class ManifestBlockReason(StrEnum):
    MISSING_ACTIVE = "missing_active"
    MALFORMED_REQUEST = "malformed_request"
    MALFORMED_RECORD = "malformed_record"
    REPO_MISMATCH = "repo_mismatch"
    GENERATION_MISMATCH = "generation_mismatch"
    NAMESPACE_MISMATCH = "namespace_mismatch"
    NOT_READY = "not_ready"
    UNCONFIRMED_RECEIPT = "unconfirmed_receipt"
    RECEIPT_COUNT_MISMATCH = "receipt_count_mismatch"
    RECEIPT_READBACK_MISMATCH = "receipt_readback_mismatch"
    MALFORMED_GRAPH = "malformed_graph"
    NODE_PROVENANCE_MISMATCH = "node_provenance_mismatch"
    RELATION_PROVENANCE_MISMATCH = "relation_provenance_mismatch"


@dataclass(frozen=True)
class Neo4jNamespace:
    """An explicit namespace in the Neo4j-only service graph backend."""

    value: str

    def __post_init__(self) -> None:
        _require_nonblank(self.value, "namespace")


@dataclass(frozen=True)
class ServiceGraphManifest:
    """An immutable candidate publication manifest, always created in BUILDING."""

    repo_id: str
    generation_id: str
    source_revision: str
    graph_namespace: Neo4jNamespace
    status: ManifestState = ManifestState.BUILDING

    def __post_init__(self) -> None:
        _require_nonblank(self.repo_id, "repo_id")
        _require_nonblank(self.generation_id, "generation_id")
        _require_nonblank(self.source_revision, "source_revision")
        if type(self.graph_namespace) is not Neo4jNamespace:
            raise ValueError("graph_namespace must be a Neo4jNamespace")
        if type(self.status) is not ManifestState or self.status is not ManifestState.BUILDING:
            raise ValueError("candidate manifests must begin in BUILDING")


@dataclass(frozen=True)
class ActiveServiceGraphBinding:
    """The receipt-confirmed READY publication of a BUILDING manifest."""

    manifest: ServiceGraphManifest
    status: ManifestState = ManifestState.READY

    def __post_init__(self) -> None:
        if type(self.manifest) is not ServiceGraphManifest:
            raise ValueError("active binding requires a service graph manifest")
        if (
            self.manifest.status is not ManifestState.BUILDING
            or type(self.status) is not ManifestState
            or self.status is not ManifestState.READY
        ):
            raise ValueError("active binding must contain a BUILDING manifest and be READY")


@dataclass(frozen=True)
class ManifestResolution:
    status: ManifestResolutionStatus
    binding: ActiveServiceGraphBinding | None
    reasons: tuple[ManifestBlockReason, ...]

    def __post_init__(self) -> None:
        if type(self.status) is not ManifestResolutionStatus:
            raise ValueError("resolution status must be a ManifestResolutionStatus")
        if self.status is ManifestResolutionStatus.READY:
            if type(self.binding) is not ActiveServiceGraphBinding or type(self.reasons) is not tuple or self.reasons:
                raise ValueError("READY resolution requires one active binding and no reasons")
            return
        if (
            self.binding is not None
            or type(self.reasons) is not tuple
            or not self.reasons
            or any(type(reason) is not ManifestBlockReason for reason in self.reasons)
        ):
            raise ValueError("BLOCKED resolution requires reasons and no binding")


class ServiceGraphManifestRegistry:
    """In-process, Neo4j receipt/readback-gated ACTIVE binding registry."""

    def __init__(self) -> None:
        self._active_by_repo: dict[str, ActiveServiceGraphBinding] = {}
        self._lock = Lock()

    def publish(
        self, manifest: ServiceGraphManifest, plan: GraphWritePlan, receipt: WriteReceipt
    ) -> ManifestResolution:
        """Validate a candidate then atomically replace the repo's active binding."""
        reason = self._publication_failure(manifest, plan, receipt)
        if reason is not None:
            return _blocked(reason)
        binding = ActiveServiceGraphBinding(manifest)
        with self._lock:
            self._active_by_repo[manifest.repo_id] = binding
        return _ready(binding)

    def resolve(self, repo_id: str, generation_id: str, graph_namespace: Neo4jNamespace) -> ManifestResolution:
        """Resolve only an exact ACTIVE READY binding; all misses are BLOCKED."""
        if not _is_nonblank(repo_id) or not _is_nonblank(generation_id) or type(graph_namespace) is not Neo4jNamespace:
            return _blocked(ManifestBlockReason.MALFORMED_REQUEST)
        with self._lock:
            binding = self._active_by_repo.get(repo_id)
            has_active_bindings = bool(self._active_by_repo)
        if binding is None:
            return _blocked(
                ManifestBlockReason.REPO_MISMATCH if has_active_bindings else ManifestBlockReason.MISSING_ACTIVE
            )
        if not _is_well_formed_binding(binding):
            return _blocked(ManifestBlockReason.MALFORMED_RECORD)
        if binding.status is not ManifestState.READY:
            return _blocked(ManifestBlockReason.NOT_READY)
        active = binding.manifest
        if active.repo_id != repo_id:
            return _blocked(ManifestBlockReason.REPO_MISMATCH)
        if active.generation_id != generation_id:
            return _blocked(ManifestBlockReason.GENERATION_MISMATCH)
        if active.graph_namespace != graph_namespace:
            return _blocked(ManifestBlockReason.NAMESPACE_MISMATCH)
        return _ready(binding)

    @staticmethod
    def _publication_failure(
        manifest: ServiceGraphManifest, plan: GraphWritePlan, receipt: WriteReceipt
    ) -> ManifestBlockReason | None:
        if (
            type(manifest) is not ServiceGraphManifest
            or type(plan) is not GraphWritePlan
            or type(receipt) is not WriteReceipt
        ):
            return ManifestBlockReason.MALFORMED_RECORD
        if (
            type(receipt.confirmed) is not bool
            or type(receipt.node_count) is not int
            or type(receipt.relation_count) is not int
            or type(receipt.readback) is not GraphWritePlan
            or not _is_nonblank(receipt.graph_namespace)
            or type(plan.nodes) is not tuple
            or type(plan.relations) is not tuple
            or any(type(node) is not GraphNode or not isinstance(node.props, Mapping) for node in plan.nodes)
            or any(
                type(relation) is not GraphRelation or not isinstance(relation.props, Mapping)
                for relation in plan.relations
            )
        ):
            return ManifestBlockReason.MALFORMED_RECORD
        if not receipt.confirmed:
            return ManifestBlockReason.UNCONFIRMED_RECEIPT
        if receipt.graph_namespace != manifest.graph_namespace.value:
            return ManifestBlockReason.NAMESPACE_MISMATCH
        if receipt.node_count != len(plan.nodes) or receipt.relation_count != len(plan.relations):
            return ManifestBlockReason.RECEIPT_COUNT_MISMATCH
        if (
            receipt.readback != plan
            or receipt.node_count != len(receipt.readback.nodes)
            or receipt.relation_count != len(receipt.readback.relations)
        ):
            return ManifestBlockReason.RECEIPT_READBACK_MISMATCH
        return _validate_graph_for_manifest(receipt.readback, manifest)


def _ready(binding: ActiveServiceGraphBinding) -> ManifestResolution:
    return ManifestResolution(ManifestResolutionStatus.READY, binding, ())


def _blocked(reason: ManifestBlockReason) -> ManifestResolution:
    return ManifestResolution(ManifestResolutionStatus.BLOCKED, None, (reason,))


_NODE_TYPES = frozenset({"ServiceDefinition", "Endpoint", "Evidence"})
_RELATION_TYPES = frozenset({"PROVIDES_ENDPOINT", "CONSUMES_ENDPOINT", "DEPENDS_ON", "SUPPORTED_BY_EVIDENCE"})


def _validate_graph_for_manifest(plan: GraphWritePlan, manifest: ServiceGraphManifest) -> ManifestBlockReason | None:
    if any(
        type(node) is not GraphNode
        or not _is_nonblank(node.id)
        or node.node_type not in _NODE_TYPES
        or not isinstance(node.props, Mapping)
        for node in plan.nodes
    ) or any(
        type(relation) is not GraphRelation
        or not _is_nonblank(relation.id)
        or relation.relation_type not in _RELATION_TYPES
        or not _is_nonblank(relation.source_id)
        or not _is_nonblank(relation.target_id)
        or not isinstance(relation.props, Mapping)
        for relation in plan.relations
    ):
        return ManifestBlockReason.MALFORMED_GRAPH
    node_ids = {node.id for node in plan.nodes}
    if len(node_ids) != len(plan.nodes) or len({relation.id for relation in plan.relations}) != len(plan.relations):
        return ManifestBlockReason.MALFORMED_GRAPH
    if any(relation.source_id not in node_ids or relation.target_id not in node_ids for relation in plan.relations):
        return ManifestBlockReason.MALFORMED_GRAPH
    nodes = {node.id: node for node in plan.nodes}
    if any(
        node.props.get("id") != node.id
        or not _has_provenance(node.props, ("repo_id", "generation_id", "source_revision", "canonical_key"))
        for node in plan.nodes
    ):
        return ManifestBlockReason.NODE_PROVENANCE_MISMATCH
    if any(
        node.props.get("repo_id") == manifest.repo_id and not _node_matches_manifest(node, manifest)
        for node in plan.nodes
    ) or not any(_node_matches_manifest(node, manifest) for node in plan.nodes):
        return ManifestBlockReason.NODE_PROVENANCE_MISMATCH
    if any(
        not _has_provenance(relation.props, ("canonical_key", "generation_id", "source_revision"))
        for relation in plan.relations
    ):
        return ManifestBlockReason.RELATION_PROVENANCE_MISMATCH
    for relation in plan.relations:
        source = nodes[relation.source_id]
        if (relation.props.get("generation_id"), relation.props.get("source_revision")) != (
            source.props.get("generation_id"),
            source.props.get("source_revision"),
        ):
            return ManifestBlockReason.RELATION_PROVENANCE_MISMATCH
        if relation.relation_type == "DEPENDS_ON" and not _dependency_sides_match_nodes(
            relation, source, nodes[relation.target_id]
        ):
            return ManifestBlockReason.RELATION_PROVENANCE_MISMATCH
    return None


def _has_provenance(props: Mapping[str, object], keys: tuple[str, ...]) -> bool:
    return all(_is_nonblank(props.get(key)) for key in keys) and _has_evidence_ids(props.get("evidence_ids"))


def _node_matches_manifest(node: GraphNode, manifest: ServiceGraphManifest) -> bool:
    return (node.props.get("repo_id"), node.props.get("generation_id"), node.props.get("source_revision")) == (
        manifest.repo_id,
        manifest.generation_id,
        manifest.source_revision,
    )


def _dependency_sides_match_nodes(relation: GraphRelation, source: GraphNode, target: GraphNode) -> bool:
    side_keys = (
        "provider_repo_id",
        "provider_generation_id",
        "provider_source_revision",
        "consumer_repo_id",
        "consumer_generation_id",
        "consumer_source_revision",
    )
    present = [key in relation.props for key in side_keys]
    if not any(present):
        return True
    if not all(present) or not all(_is_nonblank(relation.props[key]) for key in side_keys):
        return False
    return (
        relation.props["consumer_repo_id"],
        relation.props["consumer_generation_id"],
        relation.props["consumer_source_revision"],
    ) == (source.props.get("repo_id"), source.props.get("generation_id"), source.props.get("source_revision")) and (
        relation.props["provider_repo_id"],
        relation.props["provider_generation_id"],
        relation.props["provider_source_revision"],
    ) == (target.props.get("repo_id"), target.props.get("generation_id"), target.props.get("source_revision"))


def _has_evidence_ids(value: object) -> bool:
    return isinstance(value, tuple) and bool(value) and all(_is_nonblank(item) for item in value)


def _is_well_formed_binding(binding: ActiveServiceGraphBinding) -> bool:
    manifest = binding.manifest
    return (
        type(manifest) is ServiceGraphManifest
        and binding.status is ManifestState.READY
        and manifest.status is ManifestState.BUILDING
        and _is_nonblank(manifest.repo_id)
        and _is_nonblank(manifest.generation_id)
        and _is_nonblank(manifest.source_revision)
        and type(manifest.graph_namespace) is Neo4jNamespace
        and _is_nonblank(manifest.graph_namespace.value)
    )


def _require_nonblank(value: object, field_name: str) -> None:
    if not _is_nonblank(value):
        raise ValueError(f"{field_name} must be a nonblank string")


def _is_nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
