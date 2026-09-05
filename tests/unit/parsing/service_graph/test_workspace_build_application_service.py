from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.workspace.build_application_service import WorkspaceBuildApplicationService
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import WorkspacePublishStatus


def _git_repository(tmp_path: Path, name: str) -> tuple[Path, str]:
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True, capture_output=True)
    (root / "Example.java").write_text("class Example {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return root, revision


def _manifest(tmp_path: Path, repositories: list[dict[str, object]]) -> Path:
    path = tmp_path / "workspace.json"
    path.write_text(
        json.dumps({"workspace_id": "workspace-1", "name": "Workspace", "repositories": repositories}),
        encoding="utf-8",
    )
    return path


def _repositories(tmp_path: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for repo_id in ("repo-a", "repo-b", "repo-c"):
        root, revision = _git_repository(tmp_path, repo_id)
        result.append(
            {
                "repo_id": repo_id,
                "path": str(root),
                "branch": "main",
                "source_revision": revision,
                "languages": ["java", "yaml"],
            }
        )
    return result


def test_build_freezes_local_git_repositories_and_routes_validated_request(tmp_path: Path) -> None:
    # Arrange
    received = []
    service = WorkspaceBuildApplicationService(received.append, id_factory=iter(("task-key", "generation-1")).__next__)

    # Act
    result = service.build(_manifest(tmp_path, _repositories(tmp_path)))

    # Assert
    request = received[0]
    assert result.task_id == WorkspaceBuildApplicationService.task_id_for("workspace-1", "task-key")
    assert result.generation_id == "generation-1"
    assert tuple(snapshot.repo_id for snapshot in request.snapshots) == ("repo-a", "repo-b", "repo-c")
    assert all(len(snapshot.source_revision) == 40 for snapshot in request.snapshots)
    assert all(snapshot.source.value == snapshot.repo_id for snapshot in request.snapshots)
    assert all(snapshot.languages == frozenset({"java", "yaml"}) for snapshot in request.repository_snapshots)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda repositories: repositories.pop(), "at least three"),
        (lambda repositories: repositories.__setitem__(0, {**repositories[0], "path": "/missing"}), "path"),
        (lambda repositories: repositories.__setitem__(0, {**repositories[0], "source_revision": "stale"}), "revision"),
        (lambda repositories: repositories.__setitem__(1, {**repositories[1], "repo_id": "repo-a"}), "duplicate"),
        (
            lambda repositories: repositories.__setitem__(
                0, {key: value for key, value in repositories[0].items() if key != "languages"}
            ),
            "languages",
        ),
    ],
)
def test_build_rejects_invalid_manifest_before_publishing(tmp_path: Path, change: object, message: str) -> None:
    # Arrange
    repositories = _repositories(tmp_path)
    change(repositories)  # type: ignore[operator]
    published = []
    service = WorkspaceBuildApplicationService(published.append)

    # Act / Assert
    with pytest.raises(ValueError, match=message):
        service.build(_manifest(tmp_path, repositories))
    assert published == []


def test_build_does_not_construct_llm_or_embedding_dependencies(tmp_path: Path) -> None:
    # Arrange
    received = []
    service = WorkspaceBuildApplicationService(received.append, id_factory=iter(("task-key", "generation-1")).__next__)

    # Act
    service.build(_manifest(tmp_path, _repositories(tmp_path)))

    # Assert
    assert len(received) == 1
    assert WorkspacePublishStatus.ACTIVE.value == "active"


def test_build_rejects_non_git_directory_before_publishing(tmp_path: Path) -> None:
    # Arrange
    repositories = _repositories(tmp_path)
    non_git = tmp_path / "not-a-repository"
    non_git.mkdir()
    repositories[0] = {**repositories[0], "path": str(non_git)}
    published = []
    service = WorkspaceBuildApplicationService(published.append)

    # Act / Assert
    with pytest.raises(ValueError, match="not a Git repository"):
        service.build(_manifest(tmp_path, repositories))
    assert published == []


def test_build_rejects_branch_mismatch_before_publishing(tmp_path: Path) -> None:
    # Arrange
    repositories = _repositories(tmp_path)
    repositories[0] = {**repositories[0], "branch": "release"}
    published = []
    service = WorkspaceBuildApplicationService(published.append)

    # Act / Assert
    with pytest.raises(ValueError, match="branch mismatch"):
        service.build(_manifest(tmp_path, repositories))
    assert published == []


@pytest.mark.parametrize(
    "git_url",
    [
        "file:///tmp/repository.git",
        "/tmp/repository.git",
        "https://token@example.test/org/repository.git",
        "https://example.test:8443/org/repository.git",
        "git@example.test:org/repository.git",
    ],
)
def test_prepare_rejects_unsafe_git_urls_before_clone(tmp_path: Path, git_url: str) -> None:
    repositories = _repositories(tmp_path)
    repositories[0] = {key: value for key, value in repositories[0].items() if key != "path"}
    repositories[0]["git_url"] = git_url
    calls: list[tuple[list[str], Path | None]] = []
    service = WorkspaceBuildApplicationService(
        lambda _: None,
        git_runner=lambda args, cwd: calls.append((args, cwd)) or "",
    )

    with pytest.raises(ValueError, match="git_url"):
        service.prepare(
            {"workspace_id": "workspace-1", "name": "Workspace", "repositories": repositories},
            tmp_path,
            "request-1",
            "generation-1",
        )

    assert calls == []


def test_prepare_rejects_repository_with_both_path_and_git_url(tmp_path: Path) -> None:
    repositories = _repositories(tmp_path)
    repositories[0]["git_url"] = "https://example.test/org/repository.git"
    service = WorkspaceBuildApplicationService(lambda _: None)

    with pytest.raises(ValueError, match="exactly one"):
        service.prepare(
            {"workspace_id": "workspace-1", "name": "Workspace", "repositories": repositories},
            tmp_path,
            "request-1",
            "generation-1",
        )


def test_prepare_clones_allowed_git_url_freezes_commit_and_sanitizes_source(tmp_path: Path) -> None:
    revision = "0123456789abcdef0123456789abcdef01234567"
    commands: list[tuple[list[str], Path | None]] = []

    def git_runner(args: list[str], cwd: Path | None) -> str:
        commands.append((args, cwd))
        if args[1:] == ["branch", "--show-current"]:
            return "main"
        if args[1:] == ["rev-parse", "HEAD"]:
            return revision
        return ""

    repositories = [
        {
            "repo_id": repo_id,
            "git_url": f"https://example.test/org/{repo_id}.git",
            "branch": "main",
            "source_revision": revision,
            "languages": ["java"],
        }
        for repo_id in ("repo-a", "repo-b", "repo-c")
    ]
    service = WorkspaceBuildApplicationService(lambda _: None, git_runner=git_runner)

    request = service.prepare(
        {"workspace_id": "workspace-1", "name": "Workspace", "repositories": repositories},
        tmp_path,
        "request-1",
        "generation-1",
    )

    assert commands[0][0][:7] == ["git", "clone", "--depth", "1", "--single-branch", "--no-tags", "--branch"]
    assert all("--recurse-submodules" not in args for args, _ in commands)
    assert all(snapshot.source.value.startswith("https://example.test/") for snapshot in request.snapshots)
    assert all(snapshot.source_revision == revision for snapshot in request.snapshots)
    assert all(path.exists() for path in request.owned_work_dirs)
    service.cleanup(request)
    assert all(not path.exists() for path in request.owned_work_dirs)


def test_run_cleans_cloned_directories_when_publisher_fails(tmp_path: Path) -> None:
    revision = "0123456789abcdef0123456789abcdef01234567"

    def git_runner(args: list[str], cwd: Path | None) -> str:
        if args[1:] == ["branch", "--show-current"]:
            return "main"
        if args[1:] == ["rev-parse", "HEAD"]:
            return revision
        return ""

    repositories = [
        {
            "repo_id": repo_id,
            "git_url": f"ssh://git@example.test/org/{repo_id}.git",
            "branch": "main",
            "source_revision": revision,
            "languages": ["java"],
        }
        for repo_id in ("repo-a", "repo-b", "repo-c")
    ]
    service = WorkspaceBuildApplicationService(
        lambda _: (_ for _ in ()).throw(RuntimeError("detector failure")), git_runner=git_runner
    )
    request = service.prepare(
        {"workspace_id": "workspace-1", "name": "Workspace", "repositories": repositories},
        tmp_path,
        "request-1",
        "generation-1",
    )
    owned_work_dirs = request.owned_work_dirs

    with pytest.raises(RuntimeError, match="detector failure"):
        service.run(request)

    assert all(not path.exists() for path in owned_work_dirs)
