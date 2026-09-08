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


def test_filtered_page_removes_hidden_node_references_but_full_page_preserves_them() -> None:
    filtered_service, _, filtered_repository = _service()
    filtered_repository.nodes = (
        {
            "id": "consumer",
            "node_type": "ConsumerMethodCall",
            "repo_id": "repo-a",
            "provider_operation_id": "provider",
            "providerOperationId": "provider",
            "target_reference": "provider",
            "targetReference": ["provider", "consumer"],
            "canonical_key": "orders",
        },
        {"id": "provider", "node_type": "ServiceOperation", "repo_id": "repo-b"},
    )
    filtered_repository.edges = ()

    filtered = filtered_service.service_directory(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"))
    consumer = next(node for node in filtered.nodes if node["id"] == "consumer")
    assert all(key not in consumer for key in ("provider_operation_id", "providerOperationId", "target_reference"))
    assert consumer["targetReference"] == ["consumer"]
    assert consumer["canonical_key"] == "orders"

    full_service, _, full_repository = _service(full=True)
    full_repository.nodes = filtered_repository.nodes
    full_repository.edges = ()
    full = full_service.service_directory(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"))
    full_consumer = next(node for node in full.nodes if node["id"] == "consumer")
    assert full_consumer["provider_operation_id"] == "provider"
    assert full_consumer["targetReference"] == ["provider", "consumer"]


def test_endpoint_methods_returns_closed_visible_subgraph_and_empty_for_hidden_or_invalid_endpoint() -> None:
    service, _, repository = _service(full=True)
    repository.nodes = (
        {"id": "endpoint-a", "node_type": "Endpoint", "repo_id": "repo-a"},
        {
            "id": "method-a",
            "node_type": "ImplementationMethod",
            "repo_id": "repo-a",
            "endpoint_id": "endpoint-a",
            "factPayload": "secret",
        },
        {"id": "evidence-a", "node_type": "MethodEvidence", "repo_id": "repo-a"},
        {"id": "unrelated", "node_type": "ImplementationMethod", "repo_id": "repo-a"},
    )
    repository.edges = (
        {
            "id": "method-evidence",
            "relation_type": "METHOD_EVIDENCE",
            "source_id": "method-a",
            "target_id": "evidence-a",
            "repo_ids": ("repo-a",),
        },
        {
            "id": "unrelated-edge",
            "relation_type": "CALLER_METHOD",
            "source_id": "unrelated",
            "target_id": "method-a",
            "repo_ids": ("repo-a",),
        },
    )
    page = service.endpoint_methods(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"), "endpoint-a")
    assert {node["id"] for node in page.nodes} == {"endpoint-a", "method-a", "evidence-a"}
    assert {edge["id"] for edge in page.edges} == {"method-evidence"}
    assert all("factPayload" not in node for node in page.nodes)
    assert all({edge["source_id"], edge["target_id"]} <= {node["id"] for node in page.nodes} for edge in page.edges)
    assert (
        service.endpoint_methods(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"), "missing").nodes
        == ()
    )


def test_endpoint_methods_filtered_grant_excludes_provider_records() -> None:
    service, _, repository = _service()
    repository.nodes = (
        {"id": "endpoint-a", "node_type": "Endpoint", "repo_id": "repo-a"},
        {"id": "method-a", "node_type": "ImplementationMethod", "repo_id": "repo-a", "endpoint_id": "endpoint-a"},
        {"id": "endpoint-b", "node_type": "Endpoint", "repo_id": "repo-b"},
        {"id": "method-b", "node_type": "ImplementationMethod", "repo_id": "repo-b", "endpoint_id": "endpoint-b"},
    )
    repository.edges = ()
    assert {
        node["id"]
        for node in service.endpoint_methods(
            PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"), "endpoint-a"
        ).nodes
    } == {"endpoint-a", "method-a"}
    assert (
        service.endpoint_methods(
            PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"), "endpoint-b"
        ).nodes
        == ()
    )


def test_endpoint_methods_without_grant_fails_closed() -> None:
    class _Denied:
        def authorize(self, principal: PrincipalIdentity, workspace_id: str, generation_id: str | None = None):
            raise WorkspaceAuthorizationError(WorkspaceAuthorizationFailure.FORBIDDEN)

    service = WorkspaceServiceGraphQueryService(_Denied(), _Repository(), b"test-cursor-secret")
    with pytest.raises(WorkspaceAuthorizationError):
        service.endpoint_methods(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"), "endpoint-a")


def test_full_grant_retains_cross_repository_links_and_repo_filter_only_narrows() -> None:
    service, _, _ = _service(full=True)

    all_repos = service.service_directory(PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1"))
    narrowed = service.service_directory(
        PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1", repo_id="repo-a")
    )

    assert {edge["id"] for edge in all_repos.edges} == {"a-b", "a-e"}
    assert {node["id"] for node in narrowed.nodes} == {"a", "b", "e-a"}
    assert {edge["id"] for edge in narrowed.edges} == {"a-b", "a-e"}


def test_node_page_closes_edges_over_endpoint_nodes() -> None:
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

    assert {node["id"] for node in page.nodes} == {"a-caller", "b-filler", "c-provider"}
    assert {edge["id"] for edge in page.edges} == {"caller-provider"}
    assert all({edge["source_id"], edge["target_id"]} <= {node["id"] for node in page.nodes} for edge in page.edges)
    assert page.next_cursor is not None


def test_dependencies_and_impact_traverse_visible_graph_at_requested_depth() -> None:
    service, _, repository = _service(full=True)
    repository.nodes = tuple(
        {"id": node_id, "node_type": "ServiceDefinition", "repo_id": "repo-a"} for node_id in ("a", "b", "c", "d")
    )
    repository.edges = (
        {"id": "a-b", "relation_type": "DEPENDS_ON", "source_id": "a", "target_id": "b", "repo_ids": ("repo-a",)},
        {"id": "b-c", "relation_type": "DEPENDS_ON", "source_id": "b", "target_id": "c", "repo_ids": ("repo-a",)},
        {"id": "c-d", "relation_type": "DEPENDS_ON", "source_id": "c", "target_id": "d", "repo_ids": ("repo-a",)},
    )
    principal = PrincipalIdentity("alice")

    depth_one = service.dependencies(principal, WorkspaceGraphQueryRequest("workspace-1", depth=1), "a")
    depth_two = service.dependencies(principal, WorkspaceGraphQueryRequest("workspace-1", depth=2), "a")
    depth_three = service.dependencies(principal, WorkspaceGraphQueryRequest("workspace-1", depth=3), "a")
    impact = service.impact(principal, WorkspaceGraphQueryRequest("workspace-1", depth=2), "d")

    assert {node["id"] for node in depth_one.nodes} == {"a", "b"}
    assert {node["id"] for node in depth_two.nodes} == {"a", "b", "c"}
    assert {node["id"] for node in depth_three.nodes} == {"a", "b", "c", "d"}
    assert {node["id"] for node in impact.nodes} == {"b", "c", "d"}


def test_dependency_traversal_terminates_cycles_prunes_acl_and_respects_node_limit() -> None:
    service, _, repository = _service()
    repository.nodes = (
        {"id": "a", "node_type": "ServiceDefinition", "repo_id": "repo-a"},
        {"id": "b", "node_type": "ServiceDefinition", "repo_id": "repo-a"},
        {"id": "c", "node_type": "ServiceDefinition", "repo_id": "repo-a"},
        {"id": "hidden", "node_type": "ServiceDefinition", "repo_id": "repo-b"},
    )
    repository.edges = (
        {"id": "a-b", "relation_type": "DEPENDS_ON", "source_id": "a", "target_id": "b", "repo_ids": ("repo-a",)},
        {"id": "b-c", "relation_type": "DEPENDS_ON", "source_id": "b", "target_id": "c", "repo_ids": ("repo-a",)},
        {"id": "c-a", "relation_type": "DEPENDS_ON", "source_id": "c", "target_id": "a", "repo_ids": ("repo-a",)},
        {
            "id": "b-hidden",
            "relation_type": "DEPENDS_ON",
            "source_id": "b",
            "target_id": "hidden",
            "repo_ids": ("repo-a", "repo-b"),
        },
    )

    result = service.dependencies(
        PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1", depth=8, node_limit=2), "a"
    )

    assert {node["id"] for node in result.nodes} == {"a", "b"}
    assert {edge["id"] for edge in result.edges} == {"a-b"}


def test_closed_pagination_advances_primary_nodes_without_loss_or_repeat() -> None:
    service, _, repository = _service(full=True)
    repository.nodes = tuple(
        {"id": node_id, "node_type": "ServiceDefinition", "repo_id": "repo-a"} for node_id in ("a", "b", "c", "d")
    )
    repository.edges = (
        {"id": "a-d", "relation_type": "DEPENDS_ON", "source_id": "a", "target_id": "d", "repo_ids": ("repo-a",)},
        {"id": "b-c", "relation_type": "DEPENDS_ON", "source_id": "b", "target_id": "c", "repo_ids": ("repo-a",)},
    )
    request = WorkspaceGraphQueryRequest("workspace-1", page_size=1)

    first = service.service_directory(PrincipalIdentity("alice"), request)
    second = service.service_directory(
        PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1", page_size=1, cursor=first.next_cursor)
    )
    third = service.service_directory(
        PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1", page_size=1, cursor=second.next_cursor)
    )
    fourth = service.service_directory(
        PrincipalIdentity("alice"), WorkspaceGraphQueryRequest("workspace-1", page_size=1, cursor=third.next_cursor)
    )

    assert [{node["id"] for node in page.nodes} for page in (first, second, third, fourth)] == [
        {"a", "d"},
        {"b", "c"},
        {"c"},
        {"d"},
    ]
    assert [tuple(edge["id"] for edge in page.edges) for page in (first, second, third, fourth)] == [
        ("a-d",),
        ("b-c",),
        (),
        (),
    ]
    assert all(
        {edge["source_id"], edge["target_id"]} <= {node["id"] for node in page.nodes}
        for page in (first, second, third, fourth)
        for edge in page.edges
    )


def test_cursor_rejects_changed_depth_node_limit_and_operation() -> None:
    service, _, repository = _service(full=True)
    repository.nodes = (
        {"id": "a", "node_type": "ServiceDefinition", "repo_id": "repo-a"},
        {"id": "b", "node_type": "ServiceDefinition", "repo_id": "repo-a"},
    )
    repository.edges = (
        {"id": "a-b", "relation_type": "DEPENDS_ON", "source_id": "a", "target_id": "b", "repo_ids": ("repo-a",)},
    )
    principal = PrincipalIdentity("alice")
    first = service.dependencies(principal, WorkspaceGraphQueryRequest("workspace-1", page_size=1, depth=1), "a")
    assert first.next_cursor is not None

    with pytest.raises(WorkspaceGraphQueryValidationError):
        service.dependencies(
            principal, WorkspaceGraphQueryRequest("workspace-1", page_size=1, depth=2, cursor=first.next_cursor), "a"
        )
    with pytest.raises(WorkspaceGraphQueryValidationError):
        service.dependencies(
            principal,
            WorkspaceGraphQueryRequest("workspace-1", page_size=1, node_limit=1, cursor=first.next_cursor),
            "a",
        )
    with pytest.raises(WorkspaceGraphQueryValidationError):
        service.impact(principal, WorkspaceGraphQueryRequest("workspace-1", page_size=1, cursor=first.next_cursor), "a")


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
