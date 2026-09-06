"""Dedicated Neo4j persistence for workspace query ACL grants and trusted snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ontoagent.domain.workspace_authorization import (
    PrincipalIdentity,
    WorkspaceAuthorizationGeneration,
    WorkspaceGrant,
    WorkspaceGrantScope,
)


class Neo4jDriver(Protocol):
    def session(self) -> object: ...


class Neo4jWorkspaceAclRepository:
    """Read/write workspace ACL records without querying service graph or endpoint metadata."""

    GRANT_LABEL = "OntoAgentWorkspaceAclGrant"
    ENSURE_CONSTRAINTS = (
        "CREATE CONSTRAINT ontoagent_workspace_acl_grant_identity IF NOT EXISTS "
        "FOR (n:OntoAgentWorkspaceAclGrant) REQUIRE (n.principalId, n.workspaceId) IS UNIQUE",
    )
    UPSERT_GRANT_QUERY = (
        "MERGE (grant:OntoAgentWorkspaceAclGrant {principalId: $principal_id, workspaceId: $workspace_id}) "
        "SET grant.scope = $scope, grant.repositoryIds = $repository_ids "
        "RETURN grant.principalId AS principal_id, grant.workspaceId AS workspace_id, grant.scope AS scope, "
        "grant.repositoryIds AS repository_ids"
    )
    GET_GRANT_QUERY = (
        "MATCH (grant:OntoAgentWorkspaceAclGrant {principalId: $principal_id, workspaceId: $workspace_id}) "
        "RETURN grant.principalId AS principal_id, grant.workspaceId AS workspace_id, grant.scope AS scope, "
        "grant.repositoryIds AS repository_ids"
    )
    WORKSPACE_EXISTS_QUERY = (
        "MATCH (workspace:OntoAgentWorkspace {workspaceId: $workspace_id}) RETURN count(workspace) > 0 AS exists"
    )
    GET_ACTIVE_GENERATION_ID_QUERY = (
        "MATCH (binding:OntoAgentWorkspaceActiveBinding {workspaceId: $workspace_id}) "
        "RETURN binding.generationId AS generation_id"
    )
    GET_GENERATION_QUERY = (
        "MATCH (generation:OntoAgentWorkspaceGeneration {workspaceId: $workspace_id, generationId: $generation_id}) "
        "MATCH (generation)-[:HAS_FROZEN_SNAPSHOT]->(snapshot:OntoAgentWorkspaceRepositorySnapshot) "
        "WITH generation, snapshot ORDER BY snapshot.repoId "
        "RETURN generation.workspaceId AS workspace_id, generation.generationId AS generation_id, generation.state AS state, "
        "collect(snapshot.repoId) AS repository_ids"
    )

    def __init__(self, driver: Neo4jDriver) -> None:
        self._driver = driver
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._driver.session() as session:  # type: ignore[union-attr]
            for query in self.ENSURE_CONSTRAINTS:
                session.run(query)  # type: ignore[union-attr]

    def upsert_grant(self, grant: WorkspaceGrant) -> WorkspaceGrant:
        if type(grant) is not WorkspaceGrant:
            raise ValueError("grant must be a WorkspaceGrant")
        row = self._one(
            self.UPSERT_GRANT_QUERY,
            {
                "principal_id": grant.principal.principal_id,
                "workspace_id": grant.workspace_id,
                "scope": grant.scope.value,
                "repository_ids": list(grant.repository_ids),
            },
        )
        return _grant_from_row(row)

    def get_grant(self, principal: PrincipalIdentity, workspace_id: str) -> WorkspaceGrant | None:
        if type(principal) is not PrincipalIdentity:
            raise ValueError("principal must be a PrincipalIdentity")
        _require_nonblank(workspace_id, "workspace_id")
        row = self._optional(
            self.GET_GRANT_QUERY, {"principal_id": principal.principal_id, "workspace_id": workspace_id}
        )
        return None if row is None else _grant_from_row(row)

    def workspace_exists(self, workspace_id: str) -> bool:
        _require_nonblank(workspace_id, "workspace_id")
        row = self._optional(self.WORKSPACE_EXISTS_QUERY, {"workspace_id": workspace_id})
        exists = None if row is None else row.get("exists")
        if type(exists) is not bool:
            raise ValueError("malformed persisted workspace existence")
        return exists

    def get_active_generation_id(self, workspace_id: str) -> str | None:
        _require_nonblank(workspace_id, "workspace_id")
        row = self._optional(self.GET_ACTIVE_GENERATION_ID_QUERY, {"workspace_id": workspace_id})
        if row is None:
            return None
        return _string(row, "generation_id")

    def get_generation(self, workspace_id: str, generation_id: str) -> WorkspaceAuthorizationGeneration | None:
        _require_nonblank(workspace_id, "workspace_id")
        _require_nonblank(generation_id, "generation_id")
        row = self._optional(self.GET_GENERATION_QUERY, {"workspace_id": workspace_id, "generation_id": generation_id})
        if row is None:
            return None
        repository_ids = row.get("repository_ids")
        if not isinstance(repository_ids, list):
            raise ValueError("malformed persisted workspace generation repository_ids")
        return WorkspaceAuthorizationGeneration(
            _string(row, "workspace_id"),
            _string(row, "generation_id"),
            _string(row, "state"),
            tuple(repository_ids),
        )

    def _one(self, query: str, params: dict[str, object]) -> Mapping[str, object]:
        row = self._optional(query, params)
        if row is None:
            raise ValueError("workspace ACL persistence rejected grant")
        return row

    def _optional(self, query: str, params: dict[str, object]) -> Mapping[str, object] | None:
        with self._driver.session() as session:  # type: ignore[union-attr]
            rows = list(session.run(query, **params))  # type: ignore[union-attr]
        return None if not rows else _mapping(rows[0])


def _grant_from_row(row: Mapping[str, object]) -> WorkspaceGrant:
    repository_ids = row.get("repository_ids")
    if not isinstance(repository_ids, list):
        raise ValueError("malformed persisted workspace ACL repository_ids")
    principal = PrincipalIdentity(_string(row, "principal_id"))
    try:
        return WorkspaceGrant(
            principal, _string(row, "workspace_id"), WorkspaceGrantScope(_string(row, "scope")), tuple(repository_ids)
        )
    except ValueError as error:
        raise ValueError("malformed persisted workspace ACL grant") from error


def _mapping(row: object) -> Mapping[str, object]:
    if isinstance(row, Mapping):
        return row
    try:
        return dict(row)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError("Neo4j row is not mapping-like") from error


def _string(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be nonblank")
    return value


def _require_nonblank(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be nonblank")
