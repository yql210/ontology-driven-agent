"""Tests for the durable workspace build Web vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ontoagent.api.web.app import create_app
from ontoagent.parsing.service_graph.workspace.models import WorkspaceGenerationState


@dataclass(frozen=True)
class _Submission:
    task_id: str
    workspace_id: str
    generation_id: str
    scheduled: bool
    request: object = object()


@dataclass(frozen=True)
class _Status:
    task_id: str
    workspace_id: str
    generation_id: str
    state: WorkspaceGenerationState


@pytest.fixture
def service() -> MagicMock:
    mock = MagicMock()
    mock.submit.return_value = _Submission("task-1", "workspace-1", "generation-1", True)
    mock.get_task_status.return_value = _Status(
        "task-1", "workspace-1", "generation-1", WorkspaceGenerationState.PENDING
    )
    return mock


@pytest.fixture
def factory(service: MagicMock) -> MagicMock:
    mock = MagicMock()
    mock.create.return_value = service
    return mock


def _body(tmp_path: Path) -> dict[str, object]:
    repositories = [
        {
            "repo_id": repo_id,
            "path": str(tmp_path / repo_id),
            "branch": "main",
            "source_revision": "revision",
            "languages": ["java"],
        }
        for repo_id in ("repo-a", "repo-b", "repo-c")
    ]
    return {
        "manifest": {"workspace_id": "workspace-1", "name": "Workspace", "repositories": repositories},
        "generation_id": "generation-1",
        "idempotency_key": "request-1",
    }


@pytest.mark.unit
def test_workspace_build_rejects_invalid_request_without_creating_or_scheduling(
    factory: MagicMock, tmp_path: Path
) -> None:
    with patch("ontoagent.api.web.router.workspace_build.workspace_build_service_factory", factory):
        response = TestClient(create_app()).post(
            "/api/workspaces/workspace-1/build", json={**_body(tmp_path), "unexpected": True}
        )

    assert response.status_code == 422
    factory.create.assert_not_called()


@pytest.mark.unit
def test_workspace_build_returns_pending_before_async_execution(
    factory: MagicMock, service: MagicMock, tmp_path: Path
) -> None:
    with patch("ontoagent.api.web.router.workspace_build.workspace_build_service_factory", factory):
        response = TestClient(create_app()).post("/api/workspaces/workspace-1/build", json=_body(tmp_path))

    assert response.status_code == 202
    assert response.json() == {
        "task_id": "task-1",
        "workspace_id": "workspace-1",
        "generation_id": "generation-1",
        "status": "PENDING",
    }
    service.submit.assert_called_once()
    service.run.assert_called_once_with(service.submit.return_value.request)
    service.close.assert_called_once_with()


@pytest.mark.unit
def test_workspace_build_duplicate_idempotency_returns_existing_task_without_rescheduling(
    factory: MagicMock, service: MagicMock, tmp_path: Path
) -> None:
    service.submit.return_value = _Submission("task-existing", "workspace-1", "generation-existing", False)
    with patch("ontoagent.api.web.router.workspace_build.workspace_build_service_factory", factory):
        response = TestClient(create_app()).post("/api/workspaces/workspace-1/build", json=_body(tmp_path))

    assert response.status_code == 202
    assert response.json()["task_id"] == "task-existing"
    service.run.assert_not_called()
    service.close.assert_called_once_with()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (WorkspaceGenerationState.ACTIVE, "generation is active"),
        (WorkspaceGenerationState.FAILED, "generation failed"),
        (WorkspaceGenerationState.BLOCKED, "generation blocked"),
    ],
)
def test_workspace_task_readback_returns_persisted_generation_state(
    factory: MagicMock, service: MagicMock, state: WorkspaceGenerationState, reason: str
) -> None:
    service.get_task_status.return_value = _Status("task-1", "workspace-1", "generation-1", state)
    with patch("ontoagent.api.web.router.workspace_build.workspace_build_service_factory", factory):
        response = TestClient(create_app()).get("/api/workspaces/workspace-1/tasks/task-1")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-1",
        "workspace_id": "workspace-1",
        "generation_id": "generation-1",
        "status": state.name,
        "reason": reason,
    }
    service.close.assert_called_once_with()


@pytest.mark.unit
def test_workspace_task_readback_maps_invalid_and_missing_to_422_and_404(
    factory: MagicMock, service: MagicMock
) -> None:
    service.get_task_status.side_effect = [ValueError("invalid task"), None]
    with patch("ontoagent.api.web.router.workspace_build.workspace_build_service_factory", factory):
        client = TestClient(create_app())
        invalid = client.get("/api/workspaces/%20/tasks/task-1")
        missing = client.get("/api/workspaces/workspace-1/tasks/missing")

    assert invalid.status_code == 422
    assert missing.status_code == 404


@pytest.mark.unit
def test_workspace_build_router_does_not_use_legacy_build_or_generic_store(factory: MagicMock, tmp_path: Path) -> None:
    with (
        patch("ontoagent.api.web.router.workspace_build.workspace_build_service_factory", factory),
        patch("ontoagent.api.web.app.create_graph_store") as create_graph_store,
        patch("ontoagent.api.web.router.build._run_build") as old_run_build,
        TestClient(create_app()) as client,
    ):
        response = client.post("/api/workspaces/workspace-1/build", json=_body(tmp_path))

    assert response.status_code == 202
    create_graph_store.assert_not_called()
    old_run_build.assert_not_called()
