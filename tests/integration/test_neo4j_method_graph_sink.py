"""Remote Neo4j coverage for the isolated method-fact persistence slice."""

from __future__ import annotations

import os
from dataclasses import replace
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from ontoagent.parsing.service_graph.method_graph_writer import (
    MethodGraphScope,
    MethodGraphWritePlan,
    MethodGraphWriter,
)
from ontoagent.parsing.service_graph.methods import (
    ConsumerMethodCall,
    ImplementationMethod,
    MethodEvidence,
    MethodFacts,
    OperationBinding,
    ServiceOperation,
)
from ontoagent.parsing.service_graph.neo4j_method_graph_sink import Neo4jMethodGraphSink
from ontoagent.parsing.service_graph.workspace.models import (
    WorkspaceGeneration,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)

pytestmark = pytest.mark.integration


def _credentials() -> tuple[str, str, str]:
    uri = os.getenv("ONTOAGENT_NEO4J_URI")
    user = os.getenv("ONTOAGENT_NEO4J_USER")
    password = os.getenv("ONTOAGENT_NEO4J_PASSWORD")
    if not all((uri, user, password)):
        pytest.skip("explicit ONTOAGENT_NEO4J_URI, ONTOAGENT_NEO4J_USER, and ONTOAGENT_NEO4J_PASSWORD are required")
    return uri, user, password


def _scope(namespace: str) -> MethodGraphScope:
    snapshots = tuple(
        WorkspaceRepositorySnapshot(
            "method-workspace",
            repo_id,
            "main",
            f"revision-{repo_id}",
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id in ("consumer", "provider", "isolated")
    )
    return MethodGraphScope(namespace, WorkspaceGeneration("method-workspace", "method-generation", snapshots))


def _facts(repo_id: str, *, target_operation_id: str | None = None) -> MethodFacts:
    revision = f"revision-{repo_id}"
    evidence = MethodEvidence(
        repo_id,
        "module",
        "service",
        revision,
        "method-generation",
        "src/Service.java",
        1,
        1,
        "generic-java",
        "1",
        "method",
        f"{repo_id}.method",
        1.0,
    )
    operation = ServiceOperation(
        repo_id,
        "module",
        "service",
        revision,
        "method-generation",
        "provider",
        f"example.{repo_id}.Api",
        "find",
        f"example.{repo_id}.Api#find(java.lang.String):java.lang.String",
        (evidence.id,),
    )
    implementation = ImplementationMethod(
        repo_id,
        "module",
        "service",
        revision,
        "method-generation",
        f"example.{repo_id}.Service",
        "find",
        f"example.{repo_id}.Service#find(java.lang.String):java.lang.String",
        "src/Service.java",
        (evidence.id,),
    )
    calls = (
        ()
        if target_operation_id is None
        else (
            ConsumerMethodCall(
                repo_id,
                "module",
                "service",
                revision,
                "method-generation",
                implementation.id,
                target_operation_id,
                "operation",
                (evidence.id,),
            ),
        )
    )
    binding = OperationBinding(
        repo_id,
        "module",
        "service",
        revision,
        "method-generation",
        "endpoint-ref",
        operation.id,
        implementation.id,
        (evidence.id,),
    )
    return MethodFacts(
        "generic-java",
        "1",
        repo_id,
        revision,
        "method-generation",
        (operation,),
        (implementation,),
        calls,
        (binding,),
        (evidence,),
        (),
    )


def test_neo4j_method_sink_round_trips_three_repositories_and_blocks_wrong_namespace() -> None:
    uri, user, password = _credentials()
    namespace = f"method-graph-{uuid4()}"
    scope = _scope(namespace)
    provider = _facts("provider")
    plan = MethodGraphWritePlan(
        scope, (provider, _facts("consumer", target_operation_id=provider.operations[0].id), _facts("isolated"))
    )
    driver = GraphDatabase.driver(uri, auth=(user, password))
    sink = Neo4jMethodGraphSink(driver, scope)
    try:
        receipt = MethodGraphWriter(sink).write(plan)

        assert receipt.confirmed
        assert receipt.node_count == plan.node_count
        assert receipt.relation_count == plan.relation_count
        assert receipt.readback == plan
        with pytest.raises(ValueError, match="namespace"):
            sink.readback(replace(scope, namespace=f"wrong-{namespace}"))
    finally:
        with driver.session() as session:
            session.run("MATCH (n {namespace: $namespace}) DETACH DELETE n", namespace=namespace)
        driver.close()
