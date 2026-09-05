from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import ip_address
from pathlib import PurePosixPath
from urllib.parse import urlparse


class WorkspaceSourceKind(StrEnum):
    LOCAL = "local"
    GIT = "git"


class WorkspaceGenerationState(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    RESOLVING = "resolving"
    WRITING = "writing"
    VERIFYING = "verifying"
    ACTIVE = "active"
    FAILED = "failed"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"


class WorkspacePublishStatus(StrEnum):
    PUBLISHED = "published"
    STALE_ACTIVE = "stale_active"
    CANDIDATE_NOT_READY = "candidate_not_ready"
    CANDIDATE_INVALID = "candidate_invalid"


_TRANSITIONS: dict[WorkspaceGenerationState, frozenset[WorkspaceGenerationState]] = {
    WorkspaceGenerationState.PENDING: frozenset(
        {WorkspaceGenerationState.EXTRACTING, WorkspaceGenerationState.FAILED, WorkspaceGenerationState.BLOCKED}
    ),
    WorkspaceGenerationState.EXTRACTING: frozenset(
        {WorkspaceGenerationState.RESOLVING, WorkspaceGenerationState.FAILED, WorkspaceGenerationState.BLOCKED}
    ),
    WorkspaceGenerationState.RESOLVING: frozenset(
        {WorkspaceGenerationState.WRITING, WorkspaceGenerationState.FAILED, WorkspaceGenerationState.BLOCKED}
    ),
    WorkspaceGenerationState.WRITING: frozenset(
        {WorkspaceGenerationState.VERIFYING, WorkspaceGenerationState.FAILED, WorkspaceGenerationState.BLOCKED}
    ),
    WorkspaceGenerationState.VERIFYING: frozenset(
        {WorkspaceGenerationState.ACTIVE, WorkspaceGenerationState.FAILED, WorkspaceGenerationState.BLOCKED}
    ),
    WorkspaceGenerationState.ACTIVE: frozenset({WorkspaceGenerationState.SUPERSEDED}),
    WorkspaceGenerationState.FAILED: frozenset(),
    WorkspaceGenerationState.BLOCKED: frozenset(),
    WorkspaceGenerationState.SUPERSEDED: frozenset(),
}


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    name: str

    def __post_init__(self) -> None:
        _require_nonblank(self.workspace_id, "workspace_id")
        _require_nonblank(self.name, "name")


@dataclass(frozen=True)
class WorkspaceSourceDescriptor:
    """A source reference suitable for Neo4j persistence, never a machine-local absolute path."""

    kind: WorkspaceSourceKind
    value: str

    def __post_init__(self) -> None:
        if type(self.kind) is not WorkspaceSourceKind:
            raise ValueError("kind must be a WorkspaceSourceKind")
        _require_nonblank(self.value, "source descriptor")
        if self.kind is WorkspaceSourceKind.LOCAL:
            path = PurePosixPath(self.value)
            if path.is_absolute() or ".." in path.parts or str(path) in {".", ""}:
                raise ValueError("local source descriptor must be a relative path")
            return
        parsed = urlparse(self.value)
        if parsed.scheme not in {"https", "ssh"} or not parsed.netloc or not parsed.hostname or not parsed.path:
            raise ValueError("git source descriptor must be an absolute https or ssh URL")
        if parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError("git source descriptor must not contain credentials, query, or fragment")
        if parsed.scheme == "https" and parsed.username is not None:
            raise ValueError("git source descriptor must not contain credentials, query, or fragment")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("git source descriptor must not contain a nonstandard transport") from error
        if port not in {None, 443 if parsed.scheme == "https" else 22}:
            raise ValueError("git source descriptor must not contain a nonstandard transport")
        if parsed.hostname == "localhost":
            raise ValueError("git source descriptor must not refer to a local host")
        try:
            if ip_address(parsed.hostname).is_loopback:
                raise ValueError("git source descriptor must not refer to a local host")
        except ValueError as error:
            if str(error).startswith("git source descriptor"):
                raise


@dataclass(frozen=True)
class WorkspaceRepositorySnapshot:
    workspace_id: str
    repo_id: str
    branch: str
    source_revision: str
    source: WorkspaceSourceDescriptor

    def __post_init__(self) -> None:
        for field_name in ("workspace_id", "repo_id", "branch", "source_revision"):
            _require_nonblank(getattr(self, field_name), field_name)
        if type(self.source) is not WorkspaceSourceDescriptor:
            raise ValueError("source must be a WorkspaceSourceDescriptor")


@dataclass(frozen=True)
class BuildTask:
    task_id: str
    workspace_id: str
    idempotency_key: str
    generation_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("task_id", "workspace_id", "idempotency_key"):
            _require_nonblank(getattr(self, field_name), field_name)
        if self.generation_id is not None:
            _require_nonblank(self.generation_id, "generation_id")


@dataclass(frozen=True)
class WorkspaceGeneration:
    workspace_id: str
    generation_id: str
    snapshots: tuple[WorkspaceRepositorySnapshot, ...]
    state: WorkspaceGenerationState = WorkspaceGenerationState.PENDING

    def __post_init__(self) -> None:
        _require_nonblank(self.workspace_id, "workspace_id")
        _require_nonblank(self.generation_id, "generation_id")
        if type(self.snapshots) is not tuple or not self.snapshots:
            raise ValueError("snapshots must be a non-empty tuple")
        if any(type(snapshot) is not WorkspaceRepositorySnapshot for snapshot in self.snapshots):
            raise ValueError("snapshots must contain WorkspaceRepositorySnapshot values")
        if any(snapshot.workspace_id != self.workspace_id for snapshot in self.snapshots):
            raise ValueError("snapshot workspace_id mismatch")
        if len({snapshot.repo_id for snapshot in self.snapshots}) != len(self.snapshots):
            raise ValueError("snapshots must have unique repo_id values")
        if type(self.state) is not WorkspaceGenerationState:
            raise ValueError("state must be a WorkspaceGenerationState")
        object.__setattr__(self, "snapshots", tuple(sorted(self.snapshots, key=lambda snapshot: snapshot.repo_id)))

    def transition_to(self, target: WorkspaceGenerationState) -> WorkspaceGeneration:
        if type(target) is not WorkspaceGenerationState or target not in _TRANSITIONS[self.state]:
            raise ValueError("invalid workspace generation state transition")
        return WorkspaceGeneration(self.workspace_id, self.generation_id, self.snapshots, target)


@dataclass(frozen=True)
class WorkspaceActiveBinding:
    workspace_id: str
    generation_id: str

    def __post_init__(self) -> None:
        _require_nonblank(self.workspace_id, "workspace_id")
        _require_nonblank(self.generation_id, "generation_id")


@dataclass(frozen=True)
class WorkspacePublishResult:
    status: WorkspacePublishStatus
    active_generation_id: str | None

    def __post_init__(self) -> None:
        if type(self.status) is not WorkspacePublishStatus:
            raise ValueError("status must be a WorkspacePublishStatus")
        if self.active_generation_id is not None:
            _require_nonblank(self.active_generation_id, "active_generation_id")


def _require_nonblank(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonblank")
