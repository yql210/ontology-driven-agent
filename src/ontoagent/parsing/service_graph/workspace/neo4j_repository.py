from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar

from .models import (
    BuildTask,
    Workspace,
    WorkspaceActiveBinding,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspacePublishResult,
    WorkspacePublishStatus,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)


class Neo4jDriver(Protocol):
    def session(self) -> object: ...


T = TypeVar("T")


class Neo4jWorkspaceRepository:
    """Dedicated workspace persistence; it deliberately does not read service graph manifests."""

    WORKSPACE_LABEL = "OntoAgentWorkspace"
    TASK_LABEL = "OntoAgentWorkspaceBuildTask"
    GENERATION_LABEL = "OntoAgentWorkspaceGeneration"
    SNAPSHOT_LABEL = "OntoAgentWorkspaceRepositorySnapshot"
    ACTIVE_LABEL = "OntoAgentWorkspaceActiveBinding"
    ENSURE_CONSTRAINTS = (
        "CREATE CONSTRAINT ontoagent_workspace_identity IF NOT EXISTS FOR (n:OntoAgentWorkspace) REQUIRE n.workspaceId IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_task_identity IF NOT EXISTS FOR (n:OntoAgentWorkspaceBuildTask) REQUIRE n.taskId IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_task_idempotency IF NOT EXISTS FOR (n:OntoAgentWorkspaceBuildTask) REQUIRE (n.workspaceId, n.idempotencyKey) IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_generation_identity IF NOT EXISTS FOR (n:OntoAgentWorkspaceGeneration) REQUIRE (n.workspaceId, n.generationId) IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_snapshot_identity IF NOT EXISTS FOR (n:OntoAgentWorkspaceRepositorySnapshot) REQUIRE (n.workspaceId, n.generationId, n.repoId) IS UNIQUE",
        "CREATE CONSTRAINT ontoagent_workspace_active_identity IF NOT EXISTS FOR (n:OntoAgentWorkspaceActiveBinding) REQUIRE n.workspaceId IS UNIQUE",
    )
    CREATE_WORKSPACE_QUERY = (
        "MERGE (workspace:OntoAgentWorkspace {workspaceId: $workspace_id}) "
        "ON CREATE SET workspace.name = $name "
        "WITH workspace WHERE workspace.name = $name "
        "RETURN workspace.workspaceId AS workspace_id, workspace.name AS name"
    )
    GET_WORKSPACE_QUERY = (
        "MATCH (workspace:OntoAgentWorkspace {workspaceId: $workspace_id}) "
        "RETURN workspace.workspaceId AS workspace_id, workspace.name AS name"
    )
    CREATE_TASK_QUERY = (
        "MATCH (:OntoAgentWorkspace {workspaceId: $workspace_id}) "
        "MERGE (task:OntoAgentWorkspaceBuildTask {workspaceId: $workspace_id, idempotencyKey: $idempotency_key}) "
        "ON CREATE SET task.taskId = $task_id "
        "RETURN task.taskId AS task_id, task.workspaceId AS workspace_id, task.idempotencyKey AS idempotency_key"
    )
    CREATE_GENERATION_TASK_QUERY = (
        "MATCH (:OntoAgentWorkspace {workspaceId: $workspace_id}) "
        "MERGE (task:OntoAgentWorkspaceBuildTask {workspaceId: $workspace_id, idempotencyKey: $idempotency_key}) "
        "ON CREATE SET task.taskId = $task_id, task.generationId = $generation_id "
        "RETURN task.taskId AS task_id, task.workspaceId AS workspace_id, task.idempotencyKey AS idempotency_key, "
        "task.generationId AS generation_id"
    )
    GET_TASK_QUERY = (
        "MATCH (task:OntoAgentWorkspaceBuildTask {taskId: $task_id}) "
        "RETURN task.taskId AS task_id, task.workspaceId AS workspace_id, task.idempotencyKey AS idempotency_key, "
        "task.generationId AS generation_id"
    )
    CREATE_GENERATION_QUERY = (
        "MATCH (:OntoAgentWorkspace {workspaceId: $workspace_id}) "
        "MERGE (generation:OntoAgentWorkspaceGeneration {workspaceId: $workspace_id, generationId: $generation_id}) "
        "ON CREATE SET generation.state = $state, generation.snapshotFingerprint = $snapshot_fingerprint "
        "WITH generation WHERE generation.state = $state AND generation.snapshotFingerprint = $snapshot_fingerprint "
        "UNWIND $snapshots AS snapshot "
        "MERGE (frozen:OntoAgentWorkspaceRepositorySnapshot "
        "{workspaceId: $workspace_id, generationId: $generation_id, repoId: snapshot.repo_id}) "
        "ON CREATE SET frozen.branch = snapshot.branch, frozen.sourceRevision = snapshot.source_revision, "
        "frozen.sourceKind = snapshot.source_kind, frozen.sourceDescriptor = snapshot.source_descriptor "
        "MERGE (generation)-[:HAS_FROZEN_SNAPSHOT]->(frozen) "
        "RETURN generation.generationId AS generation_id"
    )
    GET_GENERATION_QUERY = (
        "MATCH (generation:OntoAgentWorkspaceGeneration {workspaceId: $workspace_id, generationId: $generation_id}) "
        "OPTIONAL MATCH (generation)-[:HAS_FROZEN_SNAPSHOT]->(snapshot:OntoAgentWorkspaceRepositorySnapshot) "
        "WITH generation, snapshot ORDER BY snapshot.repoId "
        "RETURN generation.workspaceId AS workspace_id, generation.generationId AS generation_id, generation.state AS state, "
        "collect({repo_id: snapshot.repoId, branch: snapshot.branch, source_revision: snapshot.sourceRevision, "
        "source_kind: snapshot.sourceKind, source_descriptor: snapshot.sourceDescriptor}) AS snapshots"
    )
    GET_ACTIVE_BINDING_QUERY = (
        "MATCH (binding:OntoAgentWorkspaceActiveBinding {workspaceId: $workspace_id}) "
        "RETURN binding.workspaceId AS workspace_id, binding.generationId AS generation_id"
    )
    ADVANCE_GENERATION_STATE_QUERY = (
        "MATCH (generation:OntoAgentWorkspaceGeneration {workspaceId: $workspace_id, generationId: $generation_id}) "
        "WHERE generation.state = $expected_state "
        "SET generation.state = $target_state "
        "RETURN generation.generationId AS generation_id"
    )
    PUBLISH_GENERATION_QUERY = (
        "MERGE (binding:OntoAgentWorkspaceActiveBinding {workspaceId: $workspace_id}) "
        "ON CREATE SET binding._ontoagentCreatedForCas = true "
        "WITH binding, coalesce(binding._ontoagentCreatedForCas, false) AS created_for_cas "
        "WITH binding, created_for_cas, ((created_for_cas AND $expected_active_generation_id IS NULL) "
        "OR (NOT created_for_cas AND binding.generationId = $expected_active_generation_id)) AS expected_matches "
        "OPTIONAL MATCH (candidate:OntoAgentWorkspaceGeneration "
        "{workspaceId: $workspace_id, generationId: $candidate_generation_id}) "
        "OPTIONAL MATCH (candidate)-[:HAS_FROZEN_SNAPSHOT]->(snapshot:OntoAgentWorkspaceRepositorySnapshot) "
        "WITH binding, created_for_cas, expected_matches, candidate, collect(snapshot) AS frozen_snapshots, "
        "collect(DISTINCT snapshot.repoId) AS frozen_repo_ids "
        "WITH binding, created_for_cas, expected_matches, candidate, "
        "[snapshot IN frozen_snapshots WHERE snapshot IS NOT NULL] AS persisted_snapshots, frozen_repo_ids "
        "WITH binding, created_for_cas, expected_matches, candidate, "
        "(candidate IS NOT NULL) AS candidate_exists, "
        "(candidate IS NOT NULL AND candidate.state = 'verifying') AS candidate_verifying, "
        "(candidate IS NOT NULL AND candidate.state = 'verifying' "
        "AND candidate.snapshotFingerprint IS NOT NULL "
        "AND size(persisted_snapshots) > 0 "
        "AND size(persisted_snapshots) = size(frozen_repo_ids) "
        "AND size(persisted_snapshots) = size([snapshot IN persisted_snapshots "
        "WHERE snapshot.workspaceId = $workspace_id "
        "AND snapshot.generationId = $candidate_generation_id "
        "AND snapshot.repoId IS NOT NULL AND snapshot.branch IS NOT NULL "
        "AND snapshot.sourceRevision IS NOT NULL AND snapshot.sourceKind IS NOT NULL "
        "AND snapshot.sourceDescriptor IS NOT NULL])) AS candidate_ready "
        "OPTIONAL MATCH (prior:OntoAgentWorkspaceGeneration "
        "{workspaceId: $workspace_id, generationId: binding.generationId}) "
        "WITH binding, created_for_cas, expected_matches, candidate, candidate_exists, candidate_verifying, candidate_ready, prior "
        "FOREACH (_ IN CASE WHEN expected_matches AND candidate_ready THEN [1] ELSE [] END | "
        "SET candidate.state = 'active', binding.generationId = $candidate_generation_id, "
        "binding._ontoagentCreatedForCas = null, prior.state = CASE WHEN prior.state = 'active' THEN 'superseded' ELSE prior.state END) "
        "FOREACH (_ IN CASE WHEN NOT expected_matches AND candidate_ready THEN [1] ELSE [] END | "
        "SET candidate.state = 'blocked') "
        "FOREACH (_ IN CASE WHEN created_for_cas AND (NOT expected_matches OR NOT candidate_ready) THEN [1] ELSE [] END | "
        "DELETE binding) "
        "RETURN CASE WHEN created_for_cas AND (NOT expected_matches OR NOT candidate_ready) THEN null "
        "ELSE binding.generationId END AS active_generation_id, expected_matches, candidate_exists, candidate_verifying, "
        "candidate_ready"
    )

    def __init__(self, driver: Neo4jDriver) -> None:
        self._driver = driver
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._driver.session() as session:  # type: ignore[union-attr]
            for query in self.ENSURE_CONSTRAINTS:
                session.run(query)  # type: ignore[union-attr]

    def create_workspace(self, workspace: Workspace) -> Workspace:
        _require_exact(workspace, Workspace, "workspace")
        return self._one(self.CREATE_WORKSPACE_QUERY, self._workspace_params(workspace), self._workspace_from_row)

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        _require_nonblank(workspace_id, "workspace_id")
        return self._optional(self.GET_WORKSPACE_QUERY, {"workspace_id": workspace_id}, self._workspace_from_row)

    def create_build_task(self, task: BuildTask) -> BuildTask:
        _require_exact(task, BuildTask, "task")
        query = self.CREATE_GENERATION_TASK_QUERY if task.generation_id is not None else self.CREATE_TASK_QUERY
        return self._one(query, self._task_params(task), self._task_from_row)

    def get_build_task(self, task_id: str) -> BuildTask | None:
        _require_nonblank(task_id, "task_id")
        return self._optional(self.GET_TASK_QUERY, {"task_id": task_id}, self._task_from_row)

    def create_generation(self, generation: WorkspaceGeneration) -> WorkspaceGeneration:
        _require_exact(generation, WorkspaceGeneration, "generation")
        params = {
            "workspace_id": generation.workspace_id,
            "generation_id": generation.generation_id,
            "state": generation.state.value,
            "snapshot_fingerprint": _snapshot_fingerprint(generation.snapshots),
            "snapshots": [_snapshot_params(snapshot) for snapshot in generation.snapshots],
        }
        self._one(self.CREATE_GENERATION_QUERY, params, _generation_id_from_row)
        return generation

    def get_generation(self, workspace_id: str, generation_id: str) -> WorkspaceGeneration | None:
        _require_nonblank(workspace_id, "workspace_id")
        _require_nonblank(generation_id, "generation_id")
        return self._optional(
            self.GET_GENERATION_QUERY,
            {"workspace_id": workspace_id, "generation_id": generation_id},
            self._generation_from_row,
        )

    def get_active_binding(self, workspace_id: str) -> WorkspaceActiveBinding | None:
        _require_nonblank(workspace_id, "workspace_id")
        return self._optional(
            self.GET_ACTIVE_BINDING_QUERY, {"workspace_id": workspace_id}, self._active_binding_from_row
        )

    def advance_generation_state(
        self, generation: WorkspaceGeneration, target: WorkspaceGenerationState
    ) -> WorkspaceGeneration:
        """Persist one valid state-machine transition only if the stored source state still matches."""
        _require_exact(generation, WorkspaceGeneration, "generation")
        advanced = generation.transition_to(target)
        self._one(
            self.ADVANCE_GENERATION_STATE_QUERY,
            {
                "workspace_id": generation.workspace_id,
                "generation_id": generation.generation_id,
                "expected_state": generation.state.value,
                "target_state": target.value,
            },
            _generation_id_from_row,
        )
        return advanced

    def publish_generation(
        self, workspace_id: str, expected_active_generation_id: str | None, candidate_generation_id: str
    ) -> WorkspacePublishResult:
        """Atomically publish a complete VERIFYING workspace generation through its sole binding."""
        _require_nonblank(workspace_id, "workspace_id")
        _require_nonblank(candidate_generation_id, "candidate_generation_id")
        if expected_active_generation_id is not None:
            _require_nonblank(expected_active_generation_id, "expected_active_generation_id")
        row = self._one(
            self.PUBLISH_GENERATION_QUERY,
            {
                "workspace_id": workspace_id,
                "expected_active_generation_id": expected_active_generation_id,
                "candidate_generation_id": candidate_generation_id,
            },
            _mapping,
        )
        active_generation_id = row.get("active_generation_id")
        if active_generation_id is not None and not isinstance(active_generation_id, str):
            raise RuntimeError("Neo4j workspace CAS publication returned a malformed active generation")
        candidate_exists = row.get("candidate_exists")
        candidate_verifying = row.get("candidate_verifying")
        candidate_ready = row.get("candidate_ready")
        expected_matches = row.get("expected_matches")
        if (
            type(candidate_exists) is not bool
            or type(candidate_verifying) is not bool
            or type(candidate_ready) is not bool
            or type(expected_matches) is not bool
        ):
            raise RuntimeError("Neo4j workspace CAS publication returned a malformed result")
        if not candidate_exists:
            status = WorkspacePublishStatus.CANDIDATE_INVALID
        elif not candidate_verifying:
            status = WorkspacePublishStatus.CANDIDATE_NOT_READY
        elif not candidate_ready:
            status = WorkspacePublishStatus.CANDIDATE_INVALID
        elif not expected_matches:
            status = WorkspacePublishStatus.STALE_ACTIVE
        else:
            status = WorkspacePublishStatus.PUBLISHED
        return WorkspacePublishResult(status, active_generation_id)

    def _one(self, query: str, params: dict[str, object], decoder: Callable[[object], T]) -> T:
        result = self._optional(query, params, decoder)
        if result is None:
            raise ValueError("workspace persistence rejected immutable identity or missing workspace")
        return result

    def _optional(self, query: str, params: dict[str, object], decoder: Callable[[object], T]) -> T | None:
        with self._driver.session() as session:  # type: ignore[union-attr]
            rows = list(session.run(query, **params))  # type: ignore[union-attr]
        return None if not rows else decoder(rows[0])

    @staticmethod
    def _workspace_params(workspace: Workspace) -> dict[str, object]:
        return {"workspace_id": workspace.workspace_id, "name": workspace.name}

    @staticmethod
    def _task_params(task: BuildTask) -> dict[str, object]:
        params: dict[str, object] = {
            "task_id": task.task_id,
            "workspace_id": task.workspace_id,
            "idempotency_key": task.idempotency_key,
        }
        if task.generation_id is not None:
            params["generation_id"] = task.generation_id
        return params

    @staticmethod
    def _workspace_from_row(row: object) -> Workspace:
        values = _mapping(row)
        return Workspace(_string(values, "workspace_id"), _string(values, "name"))

    @staticmethod
    def _task_from_row(row: object) -> BuildTask:
        values = _mapping(row)
        return BuildTask(
            _string(values, "task_id"),
            _string(values, "workspace_id"),
            _string(values, "idempotency_key"),
            _optional_nonblank_string(values, "generation_id"),
        )

    @staticmethod
    def _generation_from_row(row: object) -> WorkspaceGeneration:
        values = _mapping(row)
        snapshots = values.get("snapshots")
        if not isinstance(snapshots, list) or not snapshots:
            raise ValueError("malformed persisted workspace generation snapshots")
        workspace_id = _string(values, "workspace_id")
        try:
            return WorkspaceGeneration(
                workspace_id,
                _string(values, "generation_id"),
                tuple(_snapshot_from_mapping(workspace_id, snapshot) for snapshot in snapshots),
                WorkspaceGenerationState(_string(values, "state")),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("malformed persisted workspace generation") from exc

    @staticmethod
    def _active_binding_from_row(row: object) -> WorkspaceActiveBinding:
        values = _mapping(row)
        return WorkspaceActiveBinding(_string(values, "workspace_id"), _string(values, "generation_id"))


def _snapshot_params(snapshot: WorkspaceRepositorySnapshot) -> dict[str, str]:
    return {
        "repo_id": snapshot.repo_id,
        "branch": snapshot.branch,
        "source_revision": snapshot.source_revision,
        "source_kind": snapshot.source.kind.value,
        "source_descriptor": snapshot.source.value,
    }


def _snapshot_fingerprint(snapshots: tuple[WorkspaceRepositorySnapshot, ...]) -> str:
    payload = [_snapshot_params(snapshot) for snapshot in snapshots]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _generation_id_from_row(row: object) -> str:
    return _string(_mapping(row), "generation_id")


def _snapshot_from_mapping(workspace_id: str, row: object) -> WorkspaceRepositorySnapshot:
    values = _mapping(row)
    return WorkspaceRepositorySnapshot(
        workspace_id,
        _string(values, "repo_id"),
        _string(values, "branch"),
        _string(values, "source_revision"),
        WorkspaceSourceDescriptor(
            WorkspaceSourceKind(_string(values, "source_kind")), _string(values, "source_descriptor")
        ),
    )


def _mapping(row: object) -> Mapping[str, object]:
    if isinstance(row, Mapping):
        return row
    try:
        return dict(row)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("Neo4j row is not mapping-like") from exc


def _string(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    _require_nonblank(value, key)
    return value


def _optional_nonblank_string(values: Mapping[str, object], key: str) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    _require_nonblank(value, key)
    return value


def _require_nonblank(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")


def _require_exact(value: object, expected: type[object], name: str) -> None:
    if type(value) is not expected:
        raise ValueError(f"{name} must be a {expected.__name__}")
