"""Protocol-neutral contracts for authorizing workspace-scoped queries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class WorkspaceAuthorizationFailure(StrEnum):
    """Stable authorization outcomes for protocol adapters to map to responses."""

    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    CONFLICT = "conflict"


class WorkspaceGrantScope(StrEnum):
    """Whether a workspace grant exposes all or selected frozen repositories."""

    FULL = "full"
    FILTERED = "filtered"


@dataclass(frozen=True)
class PrincipalIdentity:
    """Authenticated caller identity independent of HTTP, CLI, or MCP transport."""

    principal_id: str

    def __post_init__(self) -> None:
        _require_nonblank(self.principal_id, "principal_id")


@dataclass(frozen=True)
class WorkspaceGrant:
    """A discoverable workspace grant with full or frozen-repository-filtered visibility."""

    principal: PrincipalIdentity
    workspace_id: str
    scope: WorkspaceGrantScope
    repository_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.principal) is not PrincipalIdentity:
            raise ValueError("principal must be a PrincipalIdentity")
        _require_nonblank(self.workspace_id, "workspace_id")
        if type(self.scope) is not WorkspaceGrantScope:
            raise ValueError("scope must be a WorkspaceGrantScope")
        _validate_repository_ids(self.repository_ids)
        if self.scope is WorkspaceGrantScope.FULL and self.repository_ids:
            raise ValueError("full grant must not specify repository_ids")
        if self.scope is WorkspaceGrantScope.FILTERED and not self.repository_ids:
            raise ValueError("filtered grant must specify repository_ids")
        object.__setattr__(self, "repository_ids", tuple(sorted(self.repository_ids)))

    @classmethod
    def full(cls, principal: PrincipalIdentity, workspace_id: str) -> WorkspaceGrant:
        return cls(principal, workspace_id, WorkspaceGrantScope.FULL)

    @classmethod
    def filtered(
        cls, principal: PrincipalIdentity, workspace_id: str, repository_ids: tuple[str, ...]
    ) -> WorkspaceGrant:
        return cls(principal, workspace_id, WorkspaceGrantScope.FILTERED, repository_ids)


@dataclass(frozen=True)
class WorkspaceAuthorizationGeneration:
    """Trusted persistence projection of a workspace generation and frozen repository IDs."""

    workspace_id: str
    generation_id: str
    state: str
    repository_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonblank(self.workspace_id, "workspace_id")
        _require_nonblank(self.generation_id, "generation_id")
        _require_nonblank(self.state, "state")
        _validate_repository_ids(self.repository_ids)
        if not self.repository_ids:
            raise ValueError("repository_ids must be non-empty")
        object.__setattr__(self, "repository_ids", tuple(sorted(self.repository_ids)))


@dataclass(frozen=True)
class AuthorizedRepositorySet:
    """Repository visibility derived from a trusted frozen workspace generation."""

    repo_ids: frozenset[str]
    is_full: bool

    def __post_init__(self) -> None:
        if type(self.repo_ids) is not frozenset or not self.repo_ids:
            raise ValueError("repo_ids must be a non-empty frozenset")
        if any(not isinstance(repo_id, str) or not repo_id.strip() for repo_id in self.repo_ids):
            raise ValueError("repo_ids must contain nonblank strings")
        if type(self.is_full) is not bool:
            raise ValueError("is_full must be a bool")


@dataclass(frozen=True)
class WorkspaceQueryAuthorization:
    """Authorization result passed to a future transport-specific query adapter."""

    workspace_id: str
    generation_id: str
    repositories: AuthorizedRepositorySet

    def __post_init__(self) -> None:
        _require_nonblank(self.workspace_id, "workspace_id")
        _require_nonblank(self.generation_id, "generation_id")
        if type(self.repositories) is not AuthorizedRepositorySet:
            raise ValueError("repositories must be an AuthorizedRepositorySet")


class WorkspaceAuthorizationRepository(Protocol):
    """Persistence reads required by workspace query authorization."""

    def workspace_exists(self, workspace_id: str) -> bool: ...

    def get_grant(self, principal: PrincipalIdentity, workspace_id: str) -> WorkspaceGrant | None: ...

    def get_active_generation_id(self, workspace_id: str) -> str | None: ...

    def get_generation(self, workspace_id: str, generation_id: str) -> WorkspaceAuthorizationGeneration | None: ...


def _validate_repository_ids(repository_ids: object) -> None:
    if type(repository_ids) is not tuple:
        raise ValueError("repository_ids must be a tuple")
    if any(not isinstance(repo_id, str) or not repo_id.strip() for repo_id in repository_ids):
        raise ValueError("repository_ids must contain nonblank strings")
    if len(set(repository_ids)) != len(repository_ids):
        raise ValueError("repository_ids must be unique")


def _require_nonblank(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonblank")
