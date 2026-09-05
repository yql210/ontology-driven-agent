from __future__ import annotations

from dataclasses import replace

import pytest

from ontoagent.parsing.service_graph.method_graph_writer import (
    InMemoryMethodGraphSink,
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
from ontoagent.parsing.service_graph.workspace.models import (
    WorkspaceGeneration,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)


def _scope() -> MethodGraphScope:
    snapshots = tuple(
        WorkspaceRepositorySnapshot(
            "workspace-1",
            repo_id,
            "main",
            f"revision-{repo_id}",
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id in ("consumer", "provider", "isolated")
    )
    return MethodGraphScope("method-namespace", WorkspaceGeneration("workspace-1", "generation-1", snapshots))


def _facts(repo_id: str = "provider", signature: str = "find(java.lang.String)") -> MethodFacts:
    revision = f"revision-{repo_id}"
    evidence = MethodEvidence(
        repo_id,
        "module",
        "orders",
        revision,
        "generation-1",
        "src/OrderApi.java",
        1,
        1,
        "generic-java",
        "1",
        "declaration",
        "OrderApi.find",
        1.0,
    )
    operation = ServiceOperation(
        repo_id,
        "module",
        "orders",
        revision,
        "generation-1",
        "provider",
        "example.OrderApi",
        "find",
        f"example.OrderApi#{signature}:example.Order",
        (evidence.id,),
    )
    implementation = ImplementationMethod(
        repo_id,
        "module",
        "orders",
        revision,
        "generation-1",
        "example.OrderService",
        "find",
        f"example.OrderService#{signature}:example.Order",
        "src/OrderService.java",
        (evidence.id,),
    )
    binding = OperationBinding(
        repo_id,
        "module",
        "orders",
        revision,
        "generation-1",
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
        "generation-1",
        (operation,),
        (implementation,),
        (),
        (binding,),
        (evidence,),
        (),
    )


def test_method_plan_is_deterministic_and_preserves_overloads_and_repo_identity() -> None:
    scope = _scope()
    first = _facts("provider", "find(java.lang.String)")
    overload = _facts("provider", "find(long)")
    same_name_other_repo = _facts("isolated", "find(java.lang.String)")

    plan = MethodGraphWritePlan(scope, (same_name_other_repo, overload, first))

    assert plan == MethodGraphWritePlan(scope, (first, overload, same_name_other_repo))
    assert plan.fingerprint == MethodGraphWritePlan(scope, (first, overload, same_name_other_repo)).fingerprint
    assert len(plan.operation_ids) == 3


def test_method_writer_round_trips_exact_generic_protocol_facts_idempotently() -> None:
    plan = MethodGraphWritePlan(_scope(), (_facts(), _facts("consumer"), _facts("isolated")))
    sink = InMemoryMethodGraphSink(plan.scope)

    first = MethodGraphWriter(sink).write(plan)
    second = MethodGraphWriter(sink).write(plan)

    assert first.confirmed and second.confirmed
    assert first.fingerprint == plan.fingerprint
    assert first.readback == plan
    assert first.node_count == plan.node_count
    assert first.relation_count == plan.relation_count


def test_method_plan_rejects_orphans_and_scope_mismatches() -> None:
    facts = _facts()
    orphan_call = ConsumerMethodCall(
        "provider",
        "module",
        "orders",
        "revision-provider",
        "generation-1",
        "unknown",
        "target",
        "operation",
        (facts.evidences[0].id,),
    )
    with pytest.raises(ValueError, match="unknown implementation"):
        MethodFacts(
            facts.detector_id,
            facts.detector_version,
            facts.repo_id,
            facts.source_revision,
            facts.generation_id,
            facts.operations,
            facts.implementations,
            (orphan_call,),
            facts.bindings,
            facts.evidences,
            facts.unresolved,
        )
    with pytest.raises(ValueError, match="workspace snapshot"):
        MethodGraphWritePlan(_scope(), (_facts("missing"),))


def test_method_writer_fails_closed_for_wrong_namespace_and_receipt_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = MethodGraphWritePlan(_scope(), (_facts(),))
    sink = InMemoryMethodGraphSink(plan.scope)
    wrong_scope = replace(plan.scope, namespace="other")

    with pytest.raises(ValueError, match="namespace"):
        sink.readback(wrong_scope)

    monkeypatch.setattr(sink, "readback", lambda scope: replace(plan, facts=()))
    receipt = MethodGraphWriter(sink).write(plan)

    assert not receipt.confirmed
