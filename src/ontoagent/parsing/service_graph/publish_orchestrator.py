from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .detectors.registry import DetectorRegistry
from .generation_manifest import (
    ManifestResolution,
    ManifestResolutionStatus,
    Neo4jNamespace,
    ServiceGraphManifest,
)
from .graph_plan import GraphPlanBuilder, GraphWritePlan
from .graph_writer import GraphWriter, WriteReceipt
from .models import DetectorFacts, RepositorySnapshot
from .neo4j_graph_sink import Neo4jDriver, Neo4jGraphSink
from .neo4j_manifest_repository import (
    ManifestPublicationResult,
    ManifestPublicationStatus,
    Neo4jServiceGraphManifestRepository,
)
from .resolver import FactBatch, ResolveResult, ServiceGraphResolver


class ServiceGraphPublishStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    FAILED = "failed"


class ServiceGraphPublishReason(StrEnum):
    INVALID_INPUT = "invalid_input"
    DETECTOR_FACT_IDENTITY_MISMATCH = "detector_fact_identity_mismatch"
    DETECTOR_FAILED = "detector_failed"
    RESOLUTION_FAILED = "resolution_failed"
    PLAN_BUILD_FAILED = "plan_build_failed"
    BUILDING_MANIFEST_FAILED = "building_manifest_failed"
    GRAPH_WRITE_FAILED = "graph_write_failed"
    GRAPH_WRITE_UNCONFIRMED = "graph_write_unconfirmed"
    VERIFIED_MANIFEST_BLOCKED = "verified_manifest_blocked"
    VERIFIED_MANIFEST_FAILED = "verified_manifest_failed"
    ACTIVE_COMPARE_AND_SET_REJECTED = "active_compare_and_set_rejected"
    ACTIVE_COMPARE_AND_SET_FAILED = "active_compare_and_set_failed"


@dataclass(frozen=True)
class ServiceGraphPublishInput:
    """One immutable repository snapshot and its explicit publication generation."""

    snapshot: RepositorySnapshot
    generation_id: str
    branch: str
    expected_active_generation_id: str | None

    def __post_init__(self) -> None:
        if type(self.snapshot) is not RepositorySnapshot:
            raise ValueError("snapshot must be a RepositorySnapshot")
        for name in ("generation_id", "branch"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")
        if self.expected_active_generation_id is not None and (
            not isinstance(self.expected_active_generation_id, str) or not self.expected_active_generation_id.strip()
        ):
            raise ValueError("expected_active_generation_id must be nonblank when provided")

    @property
    def repo_id(self) -> str:
        return self.snapshot.repo_id


@dataclass(frozen=True)
class ServiceGraphPublicationReceipt:
    """JSON-safe per-repository truth, including a possible partial ACTIVE result."""

    repo_id: str
    generation_id: str
    expected_active_generation_id: str | None
    building_persisted: bool
    manifest_verified: bool
    active_published: bool
    publication_status: ManifestPublicationStatus | None = None
    active_generation_id: str | None = None


@dataclass(frozen=True)
class ServiceGraphPublishOutcome:
    """Fail-closed result. Confirmed graph data does not imply every repo is ACTIVE."""

    status: ServiceGraphPublishStatus
    graph_write_confirmed: bool
    reason: ServiceGraphPublishReason | None
    publication_receipts: tuple[ServiceGraphPublicationReceipt, ...]

    def __post_init__(self) -> None:
        if type(self.status) is not ServiceGraphPublishStatus:
            raise ValueError("status must be a ServiceGraphPublishStatus")
        if type(self.graph_write_confirmed) is not bool:
            raise ValueError("graph_write_confirmed must be a bool")
        if self.reason is not None and type(self.reason) is not ServiceGraphPublishReason:
            raise ValueError("reason must be a ServiceGraphPublishReason")
        if type(self.publication_receipts) is not tuple or any(
            type(receipt) is not ServiceGraphPublicationReceipt for receipt in self.publication_receipts
        ):
            raise ValueError("publication_receipts must be a tuple of ServiceGraphPublicationReceipt")
        if self.status is ServiceGraphPublishStatus.ACTIVE:
            if (
                self.reason is not None
                or not self.graph_write_confirmed
                or not all(receipt.active_published for receipt in self.publication_receipts)
            ):
                raise ValueError("ACTIVE requires confirmed graph data and every repo published")
        elif self.reason is None:
            raise ValueError("non-ACTIVE outcomes require a reason")

    def to_dict(self) -> dict[str, object]:
        """Return a recursively JSON-safe representation without exposing plan contents."""
        return {
            "status": self.status.value,
            "graph_write_confirmed": self.graph_write_confirmed,
            "reason": self.reason.value if self.reason is not None else None,
            "publication_receipts": [
                {
                    "repo_id": receipt.repo_id,
                    "generation_id": receipt.generation_id,
                    "expected_active_generation_id": receipt.expected_active_generation_id,
                    "building_persisted": receipt.building_persisted,
                    "manifest_verified": receipt.manifest_verified,
                    "active_published": receipt.active_published,
                    "publication_status": receipt.publication_status.value
                    if receipt.publication_status is not None
                    else None,
                    "active_generation_id": receipt.active_generation_id,
                }
                for receipt in self.publication_receipts
            ],
        }


class DetectorRegistryPort(Protocol):
    @property
    def ids(self) -> tuple[str, ...]: ...

    def detect(self, snapshot: RepositorySnapshot, detector_id: str | None = None) -> DetectorFacts: ...


class ResolverPort(Protocol):
    def resolve(self, batches: tuple[FactBatch, ...]) -> ResolveResult: ...


class GraphPlanBuilderPort(Protocol):
    def build(self, result: ResolveResult) -> GraphWritePlan: ...


class GraphWriterPort(Protocol):
    def write(self, plan: GraphWritePlan) -> WriteReceipt: ...


class ManifestRepositoryPort(Protocol):
    def persist_building(self, manifest: ServiceGraphManifest) -> object: ...

    def persist_verified(
        self, manifest: ServiceGraphManifest, plan: GraphWritePlan, receipt: WriteReceipt
    ) -> ManifestResolution: ...

    def publish_active(
        self,
        repo_id: str,
        namespace: Neo4jNamespace,
        expected_active_generation_id: str | None,
        candidate_generation_id: str,
    ) -> ManifestPublicationResult: ...


@dataclass(frozen=True)
class ServiceGraphPublishComponents:
    detector_registry: DetectorRegistryPort
    resolver: ResolverPort
    plan_builder: GraphPlanBuilderPort
    graph_writer: GraphWriterPort
    manifest_repository: ManifestRepositoryPort


class ServiceGraphPublishComponentFactory(Protocol):
    def create(self, namespace: Neo4jNamespace) -> ServiceGraphPublishComponents: ...


class Neo4jServiceGraphPublishComponentFactory:
    """Production factory; tests can inject a small in-memory factory instead."""

    def __init__(self, driver: Neo4jDriver, detector_registry: DetectorRegistry) -> None:
        self._driver = driver
        self._detector_registry = detector_registry

    def create(self, namespace: Neo4jNamespace) -> ServiceGraphPublishComponents:
        return ServiceGraphPublishComponents(
            self._detector_registry,
            ServiceGraphResolver(),
            GraphPlanBuilder(),
            GraphWriter(Neo4jGraphSink(self._driver, namespace=namespace.value)),
            Neo4jServiceGraphManifestRepository(self._driver),
        )


class ServiceGraphPublishOrchestrator:
    """Build and publish a multi-repository service graph without compensating graph writes.

    A later manifest or CAS failure leaves already-written graph records in place. The outcome
    deliberately reports graph confirmation separately from all-repository ACTIVE publication.
    """

    def __init__(self, component_factory: ServiceGraphPublishComponentFactory) -> None:
        self._component_factory = component_factory

    def publish(
        self, namespace: Neo4jNamespace, inputs: tuple[ServiceGraphPublishInput, ...]
    ) -> ServiceGraphPublishOutcome:
        initial_receipts = _receipts(inputs)
        if not _valid_request(namespace, inputs):
            return _outcome(
                ServiceGraphPublishStatus.BLOCKED, False, ServiceGraphPublishReason.INVALID_INPUT, initial_receipts
            )
        components = self._component_factory.create(namespace)
        batches = self._detect(components.detector_registry, inputs)
        if batches is None:
            return _outcome(
                ServiceGraphPublishStatus.BLOCKED,
                False,
                ServiceGraphPublishReason.DETECTOR_FACT_IDENTITY_MISMATCH,
                initial_receipts,
            )
        try:
            resolved = components.resolver.resolve(batches)
        except Exception:
            return _outcome(
                ServiceGraphPublishStatus.FAILED, False, ServiceGraphPublishReason.RESOLUTION_FAILED, initial_receipts
            )
        try:
            plan = components.plan_builder.build(resolved)
        except Exception:
            return _outcome(
                ServiceGraphPublishStatus.FAILED, False, ServiceGraphPublishReason.PLAN_BUILD_FAILED, initial_receipts
            )
        manifests = tuple(
            ServiceGraphManifest(item.repo_id, item.generation_id, item.snapshot.source_revision, namespace)
            for item in inputs
        )
        receipts = initial_receipts
        for index, manifest in enumerate(manifests):
            try:
                components.manifest_repository.persist_building(manifest)
            except Exception:
                return _outcome(
                    ServiceGraphPublishStatus.FAILED,
                    False,
                    ServiceGraphPublishReason.BUILDING_MANIFEST_FAILED,
                    receipts,
                )
            receipts = _replace_receipt(receipts, index, building_persisted=True)
        try:
            graph_receipt = components.graph_writer.write(plan)
        except Exception:
            return _outcome(
                ServiceGraphPublishStatus.FAILED, False, ServiceGraphPublishReason.GRAPH_WRITE_FAILED, receipts
            )
        if not _confirmed_receipt(graph_receipt, plan, namespace):
            return _outcome(
                ServiceGraphPublishStatus.BLOCKED,
                False,
                ServiceGraphPublishReason.GRAPH_WRITE_UNCONFIRMED,
                receipts,
            )
        for index, manifest in enumerate(manifests):
            try:
                resolution = components.manifest_repository.persist_verified(manifest, plan, graph_receipt)
            except Exception:
                return _outcome(
                    ServiceGraphPublishStatus.FAILED,
                    True,
                    ServiceGraphPublishReason.VERIFIED_MANIFEST_FAILED,
                    receipts,
                )
            if resolution.status is not ManifestResolutionStatus.READY:
                return _outcome(
                    ServiceGraphPublishStatus.BLOCKED,
                    True,
                    ServiceGraphPublishReason.VERIFIED_MANIFEST_BLOCKED,
                    receipts,
                )
            receipts = _replace_receipt(receipts, index, manifest_verified=True)
        for index, item in enumerate(inputs):
            try:
                publication = components.manifest_repository.publish_active(
                    item.repo_id, namespace, item.expected_active_generation_id, item.generation_id
                )
            except Exception:
                return _outcome(
                    ServiceGraphPublishStatus.FAILED,
                    True,
                    ServiceGraphPublishReason.ACTIVE_COMPARE_AND_SET_FAILED,
                    receipts,
                )
            receipts = _replace_receipt(
                receipts,
                index,
                active_published=publication.status is ManifestPublicationStatus.PUBLISHED,
                publication_status=publication.status,
                active_generation_id=publication.active_generation_id,
            )
            if publication.status is not ManifestPublicationStatus.PUBLISHED:
                return _outcome(
                    ServiceGraphPublishStatus.BLOCKED,
                    True,
                    ServiceGraphPublishReason.ACTIVE_COMPARE_AND_SET_REJECTED,
                    receipts,
                )
        return _outcome(ServiceGraphPublishStatus.ACTIVE, True, None, receipts)

    @staticmethod
    def _detect(
        registry: DetectorRegistryPort, inputs: tuple[ServiceGraphPublishInput, ...]
    ) -> tuple[FactBatch, ...] | None:
        batches: list[FactBatch] = []
        for item in inputs:
            facts: list[DetectorFacts] = []
            for detector_id in registry.ids:
                try:
                    detected = registry.detect(item.snapshot, detector_id=detector_id)
                except LookupError:
                    continue
                except Exception:
                    return None
                if (
                    type(detected) is not DetectorFacts
                    or detected.repo_id != item.repo_id
                    or detected.source_revision != item.snapshot.source_revision
                ):
                    return None
                facts.append(detected)
            if not facts:
                return None
            batches.append(
                FactBatch(item.repo_id, item.snapshot.source_revision, item.generation_id, item.branch, tuple(facts))
            )
        return tuple(batches)


def _valid_request(namespace: object, inputs: object) -> bool:
    return (
        type(namespace) is Neo4jNamespace
        and type(inputs) is tuple
        and bool(inputs)
        and all(type(item) is ServiceGraphPublishInput for item in inputs)
        and len({item.repo_id for item in inputs}) == len(inputs)
    )


def _confirmed_receipt(receipt: object, plan: GraphWritePlan, namespace: Neo4jNamespace) -> bool:
    return (
        type(receipt) is WriteReceipt
        and receipt.confirmed
        and receipt.graph_namespace == namespace.value
        and receipt.readback == plan
        and receipt.node_count == len(plan.nodes)
        and receipt.relation_count == len(plan.relations)
    )


def _receipts(inputs: object) -> tuple[ServiceGraphPublicationReceipt, ...]:
    if type(inputs) is not tuple:
        return ()
    return tuple(
        ServiceGraphPublicationReceipt(
            item.repo_id,
            item.generation_id,
            item.expected_active_generation_id,
            False,
            False,
            False,
        )
        for item in inputs
        if type(item) is ServiceGraphPublishInput
    )


def _replace_receipt(
    receipts: tuple[ServiceGraphPublicationReceipt, ...], index: int, **changes: object
) -> tuple[ServiceGraphPublicationReceipt, ...]:
    receipt = receipts[index]
    replacement = ServiceGraphPublicationReceipt(
        receipt.repo_id,
        receipt.generation_id,
        receipt.expected_active_generation_id,
        bool(changes.get("building_persisted", receipt.building_persisted)),
        bool(changes.get("manifest_verified", receipt.manifest_verified)),
        bool(changes.get("active_published", receipt.active_published)),
        changes.get("publication_status", receipt.publication_status),  # type: ignore[arg-type]
        changes.get("active_generation_id", receipt.active_generation_id),  # type: ignore[arg-type]
    )
    return (*receipts[:index], replacement, *receipts[index + 1 :])


def _outcome(
    status: ServiceGraphPublishStatus,
    graph_write_confirmed: bool,
    reason: ServiceGraphPublishReason | None,
    receipts: tuple[ServiceGraphPublicationReceipt, ...],
) -> ServiceGraphPublishOutcome:
    return ServiceGraphPublishOutcome(status, graph_write_confirmed, reason, receipts)
