from __future__ import annotations

from dataclasses import replace

import pytest

from ontoagent.parsing.service_graph.workspace.models import (
    BuildTask,
    Workspace,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)


def _snapshot(repo_id: str = "repo-1", workspace_id: str = "workspace-1") -> WorkspaceRepositorySnapshot:
    return WorkspaceRepositorySnapshot(
        workspace_id,
        repo_id,
        "main",
        "0123456789abcdef",
        WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, "https://example.test/org/repo.git"),
    )


def test_workspace_domain_values_are_frozen_and_validate_persistable_source_descriptor() -> None:
    workspace = Workspace("workspace-1", "Checkout")
    snapshot = _snapshot()

    assert workspace.workspace_id == "workspace-1"
    assert snapshot.source.kind is WorkspaceSourceKind.GIT
    with pytest.raises(AttributeError):
        workspace.name = "Other"  # type: ignore[misc]
    with pytest.raises(ValueError, match="relative"):
        WorkspaceSourceDescriptor(WorkspaceSourceKind.LOCAL, "/private/repository")
    with pytest.raises(ValueError, match="credentials"):
        WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, "https://token@example.test/org/repo.git")


@pytest.mark.parametrize(
    "target",
    [
        WorkspaceGenerationState.RESOLVING,
        WorkspaceGenerationState.ACTIVE,
        WorkspaceGenerationState.SUPERSEDED,
    ],
)
def test_generation_rejects_invalid_state_transition(target: WorkspaceGenerationState) -> None:
    generation = WorkspaceGeneration("workspace-1", "generation-1", (_snapshot(),))

    with pytest.raises(ValueError, match="invalid workspace generation state transition"):
        generation.transition_to(target)


def test_generation_requires_complete_unique_snapshots_from_its_workspace() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValueError, match="unique repo_id"):
        WorkspaceGeneration("workspace-1", "generation-1", (snapshot, snapshot))
    with pytest.raises(ValueError, match="workspace_id mismatch"):
        WorkspaceGeneration("workspace-1", "generation-1", (_snapshot(workspace_id="workspace-2"),))
    with pytest.raises(ValueError, match="source_revision"):
        replace(snapshot, source_revision=" ")


def test_generation_transitions_through_lifecycle_and_active_can_be_superseded() -> None:
    generation = WorkspaceGeneration("workspace-1", "generation-1", (_snapshot(),))

    for state in (
        WorkspaceGenerationState.EXTRACTING,
        WorkspaceGenerationState.RESOLVING,
        WorkspaceGenerationState.WRITING,
        WorkspaceGenerationState.VERIFYING,
        WorkspaceGenerationState.ACTIVE,
        WorkspaceGenerationState.SUPERSEDED,
    ):
        generation = generation.transition_to(state)

    assert generation.state is WorkspaceGenerationState.SUPERSEDED


def test_build_task_requires_nonblank_idempotency_key() -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        BuildTask("task-1", "workspace-1", " ")
