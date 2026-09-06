from __future__ import annotations

import pytest

from ontoagent.domain.workspace_authorization import PrincipalIdentity, WorkspaceGrant
from ontoagent.store.neo4j_workspace_acl_repository import Neo4jWorkspaceAclRepository

pytestmark = pytest.mark.unit


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


def _repository(driver: _Driver) -> Neo4jWorkspaceAclRepository:
    repository = Neo4jWorkspaceAclRepository(driver)
    driver.calls.clear()
    return repository


def test_grant_write_and_read_are_workspace_scoped_and_parameterized() -> None:
    driver = _Driver(
        [
            [
                {
                    "principal_id": "alice",
                    "workspace_id": "workspace-1",
                    "scope": "filtered",
                    "repository_ids": ["repo-a"],
                }
            ],
            [
                {
                    "principal_id": "alice",
                    "workspace_id": "workspace-1",
                    "scope": "filtered",
                    "repository_ids": ["repo-a"],
                }
            ],
        ]
    )
    repository = _repository(driver)
    principal = PrincipalIdentity("alice")
    grant = WorkspaceGrant.filtered(principal, "workspace-1", ("repo-a",))

    assert repository.upsert_grant(grant) == grant
    assert repository.get_grant(principal, "workspace-1") == grant

    write_query, write_params = driver.calls[0]
    read_query, read_params = driver.calls[1]
    assert "OntoAgentWorkspaceAclGrant" in write_query
    assert "$principal_id" in write_query and "alice" not in write_query
    assert write_params == {
        "principal_id": "alice",
        "workspace_id": "workspace-1",
        "scope": "filtered",
        "repository_ids": ["repo-a"],
    }
    assert "workspaceId: $workspace_id" in read_query
    assert read_params == {"principal_id": "alice", "workspace_id": "workspace-1"}


def test_read_authorization_state_uses_only_workspace_acl_and_frozen_snapshot_labels() -> None:
    driver = _Driver(
        [
            [{"exists": True}],
            [{"generation_id": "generation-active"}],
            [
                {
                    "workspace_id": "workspace-1",
                    "generation_id": "generation-active",
                    "state": "active",
                    "repository_ids": ["repo-a", "repo-b"],
                }
            ],
        ]
    )
    repository = _repository(driver)

    assert repository.workspace_exists("workspace-1") is True
    assert repository.get_active_generation_id("workspace-1") == "generation-active"
    generation = repository.get_generation("workspace-1", "generation-active")

    assert generation is not None
    assert generation.repository_ids == ("repo-a", "repo-b")
    statement = " ".join(query for query, _ in driver.calls)
    assert "OntoAgentWorkspaceAclGrant" not in statement
    assert "OntoAgentWorkspaceRepositorySnapshot" in statement
    assert "OntoAgentServiceGraph" not in statement


def test_grant_read_rejects_malformed_persisted_acl_records() -> None:
    driver = _Driver(
        [
            [
                {
                    "principal_id": "alice",
                    "workspace_id": "workspace-1",
                    "scope": "filtered",
                    "repository_ids": ["repo-a", "repo-a"],
                }
            ]
        ]
    )
    repository = _repository(driver)

    with pytest.raises(ValueError, match="malformed persisted workspace ACL grant"):
        repository.get_grant(PrincipalIdentity("alice"), "workspace-1")
