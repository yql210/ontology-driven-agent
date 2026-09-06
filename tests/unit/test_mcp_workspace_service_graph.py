"""MCP adapter coverage for workspace service graph reads."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ontoagent.domain.workspace_authorization import WorkspaceAuthorizationFailure
from ontoagent.domain.workspace_graph_query import WorkspaceGraphPage, WorkspaceGraphVisibility
from ontoagent.execution.workspace_query_authorization import WorkspaceAuthorizationError


def test_mcp_workspace_service_directory_emits_shared_envelope() -> None:
    from ontoagent.api import mcp_server

    service = MagicMock()
    service.service_directory.return_value = WorkspaceGraphPage(
        "workspace-1", "generation-1", WorkspaceGraphVisibility.FULL, (), (), None
    )
    with patch.object(mcp_server, "workspace_service_graph_query_service_factory") as factory:
        factory.create.return_value.__enter__.return_value = service
        result = mcp_server.workspace_service_graph_directory("workspace-1", "alice")

    assert result == {
        "workspace_id": "workspace-1",
        "generation_id": "generation-1",
        "visibility": "full",
        "nodes": [],
        "edges": [],
        "next_cursor": None,
    }


def test_generic_graph_query_rejects_workspace_scoped_cypher() -> None:
    from ontoagent.api import mcp_server

    with pytest.raises(ValueError, match="dedicated tools"):
        mcp_server.graph_query("MATCH (n:OntoAgentWorkspace) RETURN n")


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (WorkspaceAuthorizationError(WorkspaceAuthorizationFailure.FORBIDDEN), {"error": "forbidden"}),
        (None, {"error": "invalid_request"}),
    ),
)
def test_mcp_workspace_service_directory_returns_safe_error_envelope(
    error: WorkspaceAuthorizationError | None, expected: dict[str, str]
) -> None:
    from ontoagent.api import mcp_server

    if error is None:
        result = mcp_server.workspace_service_graph_directory("workspace-1", "alice", page_size=0)
    else:
        service = MagicMock()
        service.service_directory.side_effect = error
        with patch.object(mcp_server, "workspace_service_graph_query_service_factory") as factory:
            factory.create.return_value.__enter__.return_value = service
            result = mcp_server.workspace_service_graph_directory("workspace-1", "alice")

    assert result == expected
