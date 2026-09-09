"""Delete and verify only the remote Neo4j data created by the D4.2 browser E2E."""

from __future__ import annotations

import os

from neo4j import GraphDatabase

from ontoagent.config import OntoAgentConfig
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import WorkspaceServiceGraphPublishOrchestrator


def main() -> None:
    """Remove the exact generation namespace and prove no workspace artifacts remain."""
    OntoAgentConfig.from_env()
    uri, user, password = _credentials()
    workspace_id = _required("D42_WORKSPACE_ID")
    generation_id = _required("D42_GENERATION_ID")
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for(workspace_id, generation_id)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            session.run(
                "MATCH (node) WHERE node.workspaceId = $workspace_id "
                "OR node._ontoagent_namespace = $namespace "
                "OR node.namespace = $namespace DETACH DELETE node",
                workspace_id=workspace_id,
                namespace=namespace,
            ).consume()
            remaining = session.run(
                "MATCH (node) WHERE node.workspaceId = $workspace_id "
                "OR node._ontoagent_namespace = $namespace "
                "OR node.namespace = $namespace RETURN count(node) AS count",
                workspace_id=workspace_id,
                namespace=namespace,
            ).single()
        if remaining is None or remaining["count"] != 0:
            raise RuntimeError("D4.2 cleanup left generated workspace namespaces or nodes behind")
    finally:
        driver.close()


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        raise RuntimeError("D4.2 cleanup requires Neo4j credentials")
    assert uri is not None and user is not None and password is not None
    return uri, user, password


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"D4.2 cleanup requires {name}")
    return value


if __name__ == "__main__":
    main()
