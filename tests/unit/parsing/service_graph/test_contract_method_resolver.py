from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.contract_method_resolver import (
    CallerIdentity,
    ContractMethodResolutionOutcome,
    ContractMethodResolver,
    ProtocolMetadata,
)
from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext
from ontoagent.parsing.service_graph.detectors.dubbo_method import DubboMethodDetector
from ontoagent.parsing.service_graph.java_contract_index import (
    ContractSourceMapping,
    ContractSourceRole,
    JavaContractIndex,
    JavaContractSource,
    JavaContractVisibilityResult,
)
from ontoagent.parsing.service_graph.methods import RetainedSourceCall
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


def _source(manifest: dict[str, object], repo_id: str, role: ContractSourceRole) -> JavaContractSource:
    repository = _repository(manifest, repo_id)
    fixture_path = repository.get("fixture_path", repo_id)
    assert isinstance(fixture_path, str)
    return JavaContractSource(repo_id, repo_id, _revision(manifest, repo_id), FIXTURE_ROOT / fixture_path, role)


def _consumer_calls(manifest: dict[str, object]) -> tuple[RetainedSourceCall, ...]:
    repo_id = "sample-checkout-consumer"
    snapshot = RepositorySnapshot(repo_id, _revision(manifest, repo_id), FIXTURE_ROOT / repo_id, frozenset({"java"}))
    facts = DubboMethodDetector().detect_methods(
        snapshot,
        MethodDetectionContext(repo_id, repo_id, repo_id, snapshot.source_revision, "fixture-generation"),
    )
    return facts.retained_source_calls


def _mapping(call: RetainedSourceCall, source: JavaContractSource, version: str = "1.0") -> ContractSourceMapping:
    return ContractSourceMapping(
        call.repo_id,
        call.module_id,
        call.source_revision,
        source.repo_id,
        source.module_id,
        source.source_revision,
        version,
        "workspace-contracts.yaml",
        4,
        4,
    )


def _visibility(call: RetainedSourceCall, mappings: tuple[ContractSourceMapping, ...] = ()):
    manifest = _manifest()
    api = _source(manifest, "sample-order-contract", ContractSourceRole.API)
    index = JavaContractIndex()
    result = index.build((api,))
    settings = dict(call.protocol_settings)
    return index.visible_contracts(
        result,
        call.repo_id,
        call.module_id,
        call.source_revision,
        settings["version"],
        mappings,
        call.receiver_type,
    )


def _metadata(call: RetainedSourceCall) -> ProtocolMetadata:
    return ProtocolMetadata("dubbo", call.protocol_settings)


def _compatible(call: RetainedSourceCall, visibility: JavaContractVisibilityResult, metadata: ProtocolMetadata) -> bool:
    return bool(visibility.mappings) and dict(call.protocol_settings) == dict(metadata.settings)


def _resolve(call: RetainedSourceCall, mappings: tuple[ContractSourceMapping, ...] = ()):
    return ContractMethodResolver().resolve(
        call,
        CallerIdentity.from_retained_call(call),
        _visibility(call, mappings),
        _metadata(call),
        _compatible,
    )


@pytest.mark.unit
def test_a01_resolves_exact_contract_method_when_explicit_mapping_authorizes_visibility() -> None:
    manifest = _manifest()
    call = next(item for item in _consumer_calls(manifest) if item.start_line == 20)
    api = _source(manifest, "sample-order-contract", ContractSourceRole.API)

    resolution = _resolve(call, (_mapping(call, api),))

    assert resolution.outcome is ContractMethodResolutionOutcome.DETERMINED
    assert resolution.contract_method is not None
    assert resolution.contract_method.canonical_signature == manifest["cases"]["A01"]["canonical_signature"]


@pytest.mark.unit
def test_no_mapping_remains_contract_missing_without_visibility_evidence() -> None:
    call = next(item for item in _consumer_calls(_manifest()) if item.start_line == 20)

    resolution = _resolve(call)

    assert resolution.outcome is ContractMethodResolutionOutcome.CONTRACT_MISSING
    assert resolution.contract_method is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("line", "expected_signature"),
    (
        (20, "example.orders.api.OrderService#getOrder(java.lang.String):example.orders.api.OrderSummary"),
        (28, "example.orders.api.OrderService#getOrder(int):example.orders.api.OrderSummary"),
        (32, "example.orders.api.OrderService#getOrder(long):example.orders.api.OrderSummary"),
    ),
)
def test_typed_calls_select_only_the_exact_contract_overload(line: int, expected_signature: str) -> None:
    manifest = _manifest()
    call = next(item for item in _consumer_calls(manifest) if item.start_line == line)
    api = _source(manifest, "sample-order-contract", ContractSourceRole.API)

    resolution = _resolve(call, (_mapping(call, api),))

    assert resolution.outcome is ContractMethodResolutionOutcome.DETERMINED
    assert resolution.contract_method is not None
    assert resolution.contract_method.canonical_signature == expected_signature


@pytest.mark.unit
def test_a09_version_conflict_remains_a_contract_stage_conflict() -> None:
    manifest = _manifest()
    call = next(item for item in _consumer_calls(manifest) if item.start_line == 36)
    api = _source(manifest, "sample-order-contract", ContractSourceRole.API)

    resolution = _resolve(call, (_mapping(call, api, "1.0"),))

    assert resolution.outcome is ContractMethodResolutionOutcome.VERSION_CONFLICT
    assert resolution.contract_method is None


@pytest.mark.unit
def test_dynamic_target_does_not_resolve_even_when_a_contract_is_visible() -> None:
    manifest = _manifest()
    call = next(item for item in _consumer_calls(manifest) if item.start_line == 48)
    api = _source(manifest, "sample-order-contract", ContractSourceRole.API)

    resolution = _resolve(call, (_mapping(call, api),))

    assert resolution.outcome is ContractMethodResolutionOutcome.CONTRACT_MISSING
    assert resolution.contract_method is None


@pytest.mark.unit
def test_a14_repeated_source_calls_produce_distinct_resolutions() -> None:
    manifest = _manifest()
    api = _source(manifest, "sample-order-contract", ContractSourceRole.API)
    calls = tuple(item for item in _consumer_calls(manifest) if item.method_name == "cancelOrder")

    resolutions = tuple(_resolve(call, (_mapping(call, api),)) for call in calls)

    assert len(resolutions) == 2
    assert {item.retained_call_id for item in resolutions} == {item.id for item in calls}
    assert all(item.outcome is ContractMethodResolutionOutcome.DETERMINED for item in resolutions)
