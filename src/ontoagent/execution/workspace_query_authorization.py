"""Fail-closed workspace query authorization independent of transport adapters."""

from __future__ import annotations

from ontoagent.domain.workspace_authorization import (
    AuthorizedRepositorySet,
    PrincipalIdentity,
    WorkspaceAuthorizationFailure,
    WorkspaceAuthorizationRepository,
    WorkspaceGrantScope,
    WorkspaceQueryAuthorization,
)


class WorkspaceAuthorizationError(Exception):
    """A deliberate authorization outcome for a future HTTP, CLI, or MCP adapter."""

    def __init__(self, failure: WorkspaceAuthorizationFailure) -> None:
        self.failure = failure
        super().__init__(failure.value)


class WorkspaceQueryAuthorizationService:
    """Resolve the current trusted ACTIVE generation and its authorized frozen repositories."""

    def __init__(self, repository: WorkspaceAuthorizationRepository) -> None:
        self._repository = repository

    def authorize(
        self, principal: PrincipalIdentity, workspace_id: str, generation_id: str | None = None
    ) -> WorkspaceQueryAuthorization:
        """Authorize one workspace query without disclosing ungranted workspace metadata."""
        if type(principal) is not PrincipalIdentity:
            raise ValueError("principal must be a PrincipalIdentity")
        _require_nonblank(workspace_id, "workspace_id")
        if generation_id is not None:
            _require_nonblank(generation_id, "generation_id")

        grant = self._repository.get_grant(principal, workspace_id)
        if grant is None or grant.principal != principal or grant.workspace_id != workspace_id:
            raise WorkspaceAuthorizationError(WorkspaceAuthorizationFailure.FORBIDDEN)
        if not self._repository.workspace_exists(workspace_id):
            raise WorkspaceAuthorizationError(WorkspaceAuthorizationFailure.NOT_FOUND)

        active_generation_id = self._repository.get_active_generation_id(workspace_id)
        resolved_generation_id = generation_id or active_generation_id
        if active_generation_id is None or resolved_generation_id is None:
            raise WorkspaceAuthorizationError(WorkspaceAuthorizationFailure.CONFLICT)
        generation = self._repository.get_generation(workspace_id, resolved_generation_id)
        if (
            generation is None
            or generation.workspace_id != workspace_id
            or generation.generation_id != resolved_generation_id
            or generation.state != "active"
            or resolved_generation_id != active_generation_id
        ):
            raise WorkspaceAuthorizationError(WorkspaceAuthorizationFailure.CONFLICT)

        frozen_repo_ids = frozenset(generation.repository_ids)
        if grant.scope is WorkspaceGrantScope.FULL:
            repositories = AuthorizedRepositorySet(frozen_repo_ids, True)
        elif grant.scope is WorkspaceGrantScope.FILTERED:
            granted_repo_ids = frozenset(grant.repository_ids)
            if not granted_repo_ids.issubset(frozen_repo_ids):
                raise WorkspaceAuthorizationError(WorkspaceAuthorizationFailure.CONFLICT)
            repositories = AuthorizedRepositorySet(granted_repo_ids, False)
        else:
            raise WorkspaceAuthorizationError(WorkspaceAuthorizationFailure.FORBIDDEN)
        return WorkspaceQueryAuthorization(workspace_id, resolved_generation_id, repositories)


def _require_nonblank(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonblank")
