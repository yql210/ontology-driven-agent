"""CLI adapter coverage for workspace service graph reads."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ontoagent.api.cli import main
from ontoagent.domain.workspace_authorization import WorkspaceAuthorizationFailure
from ontoagent.domain.workspace_graph_query import WorkspaceGraphPage, WorkspaceGraphVisibility
from ontoagent.execution.workspace_query_authorization import WorkspaceAuthorizationError


def test_workspace_service_graph_cli_emits_shared_json_envelope() -> None:
    service = MagicMock()
    service.service_directory.return_value = WorkspaceGraphPage(
        "workspace-1", "generation-1", WorkspaceGraphVisibility.FILTERED, (), (), None
    )
    with patch("ontoagent.api.cli.workspace_service_graph_query_service_factory") as factory:
        factory.create.return_value.__enter__.return_value = service
        result = CliRunner().invoke(
            main, ["workspace-service-graph", "directory", "workspace-1", "--principal", "alice"]
        )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "workspace_id": "workspace-1",
        "generation_id": "generation-1",
        "visibility": "filtered",
        "nodes": [],
        "edges": [],
        "next_cursor": None,
    }


@pytest.mark.parametrize(
    ("failure", "exit_code", "error"),
    (
        (WorkspaceAuthorizationFailure.FORBIDDEN, 3, "forbidden"),
        (WorkspaceAuthorizationFailure.CONFLICT, 5, "conflict"),
    ),
)
def test_workspace_service_graph_cli_emits_safe_authorization_error(
    failure: WorkspaceAuthorizationFailure, exit_code: int, error: str
) -> None:
    service = MagicMock()
    service.service_directory.side_effect = WorkspaceAuthorizationError(failure)
    with patch("ontoagent.api.cli.workspace_service_graph_query_service_factory") as factory:
        factory.create.return_value.__enter__.return_value = service
        result = CliRunner().invoke(
            main, ["workspace-service-graph", "directory", "workspace-1", "--principal", "alice"]
        )

    assert result.exit_code == exit_code
    assert json.loads(result.stderr) == {"error": error}


def test_workspace_service_graph_cli_routes_invalid_bound_to_safe_error_envelope() -> None:
    result = CliRunner().invoke(
        main, ["workspace-service-graph", "directory", "workspace-1", "--principal", "alice", "--page-size", "0"]
    )

    assert result.exit_code == 2
    assert json.loads(result.stderr) == {"error": "invalid_request"}
