from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ontoagent.domain.workspace_authorization import (
    AuthorizedRepositorySet,
    PrincipalIdentity,
    WorkspaceAuthorizationFailure,
    WorkspaceQueryAuthorization,
)
from ontoagent.execution.workspace_query_authorization import WorkspaceAuthorizationError
from ontoagent.execution.workspace_service_graph_query import (
    WorkspaceGraphPage,
    WorkspaceGraphQueryRequest,
    WorkspaceGraphQueryValidationError,
    WorkspaceServiceGraphQueryService,
    WorkspaceServiceGraphReadError,
)


class _Authorization:
    def __init__(self, decision: WorkspaceQueryAuthorization) -> None:
        self.decision = decision
        self.calls: list[tuple[PrincipalIdentity, str, str | None]] = []

    def authorize(
        self, principal: PrincipalIdentity, workspace_id: str, generation_id: str | None = None
    ) -> WorkspaceQueryAuthorization:
        self.calls.append((principal, workspace_id, generation_id))
        return self.decision


class _Repository:
    def __init__(self) -> None:
        self.calls = 0
        self.nodes = (
            {"id": "a", "node_type": "ServiceDefinition", "repo_id": "repo-a"},
            {"id": "b", "node_type": "ServiceDefinition", "repo_id": "repo-b"},
            {"id": "e-a", "node_type": "Evidence", "repo_id": "repo-a"},
            {"id": "e-b", "node_type": "Evidence", "repo_id": "repo-b"},
        )
        self.edges = (
            {
                "id": "a-b",
                "relation_type": "DEPENDS_ON",
                "source_id": "a",
                "target_id": "b",
                "repo_ids": ("repo-a", "repo-b"),
            },
            {
                "id": "a-e",
                "relation_type": "SUPPORTED_BY_EVIDENCE",
                "source_id": "a",
                "target_id": "e-a",
                "repo_ids": ("repo-a",),
            },
        )

    def read(
        self, authorization: WorkspaceQueryAuthorization
    ) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
        self.calls += 1
        return self.nodes, self.edges


def _service(full: bool = False) -> tuple[WorkspaceServiceGraphQueryService, _Authorization, _Repository]:
    repos = AuthorizedRepositorySet(frozenset({"repo-a", "repo-b"} if full else {"repo-a"}), full)
    auth = _Authorization(WorkspaceQueryAuthorization("workspace-1", "generation-1", repos))
    repository = _Repository()
    return WorkspaceServiceGraphQueryService(auth, repository, b"test-cursor-secret"), auth, repository


def test_partial_grant_filters_nodes_edges_and_evidence_after_authorization() -> None:
    service, authorization, repository = _service()

    result = service.service_directory(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"))

    assert authorization.calls == [(PrincipalIdentity("alice"), "workspace-1", None)]
    assert repository.calls == 1
    assert result.visibility == "filtered"
    assert {node["id"] for node in result.nodes} == {"a", "e-a"}
    assert {edge["id"] for edge in result.edges} == {"a-e"}
    assert not hasattr(result, "total")


def test_full_grant_retains_cross_repository_links_and_repo_filter_only_narrows() -> None:
    service, _, _ = _service(full=True)

    all_repos = service.service_directory(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"))
    narrowed = service.service_directory(
        PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1", repo_id="repo-a")
    )

    assert {edge["id"] for edge in all_repos.edges} == {"a-b", "a-e"}
    assert {node["id"] for node in narrowed.nodes} == {"a", "b", "e-a"}
    assert {edge["id"] for edge in narrowed.edges} == {"a-b", "a-e"}


def test_node_page_emits_cross_page_method_relation_from_its_source() -> None:
    service, _, repository = _service(full=True)
    repository.nodes = (
        {"id": "a-caller", "node_type": "ConsumerMethodCall", "repo_id": "repo-a"},
        {"id": "b-filler", "node_type": "ImplementationMethod", "repo_id": "repo-a"},
        {"id": "c-provider", "node_type": "ServiceOperation", "repo_id": "repo-b"},
        {"id": "d-evidence", "node_type": "MethodEvidence", "repo_id": "repo-b"},
    )
    repository.edges = (
        {
            "id": "caller-provider",
            "relation_type": "CALLS_OPERATION",
            "source_id": "a-caller",
            "target_id": "c-provider",
            "repo_ids": ("repo-a", "repo-b"),
        },
    )

    page = service.service_directory(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1", page_size=2))

    assert {node["id"] for node in page.nodes} == {"a-caller", "b-filler"}
    assert {edge["id"] for edge in page.edges} == {"caller-provider"}
    assert page.next_cursor is not None


def test_cursor_is_integrity_protected_and_bound_to_principal_and_generation() -> None:
    service, auth, _ = _service(full=True)
    first = service.service_directory(
        PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1", page_size=1)
    )
    assert isinstance(first, WorkspaceGraphPage) and first.next_cursor is not None
    with pytest.raises(WorkspaceGraphQueryValidationError):
        service.service_directory(
            PrincipalIdentity("alice"),
            WorkspaceGraphQueryRequest("workspace-1", page_size=1, cursor=first.next_cursor + "x"),
        )
    with pytest.raises(WorkspaceGraphQueryValidationError):
        service.service_directory(
            PrincipalIdentity("bob"), WorkspaceGraphQueryRequest("workspace-1", page_size=1, cursor=first.next_cursor)
        )
    auth.decision = WorkspaceQueryAuthorization(
        "workspace-1", "generation-2", AuthorizedRepositorySet(frozenset({"repo-a", "repo-b"}), True)
    )
    with pytest.raises(WorkspaceGraphQueryValidationError):
        service.service_directory(
            PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1", page_size=1, cursor=first.next_cursor)
        )


def test_untrusted_graph_read_is_a_conflict_not_a_backend_gate_leak() -> None:
    service, _, repository = _service(full=True)

    def rejected_read(
        _: WorkspaceQueryAuthorization,
    ) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
        raise WorkspaceServiceGraphReadError("receipt is untrusted")

    repository.read = rejected_read  # type: ignore[method-assign]

    with pytest.raises(WorkspaceAuthorizationError) as raised:
        service.service_directory(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"))

    assert raised.value.failure is WorkspaceAuthorizationFailure.CONFLICT


@pytest.mark.parametrize(
    "kwargs",
    [{"page_size": 0}, {"page_size": 101}, {"depth": 0}, {"depth": 9}, {"node_limit": 0}, {"node_limit": 1001}],
)
def test_request_bounds_are_typed_validation_errors(kwargs: dict[str, int]) -> None:
    with pytest.raises(WorkspaceGraphQueryValidationError):
        WorkspaceGraphQueryRequest("workspace-1", **kwargs)


def test_remote_filtered_phase_uses_bounded_single_page_queries() -> None:
    """Keep phase-2 physical filtering proof independent from pagination coverage."""
    integration_test = Path(__file__).parents[2] / "integration" / "test_workspace_service_graph_query.py"
    source = integration_test.read_text()
    tree = ast.parse(source)
    phase = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_workspace_graph_query_remote_filtered_and_ungranted_physical_filtering"
    )
    phase_source = ast.get_source_segment(source, phase)

    assert phase_source is not None
    assert "_all_service_directory_pages" not in phase_source
    assert "page_size=100" in phase_source
