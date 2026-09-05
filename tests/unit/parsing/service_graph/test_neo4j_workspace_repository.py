from __future__ import annotations

from ontoagent.parsing.service_graph.workspace.models import (
    BuildTask,
    WorkspaceGeneration,
    WorkspaceGenerationState,
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
