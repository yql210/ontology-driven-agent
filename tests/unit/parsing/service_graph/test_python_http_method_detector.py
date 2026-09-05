from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext, MethodDetector
from ontoagent.parsing.service_graph.detectors.python_http_method import PythonHttpMethodDetector
from ontoagent.parsing.service_graph.method_graph_writer import MethodGraphScope, MethodGraphWritePlan, fact_from_dict
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.workspace.models import (
    WorkspaceGeneration,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)

FIXTURE = Path(__file__).parents[3] / "fixtures/service_graph/python_http_three_repo"
REVISIONS = {"provider-api": "provider-v1", "consumer-client": "consumer-v1", "isolated-worker": "worker-v1"}


def _detect(repo_id: str, generation_id: str = "generation-python-http"):
    snapshot = RepositorySnapshot(repo_id, REVISIONS[repo_id], FIXTURE / repo_id, frozenset({"python"}))
    return PythonHttpMethodDetector().detect_methods(
        snapshot, MethodDetectionContext(repo_id, repo_id, repo_id, snapshot.source_revision, generation_id)
    )


def test_python_http_method_detector_implements_sdk_and_json_contract() -> None:
    detector = PythonHttpMethodDetector()

    assert isinstance(detector, MethodDetector)
    assert detector.metadata.supported_languages == frozenset({"python"})
    assert detector.metadata.capabilities[0].capability_id == "python-http-methods"
    assert json.loads(json.dumps(_detect("provider-api").to_dict()))["generation_id"] == "generation-python-http"


def test_fastapi_and_flask_routes_emit_provider_operations_bindings_and_router_prefix() -> None:
    facts = _detect("provider-api")

    assert {item.declaring_interface_fqcn for item in facts.operations} == {
        "spring-http:GET:/orders",
        "spring-http:POST:/orders",
        "spring-http:GET:/v1/orders/{order_id}",
        "spring-http:GET:/flask/orders",
        "spring-http:POST:/flask/orders",
    }
    assert len(facts.operations) == len(facts.bindings) == 5
    assert all(item.implementation_id is not None for item in facts.bindings)
    assert any(item.reason_code == "UNSUPPORTED_TARGET_SHAPE" for item in facts.unresolved)


def test_requests_and_httpx_calls_emit_exact_method_path_calls_for_sync_and_async_functions() -> None:
    facts = _detect("consumer-client")

    calls = {
        (
            item.target_reference,
            next(x.method_name for x in facts.implementations if x.id == item.caller_implementation_id),
        )
        for item in facts.consumer_calls
    }
    assert calls == {
        ("spring-http:GET:/orders", "checkout"),
        ("spring-http:POST:/orders", "checkout"),
        ("spring-http:GET:/v1/orders/{order_id}", "checkout"),
        ("spring-http:GET:/flask/orders", "checkout"),
        ("spring-http:POST:/flask/orders", "async_checkout"),
        ("spring-http:POST:/orders", "async_checkout"),
    }
    assert all(item.target_kind == "operation" for item in facts.consumer_calls)


def test_dynamic_unknown_and_global_calls_emit_typed_unresolved_facts_without_helper_calls() -> None:
    facts = _detect("consumer-client")

    reasons = {item.reason_code for item in facts.unresolved}
    assert {"DYNAMIC_TARGET", "UNSUPPORTED_TARGET_SHAPE", "MISSING_IMPLEMENTATION"} <= reasons
    helper = next(item for item in facts.implementations if item.method_name == "helper")
    assert all(item.caller_implementation_id != helper.id for item in facts.consumer_calls)


def test_workspace_resolves_only_the_exact_python_http_method_and_path_across_repositories() -> None:
    facts = tuple(_detect(repo_id) for repo_id in REVISIONS)
    generation = WorkspaceGeneration(
        "workspace",
        "generation-python-http",
        tuple(
            WorkspaceRepositorySnapshot(
                "workspace",
                repo_id,
                "main",
                revision,
                WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
            )
            for repo_id, revision in REVISIONS.items()
        ),
    )
    plan = MethodGraphWritePlan(MethodGraphScope("namespace", generation), facts)
    provider = _detect("provider-api")
    by_reference = {item.declaring_interface_fqcn: item.id for item in provider.operations}

    assert plan.operation_id_for("spring-http:GET:/orders") == by_reference["spring-http:GET:/orders"]
    assert plan.operation_id_for("spring-http:POST:/orders") == by_reference["spring-http:POST:/orders"]
    with pytest.raises(ValueError, match="ambiguous"):
        plan.operation_id_for("spring-http:DELETE:/orders")
    assert fact_from_dict(_detect("provider-api").to_dict()) == _detect("provider-api")
