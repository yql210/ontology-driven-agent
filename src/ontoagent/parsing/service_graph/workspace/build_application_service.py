from __future__ import annotations

import hashlib
import json
import subprocess
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

from .models import Workspace, WorkspaceRepositorySnapshot, WorkspaceSourceDescriptor, WorkspaceSourceKind
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


class WorkspaceBuildApplicationService:
    """Validate a local workspace manifest, freeze Git state, then publish one generation."""

    def __init__(
        self,
        publisher: WorkspacePublisher,
        *,
        id_factory: Callable[[], str] | None = None,
        closeable: Closable | None = None,
    ) -> None:
        self._publisher = publisher
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._closeable = closeable

    @classmethod
    def from_config(cls, config: OntoAgentConfig) -> WorkspaceBuildApplicationService:
        """Create the production service without any LLM or embedding dependencies."""
        driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password))
        registry = DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        orchestrator = WorkspaceServiceGraphPublishOrchestrator(
            Neo4jWorkspaceServiceGraphPublishComponentFactory(driver, registry)
        )
        return cls(orchestrator.publish, closeable=driver)

    @staticmethod
    def task_id_for(workspace_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"{workspace_id}\x00{idempotency_key}".encode()).hexdigest()
        return f"workspace-task-{digest}"

    def close(self) -> None:
        if self._closeable is not None:
            self._closeable.close()

    def build(self, manifest_path: Path) -> WorkspaceBuildResult:
        manifest = _load_manifest(manifest_path)
        workspace = Workspace(_required_string(manifest, "workspace_id"), _required_string(manifest, "name"))
        repositories = _repositories(manifest)
        task_idempotency_key = self._id_factory()
        generation_id = self._id_factory()
        snapshots, runtime_snapshots = _freeze_repositories(workspace.workspace_id, repositories, manifest_path.parent)
        request = WorkspaceServiceGraphPublishInput(
            workspace,
            snapshots,
            runtime_snapshots,
            task_idempotency_key,
            generation_id,
            _optional_string(manifest, "expected_active_generation_id"),
        )
        outcome = self._publisher(request)
        return WorkspaceBuildResult(
            self.task_id_for(workspace.workspace_id, task_idempotency_key), generation_id, outcome
        )


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
            item, {"repo_id", "path", "branch", "source_revision", "languages"}, f"repository {index}"
        )
        for field_name in ("repo_id", "path", "branch", "source_revision"):
            _required_string(item, field_name)
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
    workspace_id: str, repositories: tuple[Mapping[str, object], ...], manifest_dir: Path
) -> tuple[tuple[WorkspaceRepositorySnapshot, ...], tuple[RepositorySnapshot, ...]]:
    frozen: list[WorkspaceRepositorySnapshot] = []
    runtime: list[RepositorySnapshot] = []
    for repository in repositories:
        repo_id = _required_string(repository, "repo_id")
        root_path = _repository_path(_required_string(repository, "path"), manifest_dir, repo_id)
        branch = _required_string(repository, "branch")
        revision = _required_string(repository, "source_revision")
        actual_branch = _git_value(root_path, "branch", "--show-current")
        if actual_branch != branch:
            raise ValueError(f"repository {repo_id} branch mismatch: manifest={branch}, HEAD={actual_branch}")
        actual_revision = _git_value(root_path, "rev-parse", "HEAD")
        if actual_revision != revision:
            raise ValueError(f"repository {repo_id} revision mismatch: manifest={revision}, HEAD={actual_revision}")
        language_values = repository.get("languages")
        if not isinstance(language_values, list):
            raise ValueError(f"repository {repo_id} languages must be a list")
        languages = frozenset(value.strip().lower() for value in language_values if isinstance(value, str))
        frozen.append(
            WorkspaceRepositorySnapshot(
                workspace_id,
                repo_id,
                branch,
                actual_revision,
                WorkspaceSourceDescriptor(WorkspaceSourceKind.LOCAL, repo_id),
            )
        )
        runtime.append(RepositorySnapshot(repo_id, actual_revision, root_path, languages))
    return tuple(frozen), tuple(runtime)


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


def _git_value(root_path: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root_path, check=True, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"repository path is not a usable Git repository: {root_path}") from error
    value = completed.stdout.strip()
    if not value:
        raise ValueError(f"repository Git command returned an empty value: {root_path}")
    return value


def _required_string(mapping: Mapping[str, object], field_name: str) -> str:
    value = mapping.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonblank string")
    return value.strip()


def _optional_string(mapping: Mapping[str, object], field_name: str) -> str | None:
    if field_name not in mapping:
        return None
    return _required_string(mapping, field_name)


def _reject_unknown_fields(mapping: Mapping[str, object], allowed: set[str], name: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ValueError(f"{name} contains unsupported fields: {', '.join(sorted(unknown))}")
