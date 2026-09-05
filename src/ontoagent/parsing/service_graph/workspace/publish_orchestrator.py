from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..detectors.registry import DetectorRegistry
from ..graph_plan import GraphNode, GraphPlanBuilder, GraphRelation, GraphWritePlan
from ..graph_writer import GraphWriter, WriteReceipt
from ..models import DetectorFacts, RepositorySnapshot
from ..neo4j_graph_sink import Neo4jDriver, Neo4jGraphSink
from ..resolver import FactBatch, ResolveResult, ServiceGraphResolver
from .models import (
    BuildTask,
    Workspace,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspaceRepositorySnapshot,
)
from .models import (
    WorkspacePublishStatus as WorkspacePublicationStatus,
)
from .neo4j_repository import Neo4jWorkspaceRepository


class WorkspacePublishStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    FAILED = "failed"


class WorkspacePublishReason(StrEnum):
    INVALID_INPUT = "invalid_input"
    PERSISTENCE_FAILED = "persistence_failed"
    DETECTOR_FAILED = "detector_failed"
    DETECTOR_FACT_IDENTITY_MISMATCH = "detector_fact_identity_mismatch"
    RESOLUTION_FAILED = "resolution_failed"
    PLAN_BUILD_FAILED = "plan_build_failed"
    PLAN_MISSING_REPOSITORY = "plan_missing_repository"
    GRAPH_WRITE_FAILED = "graph_write_failed"
    GRAPH_WRITE_UNCONFIRMED = "graph_write_unconfirmed"
    ACTIVE_COMPARE_AND_SET_REJECTED = "active_compare_and_set_rejected"
    ACTIVE_COMPARE_AND_SET_FAILED = "active_compare_and_set_failed"


@dataclass(frozen=True)
class WorkspaceServiceGraphPublishInput:
    """Frozen workspace identities paired with immutable local detector snapshots."""

    workspace: Workspace
    snapshots: tuple[WorkspaceRepositorySnapshot, ...]
    repository_snapshots: tuple[RepositorySnapshot, ...]
    task_idempotency_key: str
    generation_id: str
    expected_active_generation_id: str | None

    def __post_init__(self) -> None:
        if type(self.workspace) is not Workspace:
            raise ValueError("workspace must be a Workspace")
        if type(self.snapshots) is not tuple or len(self.snapshots) < 3:
            raise ValueError("snapshots must be an immutable tuple of at least three repositories")
        if type(self.repository_snapshots) is not tuple:
            raise ValueError("repository_snapshots must be an immutable tuple")
        if any(type(snapshot) is not WorkspaceRepositorySnapshot for snapshot in self.snapshots):
            raise ValueError("snapshots must contain WorkspaceRepositorySnapshot values")
        if any(type(snapshot) is not RepositorySnapshot for snapshot in self.repository_snapshots):
            raise ValueError("repository_snapshots must contain RepositorySnapshot values")
        for field_name in ("task_idempotency_key", "generation_id"):
            _require_nonblank(getattr(self, field_name), field_name)
        if self.expected_active_generation_id is not None:
            _require_nonblank(self.expected_active_generation_id, "expected_active_generation_id")
        frozen = {snapshot.repo_id: snapshot for snapshot in self.snapshots}
        runtime = {snapshot.repo_id: snapshot for snapshot in self.repository_snapshots}
        if len(frozen) != len(self.snapshots) or set(frozen) != set(runtime):
            raise ValueError("frozen and runtime snapshots must have the same unique repo IDs")
        if any(snapshot.workspace_id != self.workspace.workspace_id for snapshot in self.snapshots):
            raise ValueError("snapshot workspace_id mismatch")
        if any(runtime[repo_id].source_revision != snapshot.source_revision for repo_id, snapshot in frozen.items()):
            raise ValueError("frozen and runtime source revisions must match")
        object.__setattr__(self, "snapshots", tuple(sorted(self.snapshots, key=lambda item: item.repo_id)))
        object.__setattr__(
            self, "repository_snapshots", tuple(sorted(self.repository_snapshots, key=lambda item: item.repo_id))
        )


@dataclass(frozen=True)
class WorkspacePublishOutcome:
    status: WorkspacePublishStatus
    reason: WorkspacePublishReason | None
    workspace_id: str
    generation_id: str
    candidate_namespace: str
    generation_state: WorkspaceGenerationState
    graph_write_confirmed: bool
    active_generation_id: str | None

    def __post_init__(self) -> None:
        if type(self.status) is not WorkspacePublishStatus:
            raise ValueError("status must be a WorkspacePublishStatus")
        if self.reason is not None and type(self.reason) is not WorkspacePublishReason:
            raise ValueError("reason must be a WorkspacePublishReason")
        for field_name in ("workspace_id", "generation_id", "candidate_namespace"):
            _require_nonblank(getattr(self, field_name), field_name)
        if type(self.generation_state) is not WorkspaceGenerationState:
            raise ValueError("generation_state must be a WorkspaceGenerationState")
        if type(self.graph_write_confirmed) is not bool:
            raise ValueError("graph_write_confirmed must be a bool")
        if self.active_generation_id is not None:
            _require_nonblank(self.active_generation_id, "active_generation_id")
        if self.status is WorkspacePublishStatus.ACTIVE:
            if (
                self.reason is not None
                or not self.graph_write_confirmed
                or self.generation_state is not WorkspaceGenerationState.ACTIVE
            ):
                raise ValueError("ACTIVE requires a confirmed active generation")
        elif self.reason is None:
            raise ValueError("non-ACTIVE outcomes require a reason")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reason": self.reason.value if self.reason is not None else None,
            "workspace_id": self.workspace_id,
            "generation_id": self.generation_id,
            "candidate_namespace": self.candidate_namespace,
            "generation_state": self.generation_state.value,
            "graph_write_confirmed": self.graph_write_confirmed,
            "active_generation_id": self.active_generation_id,
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


class WorkspaceRepositoryPort(Protocol):
    def create_workspace(self, workspace: Workspace) -> Workspace: ...

    def create_build_task(self, task: BuildTask) -> BuildTask: ...

    def get_build_task(self, task_id: str) -> BuildTask | None: ...

    def create_generation(self, generation: WorkspaceGeneration) -> WorkspaceGeneration: ...

    def advance_generation_state(
        self, generation: WorkspaceGeneration, target: WorkspaceGenerationState
    ) -> WorkspaceGeneration: ...

    def publish_generation(
        self, workspace_id: str, expected_active_generation_id: str | None, candidate_generation_id: str
    ) -> object: ...


@dataclass(frozen=True)
class WorkspaceServiceGraphPublishComponents:
    detector_registry: DetectorRegistryPort
    resolver: ResolverPort
    plan_builder: GraphPlanBuilderPort
    graph_writer: GraphWriterPort
    workspace_repository: WorkspaceRepositoryPort


class WorkspaceServiceGraphPublishComponentFactory(Protocol):
    def create(self, namespace: str) -> WorkspaceServiceGraphPublishComponents: ...


class Neo4jWorkspaceServiceGraphPublishComponentFactory:
    """Production dependencies for the workspace-only publication vertical slice."""

    def __init__(self, driver: Neo4jDriver, detector_registry: DetectorRegistry) -> None:
        self._driver = driver
        self._detector_registry = detector_registry

    def create(self, namespace: str) -> WorkspaceServiceGraphPublishComponents:
        return WorkspaceServiceGraphPublishComponents(
            self._detector_registry,
            ServiceGraphResolver(),
            GraphPlanBuilder(),
            GraphWriter(Neo4jGraphSink(self._driver, namespace=namespace)),
            Neo4jWorkspaceRepository(self._driver),
        )


class WorkspaceServiceGraphPublishOrchestrator:
    """Publish one frozen workspace graph without touching legacy repository manifests."""

    def __init__(self, component_factory: WorkspaceServiceGraphPublishComponentFactory) -> None:
        self._component_factory = component_factory

    @staticmethod
    def namespace_for(workspace_id: str, generation_id: str) -> str:
        _require_nonblank(workspace_id, "workspace_id")
        _require_nonblank(generation_id, "generation_id")
        digest = hashlib.sha256(f"{workspace_id}\x00{generation_id}".encode()).hexdigest()
        return f"workspace-generation-{digest}"

    def publish(self, request: WorkspaceServiceGraphPublishInput) -> WorkspacePublishOutcome:
        if type(request) is not WorkspaceServiceGraphPublishInput:
            raise ValueError("request must be a WorkspaceServiceGraphPublishInput")
        namespace = self.namespace_for(request.workspace.workspace_id, request.generation_id)
        components = self._component_factory.create(namespace)
        generation = WorkspaceGeneration(request.workspace.workspace_id, request.generation_id, request.snapshots)
        try:
            components.workspace_repository.create_workspace(request.workspace)
            components.workspace_repository.create_build_task(
                BuildTask(
                    _task_id(request.workspace.workspace_id, request.task_idempotency_key),
                    request.workspace.workspace_id,
                    request.task_idempotency_key,
                    request.generation_id,
                )
            )
            generation = components.workspace_repository.create_generation(generation)
            generation = components.workspace_repository.advance_generation_state(
                generation, WorkspaceGenerationState.EXTRACTING
            )
        except Exception:
            return self._fail(
                components.workspace_repository,
                request,
                namespace,
                generation,
                WorkspacePublishReason.PERSISTENCE_FAILED,
            )

        batches = self._detect(
            components.detector_registry, request.repository_snapshots, generation.generation_id, request.snapshots
        )
        if batches is None:
            return self._fail(
                components.workspace_repository, request, namespace, generation, WorkspacePublishReason.DETECTOR_FAILED
            )
        try:
            generation = components.workspace_repository.advance_generation_state(
                generation, WorkspaceGenerationState.RESOLVING
            )
            resolved = components.resolver.resolve(batches)
            plan = _namespace_plan(components.plan_builder.build(resolved), namespace)
        except Exception:
            return self._fail(
                components.workspace_repository,
                request,
                namespace,
                generation,
                WorkspacePublishReason.RESOLUTION_FAILED,
            )
        if not _contains_all_repositories(plan, request.snapshots) or not _has_one_namespace(plan, namespace):
            return self._fail(
                components.workspace_repository,
                request,
                namespace,
                generation,
                WorkspacePublishReason.PLAN_MISSING_REPOSITORY,
            )
        try:
            generation = components.workspace_repository.advance_generation_state(
                generation, WorkspaceGenerationState.WRITING
            )
            receipt = components.graph_writer.write(plan)
        except Exception:
            return self._fail(
                components.workspace_repository,
                request,
                namespace,
                generation,
                WorkspacePublishReason.GRAPH_WRITE_FAILED,
            )
        if not _confirmed_receipt(receipt, plan, namespace):
            return self._fail(
                components.workspace_repository,
                request,
                namespace,
                generation,
                WorkspacePublishReason.GRAPH_WRITE_UNCONFIRMED,
            )
        try:
            generation = components.workspace_repository.advance_generation_state(
                generation, WorkspaceGenerationState.VERIFYING
            )
            publication = components.workspace_repository.publish_generation(
                request.workspace.workspace_id, request.expected_active_generation_id, request.generation_id
            )
        except Exception:
            return self._fail(
                components.workspace_repository,
                request,
                namespace,
                generation,
                WorkspacePublishReason.ACTIVE_COMPARE_AND_SET_FAILED,
            )
        if getattr(publication, "status", None) is not WorkspacePublicationStatus.PUBLISHED:
            return self._block(components.workspace_repository, request, namespace, generation, publication)
        return self._outcome(
            request,
            namespace,
            WorkspacePublishStatus.ACTIVE,
            None,
            WorkspaceGeneration(
                generation.workspace_id, generation.generation_id, generation.snapshots, WorkspaceGenerationState.ACTIVE
            ),
            True,
            getattr(publication, "active_generation_id", None),
        )

    @staticmethod
    def _detect(
        registry: DetectorRegistryPort,
        runtime_snapshots: tuple[RepositorySnapshot, ...],
        generation_id: str,
        frozen_snapshots: tuple[WorkspaceRepositorySnapshot, ...],
    ) -> tuple[FactBatch, ...] | None:
        frozen_by_repo = {snapshot.repo_id: snapshot for snapshot in frozen_snapshots}
        batches: list[FactBatch] = []
        for snapshot in runtime_snapshots:
            facts: list[DetectorFacts] = []
            for detector_id in registry.ids:
                try:
                    detected = registry.detect(snapshot, detector_id=detector_id)
                except LookupError:
                    continue
                except Exception:
                    return None
                if type(detected) is not DetectorFacts or (
                    detected.repo_id != snapshot.repo_id or detected.source_revision != snapshot.source_revision
                ):
                    return None
                facts.append(detected)
            if not facts:
                return None
            frozen = frozen_by_repo[snapshot.repo_id]
            batches.append(
                FactBatch(snapshot.repo_id, snapshot.source_revision, generation_id, frozen.branch, tuple(facts))
            )
        return tuple(batches)

    def _fail(
        self,
        repository: WorkspaceRepositoryPort,
        request: WorkspaceServiceGraphPublishInput,
        namespace: str,
        generation: WorkspaceGeneration,
        reason: WorkspacePublishReason,
    ) -> WorkspacePublishOutcome:
        try:
            failed = repository.advance_generation_state(generation, WorkspaceGenerationState.FAILED)
        except Exception:
            failed = WorkspaceGeneration(
                generation.workspace_id, generation.generation_id, generation.snapshots, WorkspaceGenerationState.FAILED
            )
        return self._outcome(request, namespace, WorkspacePublishStatus.FAILED, reason, failed, False, None)

    def _block(
        self,
        repository: WorkspaceRepositoryPort,
        request: WorkspaceServiceGraphPublishInput,
        namespace: str,
        generation: WorkspaceGeneration,
        publication: object,
    ) -> WorkspacePublishOutcome:
        # The repository CAS atomically changes a stale candidate to BLOCKED.
        blocked = WorkspaceGeneration(
            generation.workspace_id, generation.generation_id, generation.snapshots, WorkspaceGenerationState.BLOCKED
        )
        return self._outcome(
            request,
            namespace,
            WorkspacePublishStatus.BLOCKED,
            WorkspacePublishReason.ACTIVE_COMPARE_AND_SET_REJECTED,
            blocked,
            True,
            getattr(publication, "active_generation_id", None),
        )

    @staticmethod
    def _outcome(
        request: WorkspaceServiceGraphPublishInput,
        namespace: str,
        status: WorkspacePublishStatus,
        reason: WorkspacePublishReason | None,
        generation: WorkspaceGeneration,
        graph_write_confirmed: bool,
        active_generation_id: str | None,
    ) -> WorkspacePublishOutcome:
        return WorkspacePublishOutcome(
            status,
            reason,
            request.workspace.workspace_id,
            request.generation_id,
            namespace,
            generation.state,
            graph_write_confirmed,
            active_generation_id,
        )


def _namespace_plan(plan: GraphWritePlan, namespace: str) -> GraphWritePlan:
    return GraphWritePlan(
        tuple(
            GraphNode(node.id, node.node_type, {**node.props, "workspace_generation_namespace": namespace})
            for node in plan.nodes
        ),
        tuple(
            GraphRelation(
                relation.id,
                relation.relation_type,
                relation.source_id,
                relation.target_id,
                {
                    **relation.props,
                    "workspace_generation_namespace": namespace,
                },
            )
            for relation in plan.relations
        ),
    )


def _contains_all_repositories(plan: GraphWritePlan, snapshots: tuple[WorkspaceRepositorySnapshot, ...]) -> bool:
    return {snapshot.repo_id for snapshot in snapshots} <= {
        node.props.get("repo_id") for node in plan.nodes if isinstance(node.props.get("repo_id"), str)
    }


def _has_one_namespace(plan: GraphWritePlan, namespace: str) -> bool:
    return all(node.props.get("workspace_generation_namespace") == namespace for node in plan.nodes) and all(
        relation.props.get("workspace_generation_namespace") == namespace for relation in plan.relations
    )


def _confirmed_receipt(receipt: object, plan: GraphWritePlan, namespace: str) -> bool:
    return (
        type(receipt) is WriteReceipt
        and receipt.confirmed
        and receipt.graph_namespace == namespace
        and receipt.readback == plan
        and receipt.node_count == len(plan.nodes)
        and receipt.relation_count == len(plan.relations)
    )


def _task_id(workspace_id: str, idempotency_key: str) -> str:
    return f"workspace-task-{hashlib.sha256(f'{workspace_id}\x00{idempotency_key}'.encode()).hexdigest()}"


def _require_nonblank(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
