"""Remove only the remote Neo4j data created by the P6-E browser fixture."""

from __future__ import annotations

import os
from hashlib import sha256

from neo4j import GraphDatabase

from ontoagent.config import OntoAgentConfig


def main() -> None:
    OntoAgentConfig.from_env()
    uri, user, password = (
        os.getenv(name) for name in ("ONTOAGENT_NEO4J_URI", "ONTOAGENT_NEO4J_USER", "ONTOAGENT_NEO4J_PASSWORD")
    )
    workspace_id = os.getenv("P6_E_WORKSPACE_ID")
    generation_ids = (os.getenv("P6_E_GENERATION_ID"), os.getenv("P6_E_FROM_GENERATION_ID"))
    if not all((uri, user, password, workspace_id, *generation_ids)):
        raise RuntimeError("P6-E cleanup requires Neo4j credentials and fixture identifiers")
    namespaces = [
        f"workspace-generation-{sha256(f'{workspace_id}\x00{generation_id}'.encode()).hexdigest()}"
        for generation_id in generation_ids
    ]
    query = (
        "MATCH (node) "
        "WHERE node.workspaceId = $workspace_id "
        "OR node._ontoagent_namespace IN $namespaces "
        "OR node.namespace IN $namespaces "
        "DETACH DELETE node"
    )
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            session.run(query, workspace_id=workspace_id, namespaces=namespaces).consume()
    finally:
        driver.close()


if __name__ == "__main__":
    main()
