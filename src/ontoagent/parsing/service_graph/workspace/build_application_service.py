from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from neo4j import GraphDatabase

from ontoagent.config import OntoAgentConfig
from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.models import RepositorySnapshot

from .models import (
    BuildTask,
    Workspace,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from .neo4j_repository import Neo4jWorkspaceRepository
from .publish_orchestrator import (
    Neo4jWorkspaceServiceGraphPublishComponentFactory,
    WorkspacePublishOutcome,
    WorkspaceServiceGraphPublishInput,
    WorkspaceServiceGraphPublishOrchestrator,
)


class WorkspacePublisher(Protocol):
    def __call__(self, request: WorkspaceServiceGraphPublishInput) -> WorkspacePublishOutcome: ...


class Closable(Protocol):
    def close(self) -> None: ...


class WorkspaceBuildRepository(Protocol):
    def create_workspace(self, workspace: Workspace) -> Workspace: ...

    def create_build_task(self, task: BuildTask) -> BuildTask: ...

    def get_build_task(self, task_id: str) -> BuildTask | None: ...

    def create_generation(self, generation: WorkspaceGeneration) -> WorkspaceGeneration: ...

    def get_generation(self, workspace_id: str, generation_id: str) -> WorkspaceGeneration | None: ...


@dataclass(frozen=True)
class WorkspaceBuildResult:
    task_id: str
    generation_id: str
    outcome: WorkspacePublishOutcome

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "generation_id": self.generation_id,
            "outcome": self.outcome.to_dict(),
        }


@dataclass(frozen=True)
class WorkspaceBuildSubmission:
    """A validated, durably accepted build request ready for asynchronous execution."""

    task_id: str
    workspace_id: str
    generation_id: str
    scheduled: bool
    request: WorkspaceServiceGraphPublishInput | None


@dataclass(frozen=True)
class WorkspaceBuildTaskStatus:
    task_id: str
    workspace_id: str
    generation_id: str
    state: WorkspaceGenerationState


class WorkspaceBuildApplicationService:
    """Validate a local workspace manifest, freeze Git state, then publish one generation."""

    def __init__(
        self,
        publisher: WorkspacePublisher,
        *,
        id_factory: Callable[[], str] | None = None,
        closeable: Closable | None = None,
        repository: WorkspaceBuildRepository | None = None,
        git_runner: Callable[[list[str], Path | None], str] | None = None,
    ) -> None:
        self._publisher = publisher
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._closeable = closeable
        self._repository = repository
        self._git_runner = git_runner or _run_git

    @classmethod
    def from_config(cls, config: OntoAgentConfig) -> WorkspaceBuildApplicationService:
        """Create the production service without any LLM or embedding dependencies."""
        driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password))
        registry = DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        repository = Neo4jWorkspaceRepository(driver)
        orchestrator = WorkspaceServiceGraphPublishOrchestrator(
            Neo4jWorkspaceServiceGraphPublishComponentFactory(driver, registry)
        )
        return cls(orchestrator.publish, closeable=driver, repository=repository)

    @staticmethod
    def task_id_for(workspace_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"{workspace_id}\x00{idempotency_key}".encode()).hexdigest()
        return f"workspace-task-{digest}"

    def close(self) -> None:
        if self._closeable is not None:
            self._closeable.close()

    def build(self, manifest_path: Path) -> WorkspaceBuildResult:
        manifest = _load_manifest(manifest_path)
        task_idempotency_key = self._id_factory()
        generation_id = self._id_factory()
        request = self.prepare(
            {key: value for key, value in manifest.items() if key != "expected_active_generation_id"},
            manifest_path.parent,
            task_idempotency_key,
            generation_id,
            _optional_string(manifest, "expected_active_generation_id"),
        )
        try:
            outcome = self._publisher(request)
            return WorkspaceBuildResult(
                self.task_id_for(request.workspace.workspace_id, task_idempotency_key), generation_id, outcome
            )
        finally:
            self.cleanup(request)

    def prepare(
        self,
        manifest: Mapping[str, object],
        manifest_dir: Path,
        idempotency_key: str,
        generation_id: str,
        expected_active_generation_id: str | None = None,
    ) -> WorkspaceServiceGraphPublishInput:
        """Validate and freeze a local-only manifest before any durable work is scheduled."""
        _reject_unknown_fields(manifest, {"workspace_id", "name", "repositories"}, "manifest")
        workspace = Workspace(_required_string(manifest, "workspace_id"), _required_string(manifest, "name"))
        repositories = _repositories(manifest)
        _require_nonblank(idempotency_key, "idempotency_key")
        _require_nonblank(generation_id, "generation_id")
        if expected_active_generation_id is not None:
            _require_nonblank(expected_active_generation_id, "expected_active_generation_id")
        snapshots, runtime_snapshots, owned_work_dirs = _freeze_repositories(
            workspace.workspace_id, repositories, manifest_dir, self._git_runner
        )
        return WorkspaceServiceGraphPublishInput(
            workspace,
            snapshots,
            runtime_snapshots,
            idempotency_key,
            generation_id,
            expected_active_generation_id,
            owned_work_dirs,
        )

    def submit(
        self,
        manifest: Mapping[str, object],
        manifest_dir: Path,
        idempotency_key: str,
        generation_id: str,
        expected_active_generation_id: str | None = None,
    ) -> WorkspaceBuildSubmission:
        """Persist a pending task only after the full local manifest preflight has succeeded."""
        if self._repository is None:
            raise RuntimeError("workspace build submission requires a workspace repository")
        request = self.prepare(manifest, manifest_dir, idempotency_key, generation_id, expected_active_generation_id)
        task_id = self.task_id_for(request.workspace.workspace_id, idempotency_key)
        existing = self._repository.get_build_task(task_id)
        if existing is not None:
            if existing.workspace_id != request.workspace.workspace_id or existing.generation_id != generation_id:
                self.cleanup(request)
                raise ValueError("idempotency_key is already bound to a different workspace generation")
            self.cleanup(request)
            return WorkspaceBuildSubmission(task_id, existing.workspace_id, existing.generation_id, False, None)
        try:
            self._repository.create_workspace(request.workspace)
            task = self._repository.create_build_task(
                BuildTask(task_id, request.workspace.workspace_id, idempotency_key, generation_id)
            )
            if task.generation_id != generation_id:
                raise ValueError("idempotency_key is already bound to a different workspace generation")
            self._repository.create_generation(
                WorkspaceGeneration(request.workspace.workspace_id, generation_id, request.snapshots)
            )
        except Exception:
            self.cleanup(request)
            raise
        return WorkspaceBuildSubmission(task.task_id, task.workspace_id, task.generation_id, True, request)

    def run(self, request: WorkspaceServiceGraphPublishInput) -> WorkspaceBuildResult:
        """Execute a submission previously accepted by :meth:`submit`."""
        try:
            outcome = self._publisher(request)
            return WorkspaceBuildResult(
                self.task_id_for(request.workspace.workspace_id, request.task_idempotency_key),
                request.generation_id,
                outcome,
            )
        finally:
            self.cleanup(request)

    def cleanup(self, request: WorkspaceServiceGraphPublishInput) -> None:
        """Remove work directories created while resolving Git URL sources."""
        for work_dir in request.owned_work_dirs:
            shutil.rmtree(work_dir, ignore_errors=True)

    def get_task_status(self, workspace_id: str, task_id: str) -> WorkspaceBuildTaskStatus | None:
        """Read a task's persisted generation state without generic graph infrastructure."""
        if self._repository is None:
            raise RuntimeError("workspace build task lookup requires a workspace repository")
        _require_nonblank(workspace_id, "workspace_id")
        _require_nonblank(task_id, "task_id")
        task = self._repository.get_build_task(task_id)
        if task is None:
            return None
        if task.workspace_id != workspace_id:
            raise ValueError("task does not belong to workspace")
        if task.generation_id is None:
            raise ValueError("task generation is missing")
        generation = self._repository.get_generation(workspace_id, task.generation_id)
        if generation is None:
            raise ValueError("task generation is missing")
        return WorkspaceBuildTaskStatus(task.task_id, workspace_id, generation.generation_id, generation.state)


def create_workspace_build_service(config: OntoAgentConfig) -> WorkspaceBuildApplicationService:
    """CLI composition root for local workspace graph publication."""
    return WorkspaceBuildApplicationService.from_config(config)


def _load_manifest(manifest_path: Path) -> Mapping[str, object]:
    if not manifest_path.is_file():
        raise ValueError("manifest path must be a readable JSON file")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON manifest: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    _reject_unknown_fields(data, {"workspace_id", "name", "repositories", "expected_active_generation_id"}, "manifest")
    return data


def _repositories(manifest: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    value = manifest.get("repositories")
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError("manifest repositories must list at least three repositories")
    repositories: list[Mapping[str, object]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"repository {index} must be an object")
        _reject_unknown_fields(
            item, {"repo_id", "path", "git_url", "branch", "source_revision", "languages"}, f"repository {index}"
        )
        for field_name in ("repo_id", "branch", "source_revision"):
            _required_string(item, field_name)
        has_path = "path" in item
        has_git_url = "git_url" in item
        if has_path == has_git_url:
            raise ValueError(f"repository {index} must declare exactly one of path or git_url")
        if has_path:
            _required_string(item, "path")
        else:
            _validate_git_url(_required_string(item, "git_url"))
        languages = item.get("languages")
        if (
            not isinstance(languages, list)
            or not languages
            or any(not isinstance(value, str) or not value.strip() for value in languages)
        ):
            raise ValueError(f"repository {index} languages must be a non-empty list of strings")
        repositories.append(item)
    if len({item["repo_id"] for item in repositories}) != len(repositories):
        raise ValueError("manifest repositories contain duplicate repo_id values")
    return tuple(repositories)


def _freeze_repositories(
    workspace_id: str,
    repositories: tuple[Mapping[str, object], ...],
    manifest_dir: Path,
    git_runner: Callable[[list[str], Path | None], str],
) -> tuple[tuple[WorkspaceRepositorySnapshot, ...], tuple[RepositorySnapshot, ...], tuple[Path, ...]]:
    frozen: list[WorkspaceRepositorySnapshot] = []
    runtime: list[RepositorySnapshot] = []
    owned_work_dirs: list[Path] = []
    try:
        for repository in repositories:
            repo_id = _required_string(repository, "repo_id")
            branch = _required_string(repository, "branch")
            revision = _required_string(repository, "source_revision")
            if "path" in repository:
                root_path = _repository_path(_required_string(repository, "path"), manifest_dir, repo_id)
                source = WorkspaceSourceDescriptor(WorkspaceSourceKind.LOCAL, repo_id)
            else:
                git_url = _required_string(repository, "git_url")
                work_dir = Path(tempfile.mkdtemp(prefix="ontoagent-workspace-"))
                owned_work_dirs.append(work_dir)
                root_path = work_dir / repo_id
                git_runner(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "--single-branch",
                        "--no-tags",
                        "--branch",
                        branch,
                        git_url,
                        str(root_path),
                    ],
                    None,
                )
                source = WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, git_url)
            actual_branch = _git_value(root_path, "branch", "--show-current", git_runner=git_runner)
            if actual_branch != branch:
                raise ValueError(f"repository {repo_id} branch mismatch: manifest={branch}, HEAD={actual_branch}")
            actual_revision = _git_value(root_path, "rev-parse", "HEAD", git_runner=git_runner)
            if actual_revision != revision:
                raise ValueError(f"repository {repo_id} revision mismatch: manifest={revision}, HEAD={actual_revision}")
            language_values = repository.get("languages")
            if not isinstance(language_values, list):
                raise ValueError(f"repository {repo_id} languages must be a list")
            languages = frozenset(value.strip().lower() for value in language_values if isinstance(value, str))
            frozen.append(WorkspaceRepositorySnapshot(workspace_id, repo_id, branch, actual_revision, source))
            runtime.append(RepositorySnapshot(repo_id, actual_revision, root_path, languages))
    except Exception:
        for work_dir in owned_work_dirs:
            shutil.rmtree(work_dir, ignore_errors=True)
        raise
    return tuple(frozen), tuple(runtime), tuple(owned_work_dirs)


def _repository_path(value: str, manifest_dir: Path, repo_id: str) -> Path:
    root_path = Path(value)
    if not root_path.is_absolute():
        root_path = manifest_dir / root_path
    root_path = root_path.resolve()
    if not root_path.is_dir():
        raise ValueError(f"repository {repo_id} path is not a directory: {root_path}")
    if not (root_path / ".git").exists():
        raise ValueError(f"repository {repo_id} path is not a Git repository: {root_path}")
    return root_path


def _git_value(root_path: Path, *args: str, git_runner: Callable[[list[str], Path | None], str] | None = None) -> str:
    try:
        value = (git_runner or _run_git)(["git", *args], root_path).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"repository path is not a usable Git repository: {root_path}") from error
    if not value:
        raise ValueError(f"repository Git command returned an empty value: {root_path}")
    return value


def _run_git(args: list[str], cwd: Path | None) -> str:
    completed = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, timeout=10)
    return completed.stdout


def _validate_git_url(value: str) -> None:
    try:
        WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, value)
    except ValueError as error:
        raise ValueError(f"git_url is unsafe: {error}") from error


def _required_string(mapping: Mapping[str, object], field_name: str) -> str:
    value = mapping.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonblank string")
    return value.strip()


def _require_nonblank(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonblank")


def _optional_string(mapping: Mapping[str, object], field_name: str) -> str | None:
    if field_name not in mapping:
        return None
    return _required_string(mapping, field_name)


def _reject_unknown_fields(mapping: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ValueError(f"{name} contains unsupported fields: {', '.join(sorted(unknown))}")
