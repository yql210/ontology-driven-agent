from __future__ import annotations

import pytest

from ontoagent.parsing.service_graph.workspace.models import (
    BuildTask,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspacePublishStatus,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.neo4j_repository import Neo4jWorkspaceRepository


class _Session:
    def __init__(self, driver: _Driver) -> None:
        self._driver = driver

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def run(self, query: str, **parameters: object) -> list[dict[str, object]]:
        self._driver.calls.append((query, parameters))
        if query.startswith("CREATE CONSTRAINT"):
            return []
        return self._driver.results.pop(0) if self._driver.results else []


class _Driver:
    def __init__(self, results: list[list[dict[str, object]]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.results = list(results or [])

    def session(self) -> _Session:
        return _Session(self)


def _snapshot(repo_id: str = "repo-1") -> WorkspaceRepositorySnapshot:
    return WorkspaceRepositorySnapshot(
        "workspace-1",
        repo_id,
        "main",
        f"revision-{repo_id}",
        WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
    )


def _repository(driver: _Driver) -> Neo4jWorkspaceRepository:
    repository = Neo4jWorkspaceRepository(driver)
    driver.calls.clear()
    return repository


def test_create_build_task_uses_workspace_scoped_idempotency_key_and_returns_existing_task() -> None:
    driver = _Driver([[{"task_id": "task-original", "workspace_id": "workspace-1", "idempotency_key": "request-1"}]])
    repository = _repository(driver)

    task = repository.create_build_task(BuildTask("task-new", "workspace-1", "request-1"))

    query, params = driver.calls[-1]
    assert "OntoAgentWorkspaceBuildTask" in query
    assert "$idempotency_key" in query and "request-1" not in query
    assert params == {"task_id": "task-new", "workspace_id": "workspace-1", "idempotency_key": "request-1"}
    assert task == BuildTask("task-original", "workspace-1", "request-1")


def test_create_generation_serializes_frozen_snapshot_set_with_parameterized_cypher() -> None:
    driver = _Driver([[{"generation_id": "generation-1"}]])
    repository = _repository(driver)
    generation = WorkspaceGeneration("workspace-1", "generation-1", (_snapshot("repo-1"), _snapshot("repo-2")))

    persisted = repository.create_generation(generation)

    query, params = driver.calls[-1]
    assert "OntoAgentWorkspaceGeneration" in query
    assert "OntoAgentWorkspaceRepositorySnapshot" in query
    assert "$snapshots" in query and "revision-repo-1" not in query
    assert params["workspace_id"] == "workspace-1"
    assert params["generation_id"] == "generation-1"
    assert params["state"] == "pending"
    assert params["snapshots"] == [
        {
            "repo_id": "repo-1",
            "branch": "main",
            "source_revision": "revision-repo-1",
            "source_kind": "git",
            "source_descriptor": "https://example.test/repo-1.git",
        },
        {
            "repo_id": "repo-2",
            "branch": "main",
            "source_revision": "revision-repo-2",
            "source_kind": "git",
            "source_descriptor": "https://example.test/repo-2.git",
        },
    ]
    assert persisted == generation


def test_read_generation_and_active_binding_decode_persisted_records() -> None:
    driver = _Driver(
        [
            [
                {
                    "workspace_id": "workspace-1",
                    "generation_id": "generation-1",
                    "state": "verifying",
                    "snapshots": [
                        {
                            "repo_id": "repo-1",
                            "branch": "main",
                            "source_revision": "revision-1",
                            "source_kind": "git",
                            "source_descriptor": "https://example.test/repo-1.git",
                        }
                    ],
                }
            ],
            [{"workspace_id": "workspace-1", "generation_id": "generation-1"}],
        ]
    )
    repository = _repository(driver)

    generation = repository.get_generation("workspace-1", "generation-1")
    binding = repository.get_active_binding("workspace-1")

    assert generation == WorkspaceGeneration(
        "workspace-1",
        "generation-1",
        (
            WorkspaceRepositorySnapshot(
                "workspace-1",
                "repo-1",
                "main",
                "revision-1",
                WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, "https://example.test/repo-1.git"),
            ),
        ),
        WorkspaceGenerationState.VERIFYING,
    )
    assert binding is not None
    assert binding.workspace_id == "workspace-1"
    assert binding.generation_id == "generation-1"


def test_advance_generation_state_uses_valid_immutable_transition_and_parameterized_cypher() -> None:
    driver = _Driver([[{"generation_id": "generation-1"}]])
    repository = _repository(driver)
    generation = WorkspaceGeneration("workspace-1", "generation-1", (_snapshot(),), WorkspaceGenerationState.PENDING)

    advanced = repository.advance_generation_state(generation, WorkspaceGenerationState.EXTRACTING)

    query, params = driver.calls[-1]
    assert "OntoAgentWorkspaceGeneration" in query
    assert "generation.state = $expected_state" in query
    assert "$target_state" in query and "extracting" not in query
    assert params == {
        "workspace_id": "workspace-1",
        "generation_id": "generation-1",
        "expected_state": "pending",
        "target_state": "extracting",
    }
    assert advanced == generation.transition_to(WorkspaceGenerationState.EXTRACTING)


def test_advance_generation_state_rejects_invalid_paths_before_persistence() -> None:
    driver = _Driver()
    repository = _repository(driver)
    generation = WorkspaceGeneration("workspace-1", "generation-1", (_snapshot(),), WorkspaceGenerationState.PENDING)

    with pytest.raises(ValueError, match="invalid workspace generation state transition"):
        repository.advance_generation_state(generation, WorkspaceGenerationState.ACTIVE)

    assert driver.calls == []


def test_publish_generation_uses_one_workspace_cas_query_for_first_publish() -> None:
    driver = _Driver(
        [
            [
                {
                    "active_generation_id": "generation-1",
                    "expected_matches": True,
                    "candidate_exists": True,
                    "candidate_verifying": True,
                    "candidate_ready": True,
                }
            ]
        ]
    )
    repository = _repository(driver)

    result = repository.publish_generation("workspace-1", None, "generation-1")

    query, params = driver.calls[-1]
    assert "OntoAgentWorkspaceActiveBinding" in query
    assert "HAS_FROZEN_SNAPSHOT" in query
    assert "FOREACH" in query
    assert "$expected_active_generation_id" in query
    assert "OntoAgentServiceGraph" not in query
    assert "generation-1" not in query
    assert params == {
        "workspace_id": "workspace-1",
        "expected_active_generation_id": None,
        "candidate_generation_id": "generation-1",
    }
    assert result.status is WorkspacePublishStatus.PUBLISHED
    assert result.active_generation_id == "generation-1"


def test_publish_generation_cas_replacement_supersedes_prior_and_blocks_stale_candidate() -> None:
    driver = _Driver(
        [
            [
                {
                    "active_generation_id": "generation-2",
                    "expected_matches": True,
                    "candidate_exists": True,
                    "candidate_verifying": True,
                    "candidate_ready": True,
                }
            ]
        ]
    )
    repository = _repository(driver)

    result = repository.publish_generation("workspace-1", "generation-1", "generation-2")

    query, params = driver.calls[-1]
    assert "candidate.state = 'active'" in query
    assert "prior.state = CASE WHEN prior.state = 'active' THEN 'superseded'" in query
    assert "SET candidate.state = 'blocked'" in query
    assert "DELETE binding" in query
    assert params["expected_active_generation_id"] == "generation-1"
    assert result.status is WorkspacePublishStatus.PUBLISHED


def test_publish_generation_returns_typed_outcomes_for_stale_not_ready_and_invalid_candidates() -> None:
    driver = _Driver(
        [
            [
                {
                    "active_generation_id": "generation-1",
                    "expected_matches": False,
                    "candidate_exists": True,
                    "candidate_verifying": True,
                    "candidate_ready": True,
                }
            ],
            [
                {
                    "active_generation_id": "generation-1",
                    "expected_matches": True,
                    "candidate_exists": True,
                    "candidate_verifying": False,
                    "candidate_ready": False,
                }
            ],
            [
                {
                    "active_generation_id": "generation-1",
                    "expected_matches": True,
                    "candidate_exists": False,
                    "candidate_verifying": False,
                    "candidate_ready": False,
                }
            ],
        ]
    )
    repository = _repository(driver)

    stale = repository.publish_generation("workspace-1", "old", "generation-2")
    not_ready = repository.publish_generation("workspace-1", "generation-1", "generation-3")
    invalid = repository.publish_generation("workspace-1", "generation-1", "missing")

    assert stale.status is WorkspacePublishStatus.STALE_ACTIVE
    assert stale.active_generation_id == "generation-1"
    assert not_ready.status is WorkspacePublishStatus.CANDIDATE_NOT_READY
    assert invalid.status is WorkspacePublishStatus.CANDIDATE_INVALID


def test_publish_generation_is_workspace_scoped() -> None:
    driver = _Driver(
        [
            [
                {
                    "active_generation_id": "generation-1",
                    "expected_matches": True,
                    "candidate_exists": True,
                    "candidate_verifying": True,
                    "candidate_ready": True,
                }
            ]
        ]
    )
    repository = _repository(driver)

    result = repository.publish_generation("workspace-2", None, "generation-1")

    assert result.status is WorkspacePublishStatus.PUBLISHED
    query, params = driver.calls[-1]
    assert "workspaceId: $workspace_id" in query
    assert params["workspace_id"] == "workspace-2"
