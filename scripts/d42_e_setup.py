"""Publish a source-only D1 Java/RPC workspace for the isolated browser E2E."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from neo4j import GraphDatabase

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.workspace_authorization import PrincipalIdentity, WorkspaceGrant
from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.java_contract_index import (
    ContractSourceMapping,
    ContractSourceRole,
    JavaContractSource,
)
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.provider_method_binder import AuthorizedProviderSource
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
from ontoagent.parsing.service_graph.workspace_java_rpc_resolution import WorkspaceJavaRpcAuthorization
from ontoagent.store.neo4j_workspace_acl_repository import Neo4jWorkspaceAclRepository

FIXTURE_ROOT = Path(__file__).parents[1] / "tests/fixtures/java_rpc_contracts"
REPOSITORIES = ("sample-order-contract", "sample-order-provider", "sample-checkout-consumer")
CONTRACT_REPOSITORY = "sample-order-contract"
PROVIDER_REPOSITORY = "sample-order-provider"
CONSUMER_REPOSITORY = "sample-checkout-consumer"
ORDER_SERVICE_PATH = Path("src/main/java/example/orders/api/OrderService.java")


def main() -> None:
    """Publish a detector-derived generation and record full and filtered ACL grants."""
    OntoAgentConfig.from_env()
    uri, user, password = _credentials()
    revisions = _revisions()
    _assert_source_only_roots()

    workspace = Workspace(f"d42-e-{uuid4()}", "D4.2 D1 Java RPC browser workspace")
    generation = f"d42-java-rpc-{uuid4()}"
    snapshots = tuple(
        WorkspaceRepositorySnapshot(
            workspace.workspace_id,
            repo_id,
            "main",
            revisions[repo_id],
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id in REPOSITORIES
    )
    runtime = tuple(
        RepositorySnapshot(repo_id, revisions[repo_id], FIXTURE_ROOT / repo_id, frozenset({"java"}))
        for repo_id in REPOSITORIES
    )
    authorization = _authorization(revisions)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        orchestrator = WorkspaceServiceGraphPublishOrchestrator(
            Neo4jWorkspaceServiceGraphPublishComponentFactory(
                driver, DetectorRegistry([SpringHttpDetector(), DubboDetector(), MessagingDetector()])
            )
        )
        outcome = orchestrator.publish(
            WorkspaceServiceGraphPublishInput(
                workspace,
                snapshots,
                runtime,
                f"d42-browser-{uuid4()}",
                generation,
                None,
                java_rpc_authorization=authorization,
            )
        )
        if not outcome.graph_write_confirmed or outcome.status.value != "active":
            raise RuntimeError(f"D4.2 publish failed: {outcome.to_dict()}")

        acl = Neo4jWorkspaceAclRepository(driver)
        full_principal = "d42-full"
        filtered_principal = "d42-filtered"
        acl.upsert_grant(WorkspaceGrant.full(PrincipalIdentity(full_principal), workspace.workspace_id))
        acl.upsert_grant(
            WorkspaceGrant.filtered(
                PrincipalIdentity(filtered_principal), workspace.workspace_id, (CONSUMER_REPOSITORY,)
            )
        )
        print(
            "D42_FIXTURE="
            + json.dumps(
                {
                    "WORKSPACE_ID": workspace.workspace_id,
                    "GENERATION_ID": generation,
                    "FULL_PRINCIPAL": full_principal,
                    "FILTERED_PRINCIPAL": filtered_principal,
                }
            )
        )
    finally:
        driver.close()


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        raise RuntimeError("D4.2 requires ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD")
    assert uri is not None and user is not None and password is not None
    return uri, user, password


def _revisions() -> dict[str, str]:
    manifest = json.loads((FIXTURE_ROOT / "expected.json").read_text(encoding="utf-8"))
    repositories = manifest.get("repositories")
    if not isinstance(repositories, list):
        raise RuntimeError("D4.2 fixture manifest has no repositories")
    revisions = {
        item["repo_id"]: item["revision"]
        for item in repositories
        if isinstance(item, dict) and item.get("repo_id") in REPOSITORIES and isinstance(item.get("revision"), str)
    }
    if set(revisions) != set(REPOSITORIES):
        raise RuntimeError("D4.2 fixture manifest is missing a required repository revision")
    return revisions


def _assert_source_only_roots() -> None:
    """Reject fixture drift that copies the contract API into a provider or consumer root."""
    copied_roots = [
        repo_id
        for repo_id in (PROVIDER_REPOSITORY, CONSUMER_REPOSITORY)
        if (FIXTURE_ROOT / repo_id / ORDER_SERVICE_PATH).exists()
    ]
    if copied_roots:
        raise RuntimeError(f"D4.2 requires source-only contract ownership; copied OrderService found in {copied_roots}")
    if not (FIXTURE_ROOT / CONTRACT_REPOSITORY / ORDER_SERVICE_PATH).is_file():
        raise RuntimeError("D4.2 contract root is missing OrderService")


def _authorization(revisions: dict[str, str]) -> WorkspaceJavaRpcAuthorization:
    contract = JavaContractSource(
        CONTRACT_REPOSITORY,
        CONTRACT_REPOSITORY,
        revisions[CONTRACT_REPOSITORY],
        FIXTURE_ROOT / CONTRACT_REPOSITORY,
        ContractSourceRole.API,
    )
    mappings = tuple(_mapping(repo_id, revisions) for repo_id in (PROVIDER_REPOSITORY, CONSUMER_REPOSITORY))
    return WorkspaceJavaRpcAuthorization(
        (contract,),
        mappings,
        frozenset({AuthorizedProviderSource(PROVIDER_REPOSITORY, PROVIDER_REPOSITORY, revisions[PROVIDER_REPOSITORY])}),
        lambda _resolution, operation, binding: (
            operation.group == "orders"
            and operation.version == "1.0"
            and operation.alias is None
            and binding.provider_endpoint_reference.endswith("|group=orders|version=1.0|alias=")
        ),
    )


def _mapping(consumer_repo_id: str, revisions: dict[str, str]) -> ContractSourceMapping:
    return ContractSourceMapping(
        consumer_repo_id,
        consumer_repo_id,
        revisions[consumer_repo_id],
        CONTRACT_REPOSITORY,
        CONTRACT_REPOSITORY,
        revisions[CONTRACT_REPOSITORY],
        "1.0",
        "workspace-contracts.yaml",
        1,
        1,
    )


if __name__ == "__main__":
    main()
