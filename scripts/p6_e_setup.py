"""Create the real remote Neo4j fixture consumed by the P6-E browser test."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from neo4j import GraphDatabase

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.workspace_authorization import PrincipalIdentity, WorkspaceGrant
from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.workspace.models import (
    Workspace,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import (
    Neo4jWorkspaceServiceGraphPublishComponentFactory,
    WorkspaceServiceGraphPublishInput,
    WorkspaceServiceGraphPublishOrchestrator,
)
from ontoagent.store.neo4j_workspace_acl_repository import Neo4jWorkspaceAclRepository

sys.path.insert(0, str(Path(__file__).parents[1]))
from tests.integration.test_workspace_service_graph_publish_orchestrator import _method_facts


def main() -> None:
    OntoAgentConfig.from_env()
    uri, user, password = (
        os.getenv(name) for name in ("ONTOAGENT_NEO4J_URI", "ONTOAGENT_NEO4J_USER", "ONTOAGENT_NEO4J_PASSWORD")
    )
    if not all((uri, user, password)):
        raise RuntimeError("P6-E requires ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD")
    root = Path(__file__).parents[1] / "tests/fixtures/service_graph/neutral_three_repo"
    repos = {
        "provider-orders": "fixture-provider-v1",
        "consumer-checkout": "fixture-consumer-v1",
        "isolated-catalog": "fixture-isolated-v1",
    }
    workspace = Workspace(f"p6-e-{uuid4()}", "P6-E browser workspace")
    first, second = f"generation-one-{uuid4()}", f"generation-two-{uuid4()}"
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        factory = Neo4jWorkspaceServiceGraphPublishComponentFactory(
            driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
        )
        orchestrator = WorkspaceServiceGraphPublishOrchestrator(factory)
        for generation, expected, suffix in ((first, None, "one"), (second, first, "two")):
            snapshots = tuple(
                WorkspaceRepositorySnapshot(
                    workspace.workspace_id,
                    repo,
                    "main",
                    rev,
                    WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo}.git"),
                )
                for repo, rev in repos.items()
            )
            runtime = tuple(
                RepositorySnapshot(repo, rev, root / repo, frozenset({"java", "yaml"})) for repo, rev in repos.items()
            )
            result = orchestrator.publish(
                WorkspaceServiceGraphPublishInput(
                    workspace,
                    snapshots,
                    runtime,
                    f"p6-e-{suffix}-{uuid4()}",
                    generation,
                    expected,
                    method_facts=_method_facts(generation),
                )
            )
            if result.status.value != "active":
                raise RuntimeError(f"publish failed: {result.to_dict()}")
        acl = Neo4jWorkspaceAclRepository(driver)
        acl.upsert_grant(WorkspaceGrant.full(PrincipalIdentity("e2e-full"), workspace.workspace_id))
        acl.upsert_grant(
            WorkspaceGrant.filtered(PrincipalIdentity("e2e-filtered"), workspace.workspace_id, ("consumer-checkout",))
        )
        print(
            "P6_E_FIXTURE="
            + json.dumps(
                {
                    "WORKSPACE_ID": workspace.workspace_id,
                    "GENERATION_ID": second,
                    "FROM_GENERATION_ID": first,
                    "FULL_PRINCIPAL": "e2e-full",
                    "FILTERED_PRINCIPAL": "e2e-filtered",
                }
            )
        )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
