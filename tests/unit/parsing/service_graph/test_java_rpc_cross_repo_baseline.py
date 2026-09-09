from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext
from ontoagent.parsing.service_graph.detectors.dubbo_method import DubboMethodDetector
from ontoagent.parsing.service_graph.models import RepositorySnapshot

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "java_rpc_contracts"


def _manifest() -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / "expected.json").read_text(encoding="utf-8"))


def _repository(manifest: dict[str, object], repo_id: str) -> dict[str, object]:
    repositories = manifest["repositories"]
    assert isinstance(repositories, list)
    return next(item for item in repositories if item["repo_id"] == repo_id)


def _revision(manifest: dict[str, object], repo_id: str) -> str:
    revision = _repository(manifest, repo_id)["revision"]
    assert isinstance(revision, str)
    return revision


def _detect(manifest: dict[str, object], repo_id: str) -> object:
    repository = _repository(manifest, repo_id)
    fixture_path = repository.get("fixture_path", repo_id)
    assert isinstance(fixture_path, str)
    revision = _revision(manifest, repo_id)
    snapshot = RepositorySnapshot(repo_id, revision, FIXTURE_ROOT / fixture_path, frozenset({"java"}))
    return DubboMethodDetector().detect_methods(
        snapshot, MethodDetectionContext(repo_id, repo_id, repo_id, revision, "baseline-generation")
    )


@pytest.mark.unit
def test_java_rpc_contract_fixture_manifest_is_frozen_and_source_pinned() -> None:
    manifest = _manifest()

    assert manifest["schema_version"] == 1
    assert manifest["fixture_revision"] == "java-rpc-contracts-d1-v2"
    repositories = manifest["repositories"]
    assert isinstance(repositories, list)
    assert {item["repo_id"] for item in repositories} == {
        "sample-order-contract",
        "sample-order-provider",
        "sample-checkout-consumer",
        "provider-client-module-provider",
        "provider-client-module-client",
    }
    assert {item["repo_id"]: item["revision"] for item in repositories} == {
        "sample-order-contract": "6666666666666666666666666666666666666666",
        "sample-order-provider": "7777777777777777777777777777777777777777",
        "sample-checkout-consumer": "8888888888888888888888888888888888888888",
        "provider-client-module-provider": "4444444444444444444444444444444444444444",
        "provider-client-module-client": "5555555555555555555555555555555555555555",
    }
    api_repository = _repository(manifest, "sample-order-contract")
    assert api_repository["role"] == "api"
    assert all(
        "DubboService" not in (FIXTURE_ROOT / api_repository["repo_id"] / path).read_text(encoding="utf-8")
        for path in api_repository["source_locations"]
    )
    consumer_source = FIXTURE_ROOT / "sample-checkout-consumer/src/main/java/example/checkout/CheckoutService.java"
    assert "interface OrderService" not in consumer_source.read_text(encoding="utf-8")
    for repository in repositories:
        root = FIXTURE_ROOT / repository.get("fixture_path", repository["repo_id"])
        assert all((root / path).is_file() for path in repository["source_locations"])
        facts = _detect(manifest, repository["repo_id"])
        assert facts.source_revision == repository["revision"]
        assert all(evidence.source_revision == repository["revision"] for evidence in facts.evidences)
    cases = manifest["cases"]
    assert isinstance(cases, dict)
    assert set(cases) == {"A01", "A02", "A03", "A04", "A05", "A08", "A09", "A10", "A11", "A12", "A14"}
    assert {case_id: case["expected_outcome"] for case_id, case in cases.items()} == {
        "A01": "determined",
        "A02": "contract_only",
        "A03": "determined",
        "A04": "determined",
        "A05": "determined",
        "A08": "determined",
        "A09": "unresolved",
        "A10": "unresolved",
        "A11": "unresolved",
        "A12": "unresolved",
        "A14": "determined",
    }
    for case in cases.values():
        source = case["source"]
        repository = _repository(manifest, source["repo_id"])
        root = FIXTURE_ROOT / repository.get("fixture_path", repository["repo_id"])
        source_lines = (root / source["path"]).read_text(encoding="utf-8").splitlines()
        assert source_lines[source["line"] - 1].strip()

    int_call = cases["A05"]
    long_call = cases["A08"]
    assert int_call["source"]["line"] == 28
    assert long_call["source"]["line"] == 32
    assert consumer_source.read_text(encoding="utf-8").splitlines()[int_call["source"]["line"] - 1].strip() == (
        "return orderService.getOrder(100);"
    )
    assert consumer_source.read_text(encoding="utf-8").splitlines()[long_call["source"]["line"] - 1].strip() == (
        "return orderService.getOrder(100L);"
    )
    assert int_call["argument_type"] == "int"
    assert (
        int_call["canonical_signature"]
        == "example.orders.api.OrderService#getOrder(int):example.orders.api.OrderSummary"
    )
    assert long_call["argument_type"] == "long"
    assert (
        long_call["canonical_signature"]
        == "example.orders.api.OrderService#getOrder(long):example.orders.api.OrderSummary"
    )


@pytest.mark.unit
def test_d1_g1_cross_repository_contract_calls_are_retained_at_source_capture() -> None:
    manifest = _manifest()

    provider = _detect(manifest, "sample-order-provider")
    consumer = _detect(manifest, "sample-checkout-consumer")

    assert not provider.operations
    assert not consumer.consumer_calls
    assert {item.reason_code for item in provider.unresolved} == {"MISSING_DECLARATION"}
    assert {item.reason_code for item in consumer.unresolved} == {"DYNAMIC_TARGET", "MISSING_DECLARATION"}
    retained_by_line = {item.start_line: item for item in consumer.retained_source_calls}
    assert retained_by_line[20].resolution_reason == "CONTRACT_MISSING"
    assert retained_by_line[20].receiver_type == "example.orders.api.OrderService"
    assert retained_by_line[48].resolution_reason == "DYNAMIC_TARGET"
    assert manifest["cases"]["A01"]["expected_outcome"] == "determined"
    assert manifest["cases"]["A03"]["expected_outcome"] == "determined"


@pytest.mark.unit
def test_d1_g2_typed_proxy_calls_are_retained_with_argument_type_evidence() -> None:
    manifest = _manifest()

    consumer = _detect(manifest, "sample-checkout-consumer")

    retained = next(item for item in consumer.retained_source_calls if item.start_line == 20)
    assert retained.argument_summaries == ("id",)
    assert retained.argument_types == ("java.lang.String",)
    assert retained.resolution_reason == "CONTRACT_MISSING"
    assert len(retained.argument_evidence_ids) == 1
    assert manifest["cases"]["A04"]["argument_type"] == "java.lang.String"
    assert manifest["cases"]["A04"]["expected_outcome"] == "determined"
    int_call = next(item for item in consumer.retained_source_calls if item.start_line == 28)
    long_call = next(item for item in consumer.retained_source_calls if item.start_line == 32)
    assert int_call.argument_summaries == ("100",)
    assert int_call.argument_types == ("int",)
    assert long_call.argument_summaries == ("100L",)
    assert long_call.argument_types == ("long",)
    assert manifest["cases"]["A05"]["argument_type"] == "int"
    assert manifest["cases"]["A05"]["canonical_signature"] == (
        "example.orders.api.OrderService#getOrder(int):example.orders.api.OrderSummary"
    )
    assert manifest["cases"]["A08"]["argument_type"] == "long"
    assert manifest["cases"]["A08"]["canonical_signature"] == (
        "example.orders.api.OrderService#getOrder(long):example.orders.api.OrderSummary"
    )


@pytest.mark.unit
def test_two_repository_provider_client_module_variant_is_source_complete() -> None:
    manifest = _manifest()

    provider = _detect(manifest, "provider-client-module-provider")
    client = _detect(manifest, "provider-client-module-client")

    assert {item.group for item in provider.operations} == {"missing", "orders"}
    assert len(provider.bindings) == 3
    assert len(client.consumer_calls) == 1
    assert not client.unresolved
    retained = client.retained_source_calls
    assert len(retained) == 1
    assert retained[0].argument_types == ("java.lang.String",)
    assert retained[0].resolution_reason is None


@pytest.mark.unit
def test_d1_a14_repeated_identical_calls_remain_distinct_source_captures() -> None:
    manifest = _manifest()
    consumer = _detect(manifest, "sample-checkout-consumer")

    calls = [item for item in consumer.retained_source_calls if item.method_name == "cancelOrder"]
    assert sorted(item.start_line for item in calls) == [52, 53]
    assert len({item.id for item in calls}) == 2
