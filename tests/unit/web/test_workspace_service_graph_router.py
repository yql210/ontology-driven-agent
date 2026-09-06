"""Transport tests for ACL-gated workspace service graph Web reads."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ontoagent.api.web.app import create_app
from ontoagent.api.web.router import workspace_service_graph
from ontoagent.domain.workspace_authorization import WorkspaceAuthorizationFailure
from ontoagent.domain.workspace_graph_query import WorkspaceGraphPage, WorkspaceGraphVisibility
from ontoagent.execution.workspace_query_authorization import WorkspaceAuthorizationError


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.mark.unit
def test_workspace_directory_uses_authenticated_principal_and_shared_envelope(client: TestClient) -> None:
    service = MagicMock()
    service.service_directory.return_value = WorkspaceGraphPage(
        "workspace-1", "generation-1", WorkspaceGraphVisibility.FULL, (), (), None
    )
    with patch.object(workspace_service_graph, "workspace_service_graph_query_service_factory") as factory:
        factory.create.return_value.__enter__.return_value = service
        response = client.get(
            "/api/workspaces/workspace-1/service-graph/directory",
            params={"generation_id": "generation-1", "page_size": 10},
            headers={"X-Workspace-Principal": "alice"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": "workspace-1",
        "generation_id": "generation-1",
        "visibility": "full",
        "nodes": [],
        "edges": [],
        "next_cursor": None,
    }
    call = service.service_directory.call_args
    assert call.args[0].principal_id == "alice"
    assert call.args[1].workspace_id == "workspace-1"
    assert call.args[1].generation_id == "generation-1"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (WorkspaceAuthorizationFailure.FORBIDDEN, 403),
        (WorkspaceAuthorizationFailure.NOT_FOUND, 404),
        (WorkspaceAuthorizationFailure.CONFLICT, 409),
    ],
)
def test_workspace_router_maps_authorization_outcomes_without_details(
    client: TestClient, failure: WorkspaceAuthorizationFailure, status: int
) -> None:
    service = MagicMock()
    service.service_directory.side_effect = WorkspaceAuthorizationError(failure)
    with patch.object(workspace_service_graph, "workspace_service_graph_query_service_factory") as factory:
        factory.create.return_value.__enter__.return_value = service
        response = client.get(
            "/api/workspaces/workspace-1/service-graph/directory", headers={"X-Workspace-Principal": "alice"}
        )

    assert response.status_code == status
    assert response.json() == {"detail": failure.value}


@pytest.mark.unit
def test_workspace_router_rejects_invalid_request_as_422(client: TestClient) -> None:
    response = client.get(
        "/api/workspaces/workspace-1/service-graph/directory",
        params={"page_size": 0},
        headers={"X-Workspace-Principal": "alice"},
    )
    assert response.status_code == 422
