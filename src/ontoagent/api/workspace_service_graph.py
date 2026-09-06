"""Shared construction and serialization for workspace service graph transports."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Generator
from contextlib import contextmanager

from neo4j import GraphDatabase

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.workspace_graph_query import WorkspaceGraphPage
from ontoagent.execution.workspace_query_authorization import WorkspaceQueryAuthorizationService
from ontoagent.execution.workspace_service_graph_query import WorkspaceServiceGraphQueryService
from ontoagent.parsing.service_graph.workspace.neo4j_query_repository import Neo4jWorkspaceServiceGraphQueryRepository
from ontoagent.store.neo4j_workspace_acl_repository import Neo4jWorkspaceAclRepository


class WorkspaceServiceGraphQueryServiceFactory:
    """Open one trusted workspace query service backed by a single Neo4j driver."""

    @contextmanager
    def create(self) -> Generator[WorkspaceServiceGraphQueryService]:
        config = OntoAgentConfig.from_env()
        driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password))
        try:
            authorization = WorkspaceQueryAuthorizationService(Neo4jWorkspaceAclRepository(driver))
            secret = hashlib.sha256(
                os.getenv("ONTOAGENT_WORKSPACE_QUERY_CURSOR_SECRET", config.neo4j_password).encode()
            ).digest()
            yield WorkspaceServiceGraphQueryService(
                authorization, Neo4jWorkspaceServiceGraphQueryRepository(driver), secret
            )
        finally:
            driver.close()


workspace_service_graph_query_service_factory = WorkspaceServiceGraphQueryServiceFactory()


def workspace_graph_page_envelope(page: WorkspaceGraphPage) -> dict[str, object]:
    """Return the stable public response shape shared by Web, CLI, and MCP."""
    return {
        "workspace_id": page.workspace_id,
        "generation_id": page.generation_id,
        "visibility": page.visibility.value,
        "nodes": list(page.nodes),
        "edges": list(page.edges),
        "next_cursor": page.next_cursor,
    }
