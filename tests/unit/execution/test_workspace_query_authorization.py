from __future__ import annotations

import pytest

from ontoagent.domain.workspace_authorization import (
    PrincipalIdentity,
    WorkspaceAuthorizationFailure,
    WorkspaceAuthorizationGeneration,
    WorkspaceGrant,
)
from ontoagent.execution.workspace_query_authorization import (
    WorkspaceAuthorizationError,
    WorkspaceQueryAuthorizationService,
)

pytestmark = pytest.mark.unit


class _Repository:
    def __init__(self) -> None:
        self.workspaces: set[str] = {"workspace-1", "workspace-2"}
        self.grants: dict[tuple[str, str], WorkspaceGrant] = {}
        self.active_generation_ids: dict[str, str] = {"workspace-1": "generation-active"}
        self.generations: dict[tuple[str, str], WorkspaceAuthorizationGeneration] = {
            ("workspace-1", "generation-active"): WorkspaceAuthorizationGeneration(
                "workspace-1", "generation-active", "active", ("repo-a", "repo-b")
            ),
            ("workspace-1", "generation-stale"): WorkspaceAuthorizationGeneration(
                "workspace-1", "generation-stale", "superseded", ("repo-a", "repo-c")
            ),
            ("workspace-1", "generation-untrusted"): WorkspaceAuthorizationGeneration(
                "workspace-1", "generation-untrusted", "active", ("repo-a", "repo-b")
            ),
        }

    def workspace_exists(self, workspace_id: str) -> bool:
        return workspace_id in self.workspaces

    def get_grant(self, principal: PrincipalIdentity, workspace_id: str) -> WorkspaceGrant | None:
        return self.grants.get((principal.principal_id, workspace_id))

    def get_active_generation_id(self, workspace_id: str) -> str | None:
        return self.active_generation_ids.get(workspace_id)

    def get_generation(self, workspace_id: str, generation_id: str) -> WorkspaceAuthorizationGeneration | None:
        return self.generations.get((workspace_id, generation_id))


@pytest.fixture
def repository() -> _Repository:
    return _Repository()


@pytest.fixture
def service(repository: _Repository) -> WorkspaceQueryAuthorizationService:
    return WorkspaceQueryAuthorizationService(repository)


def test_full_grant_resolves_current_active_generation_and_all_frozen_repositories(
    repository: _Repository, service: WorkspaceQueryAuthorizationService
) -> None:
    principal = PrincipalIdentity("alice")
    repository.grants[("alice", "workspace-1")] = WorkspaceGrant.full(principal, "workspace-1")

    decision = service.authorize(principal, "workspace-1")

    assert decision.workspace_id == "workspace-1"
    assert decision.generation_id == "generation-active"
    assert decision.repositories.is_full is True
    assert decision.repositories.repo_ids == frozenset({"repo-a", "repo-b"})


def test_filtered_grant_only_exposes_its_frozen_repository_subset(
    repository: _Repository, service: WorkspaceQueryAuthorizationService
) -> None:
    principal = PrincipalIdentity("alice")
    repository.grants[("alice", "workspace-1")] = WorkspaceGrant.filtered(principal, "workspace-1", ("repo-b",))

    decision = service.authorize(principal, "workspace-1")

    assert decision.repositories.is_full is False
    assert decision.repositories.repo_ids == frozenset({"repo-b"})


def test_no_grant_fails_closed_without_disclosing_existing_or_unknown_workspace(
    service: WorkspaceQueryAuthorizationService,
) -> None:
    principal = PrincipalIdentity("mallory")

    for workspace_id in ("workspace-1", "unknown-workspace"):
        with pytest.raises(WorkspaceAuthorizationError) as raised:
            service.authorize(principal, workspace_id)
        assert raised.value.failure is WorkspaceAuthorizationFailure.FORBIDDEN


def test_grant_allows_unknown_workspace_to_be_reported_as_not_found(
    repository: _Repository, service: WorkspaceQueryAuthorizationService
) -> None:
    principal = PrincipalIdentity("alice")
    repository.grants[("alice", "unknown-workspace")] = WorkspaceGrant.full(principal, "unknown-workspace")

    with pytest.raises(WorkspaceAuthorizationError) as raised:
        service.authorize(principal, "unknown-workspace")

    assert raised.value.failure is WorkspaceAuthorizationFailure.NOT_FOUND


def test_grant_cannot_bleed_into_another_workspace(
    repository: _Repository, service: WorkspaceQueryAuthorizationService
) -> None:
    principal = PrincipalIdentity("alice")
    repository.grants[("alice", "workspace-1")] = WorkspaceGrant.full(principal, "workspace-1")

    with pytest.raises(WorkspaceAuthorizationError) as raised:
        service.authorize(principal, "workspace-2")

    assert raised.value.failure is WorkspaceAuthorizationFailure.FORBIDDEN


def test_explicit_generation_must_be_active_and_match_the_trusted_active_binding(
    repository: _Repository, service: WorkspaceQueryAuthorizationService
) -> None:
    principal = PrincipalIdentity("alice")
    repository.grants[("alice", "workspace-1")] = WorkspaceGrant.full(principal, "workspace-1")

    assert service.authorize(principal, "workspace-1", "generation-active").generation_id == "generation-active"
    for generation_id in ("generation-stale", "generation-untrusted", "missing-generation"):
        with pytest.raises(WorkspaceAuthorizationError) as raised:
            service.authorize(principal, "workspace-1", generation_id)
        assert raised.value.failure is WorkspaceAuthorizationFailure.CONFLICT


def test_filtered_grant_cannot_authorize_repositories_absent_from_frozen_generation(
    repository: _Repository, service: WorkspaceQueryAuthorizationService
) -> None:
    principal = PrincipalIdentity("alice")
    repository.grants[("alice", "workspace-1")] = WorkspaceGrant.filtered(
        principal, "workspace-1", ("repo-a", "caller-invented-repo")
    )

    with pytest.raises(WorkspaceAuthorizationError) as raised:
        service.authorize(principal, "workspace-1")

    assert raised.value.failure is WorkspaceAuthorizationFailure.CONFLICT


def test_domain_contracts_reject_blank_duplicate_and_malformed_values() -> None:
    with pytest.raises(ValueError, match="principal_id"):
        PrincipalIdentity(" ")
    principal = PrincipalIdentity("alice")
    with pytest.raises(ValueError, match="workspace_id"):
        WorkspaceGrant.full(principal, " ")
    with pytest.raises(ValueError, match="unique"):
        WorkspaceGrant.filtered(principal, "workspace-1", ("repo-a", "repo-a"))
    with pytest.raises(ValueError, match="repository_ids"):
        WorkspaceAuthorizationGeneration("workspace-1", "generation-1", "active", ("repo-a", " "))
