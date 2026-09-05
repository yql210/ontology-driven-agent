from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.graph_plan import GraphNode, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import WriteReceipt
from ontoagent.parsing.service_graph.models import DetectorFacts, RepositorySnapshot
from ontoagent.parsing.service_graph.resolver import ResolveResult
from ontoagent.parsing.service_graph.workspace.models import (
    Workspace,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspacePublishResult,
    WorkspacePublishStatus,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import (
    WorkspacePublishStatus as OrchestratorStatus,
)
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import (
    WorkspaceServiceGraphPublishComponents,
    WorkspaceServiceGraphPublishInput,
    WorkspaceServiceGraphPublishOrchestrator,
)


@dataclass
class _Factory:
    components: WorkspaceServiceGraphPublishComponents
    namespaces: list[str]

    def create(self, namespace: str) -> WorkspaceServiceGraphPublishComponents:
        self.namespaces.append(namespace)
        return self.components


class _Registry:
    ids = ("detector-a",)

    def __init__(self, calls: list[str], *, fail: bool = False) -> None:
        self._calls = calls
        self._fail = fail

    def detect(self, snapshot: RepositorySnapshot, detector_id: str | None = None) -> DetectorFacts:
        self._calls.append(f"detect:{snapshot.repo_id}:{detector_id}")
        if self._fail:
            raise RuntimeError("detector failed")
        return DetectorFacts("detector-a", "1", snapshot.repo_id, snapshot.source_revision, (), (), (), ())


class _Resolver:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def resolve(self, batches: tuple[object, ...]) -> ResolveResult:
        self._calls.append("resolve")
        return ResolveResult((), (), ())


class _PlanBuilder:
    def __init__(self, calls: list[str], *, missing_repo: bool = False) -> None:
        self._calls = calls
        self._missing_repo = missing_repo

    def build(self, result: ResolveResult) -> GraphWritePlan:
        self._calls.append("plan")
        repo_ids = ("repo-a", "repo-b") if self._missing_repo else ("repo-a", "repo-b", "repo-c")
        nodes = tuple(
            GraphNode(repo_id, "Endpoint", {"id": repo_id, "repo_id": repo_id, "evidence_ids": (repo_id,)})
            for repo_id in repo_ids
        )
        return GraphWritePlan(nodes, ())


class _Writer:
    def __init__(self, calls: list[str], *, confirmed: bool = True) -> None:
        self._calls = calls
        self._confirmed = confirmed
        self.namespace = ""

    def write(self, plan: GraphWritePlan) -> WriteReceipt:
        self._calls.append("write")
        return WriteReceipt(self._confirmed, len(plan.nodes), len(plan.relations), plan, self.namespace)


class _Repository:
    def __init__(
        self, calls: list[str], *, publication: WorkspacePublishStatus = WorkspacePublishStatus.PUBLISHED
    ) -> None:
        self._calls = calls
        self._publication = publication
        self.active = "old-generation"

    def create_workspace(self, workspace: Workspace) -> Workspace:
        self._calls.append("workspace")
        return workspace

    def create_build_task(self, task: object) -> object:
        self._calls.append("task")
        return task

    def create_generation(self, generation: WorkspaceGeneration) -> WorkspaceGeneration:
        self._calls.append("generation")
        return generation

    def advance_generation_state(
        self, generation: WorkspaceGeneration, target: WorkspaceGenerationState
    ) -> WorkspaceGeneration:
        self._calls.append(f"state:{target.value}")
        return generation.transition_to(target)

    def publish_generation(
        self, workspace_id: str, expected_active_generation_id: str | None, candidate_generation_id: str
    ) -> WorkspacePublishResult:
        self._calls.append(f"cas:{expected_active_generation_id}:{candidate_generation_id}")
        if self._publication is WorkspacePublishStatus.PUBLISHED:
            self.active = candidate_generation_id
        return WorkspacePublishResult(self._publication, self.active)


def _input() -> WorkspaceServiceGraphPublishInput:
    workspace = Workspace("workspace-1", "Workspace")
    persisted = tuple(
        WorkspaceRepositorySnapshot(
            workspace.workspace_id,
            repo_id,
            "main",
            f"revision-{repo_id}",
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id in ("repo-a", "repo-b", "repo-c")
    )
    runtime = tuple(
        RepositorySnapshot(snapshot.repo_id, snapshot.source_revision, Path("."), frozenset({"java"}))
        for snapshot in persisted
    )
    return WorkspaceServiceGraphPublishInput(workspace, persisted, runtime, "request-1", "generation-1", None)


def _orchestrator(
    calls: list[str],
    *,
    detector_fails: bool = False,
    confirmed: bool = True,
    missing_repo: bool = False,
    publication: WorkspacePublishStatus = WorkspacePublishStatus.PUBLISHED,
) -> tuple[WorkspaceServiceGraphPublishOrchestrator, _Factory, _Writer, _Repository]:
    writer = _Writer(calls, confirmed=confirmed)
    repository = _Repository(calls, publication=publication)
    components = WorkspaceServiceGraphPublishComponents(
        _Registry(calls, fail=detector_fails),
        _Resolver(calls),
        _PlanBuilder(calls, missing_repo=missing_repo),
        writer,
        repository,
    )
    factory = _Factory(components, [])
    return WorkspaceServiceGraphPublishOrchestrator(factory), factory, writer, repository


def test_publish_transitions_frozen_workspace_generation_and_uses_only_workspace_cas() -> None:
    calls: list[str] = []
    orchestrator, factory, writer, _ = _orchestrator(calls)
    candidate = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")
    writer.namespace = candidate

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.ACTIVE
    assert outcome.graph_write_confirmed
    assert outcome.to_dict()["status"] == "active"
    assert factory.namespaces == [candidate]
    assert calls == [
        "workspace",
        "task",
        "generation",
        "state:extracting",
        "detect:repo-a:detector-a",
        "detect:repo-b:detector-a",
        "detect:repo-c:detector-a",
        "state:resolving",
        "resolve",
        "plan",
        "state:writing",
        "write",
        "state:verifying",
        "cas:None:generation-1",
    ]
    assert "manifest" not in " ".join(calls)


def test_publish_detector_failure_marks_generation_failed_without_cas() -> None:
    calls: list[str] = []
    orchestrator, _, writer, _ = _orchestrator(calls, detector_fails=True)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.FAILED
    assert outcome.generation_state is WorkspaceGenerationState.FAILED
    assert not any(call.startswith("cas:") for call in calls)


def test_publish_unconfirmed_readback_marks_generation_failed_without_cas() -> None:
    calls: list[str] = []
    orchestrator, _, writer, _ = _orchestrator(calls, confirmed=False)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.FAILED
    assert outcome.generation_state is WorkspaceGenerationState.FAILED
    assert not any(call.startswith("cas:") for call in calls)


def test_publish_stale_cas_blocks_candidate_and_preserves_old_active_binding() -> None:
    calls: list[str] = []
    orchestrator, _, writer, repository = _orchestrator(calls, publication=WorkspacePublishStatus.STALE_ACTIVE)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.BLOCKED
    assert outcome.generation_state is WorkspaceGenerationState.BLOCKED
    assert repository.active == "old-generation"
    assert calls[-1] == "cas:None:generation-1"


def test_publish_missing_frozen_repository_from_plan_fails_before_write_and_cas() -> None:
    calls: list[str] = []
    orchestrator, _, writer, _ = _orchestrator(calls, missing_repo=True)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.FAILED
    assert outcome.generation_state is WorkspaceGenerationState.FAILED
    assert "write" not in calls
    assert not any(call.startswith("cas:") for call in calls)


def test_publish_defensively_rejects_less_than_three_frozen_repositories_before_factory_or_write() -> None:
    calls: list[str] = []
    orchestrator, factory, _, _ = _orchestrator(calls)
    valid = _input()
    malformed = object.__new__(WorkspaceServiceGraphPublishInput)
    for field_name, value in (
        ("workspace", valid.workspace),
        ("snapshots", valid.snapshots[:2]),
        ("repository_snapshots", valid.repository_snapshots[:2]),
        ("task_idempotency_key", valid.task_idempotency_key),
        ("generation_id", valid.generation_id),
        ("expected_active_generation_id", valid.expected_active_generation_id),
        ("owned_work_dirs", ()),
    ):
        object.__setattr__(malformed, field_name, value)

    with pytest.raises(ValueError, match="at least three unique"):
        orchestrator.publish(malformed)

    assert factory.namespaces == []
    assert calls == []
