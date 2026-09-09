from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.contract_method_resolver import (
    CallerIdentity,
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
)
from ontoagent.parsing.service_graph.methods import (
    ImplementationMethod,
    MethodEvidence,
    OperationBinding,
    ServiceOperation,
)
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.provider_method_binder import (
    AuthorizedProviderSource,
    ProviderMethodBinder,
    ProviderMethodBindingOutcome,
)

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "java_rpc_contracts"
CONSUMER_REPO = "sample-checkout-consumer"
CONTRACT_REPO = "sample-order-contract"
PROVIDER_REPO = "sample-order-provider"
GENERATION_ID = "provider-generation"


def _manifest() -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / "expected.json").read_text(encoding="utf-8"))


def _revision(repo_id: str) -> str:
    repositories = _manifest()["repositories"]
    assert isinstance(repositories, list)
    repository = next(item for item in repositories if item["repo_id"] == repo_id)
    revision = repository["revision"]
    assert isinstance(revision, str)
    return revision


def _consumer_calls() -> tuple[object, ...]:
    snapshot = RepositorySnapshot(
        CONSUMER_REPO, _revision(CONSUMER_REPO), FIXTURE_ROOT / CONSUMER_REPO, frozenset({"java"})
    )
    return (
        DubboMethodDetector()
        .detect_methods(
            snapshot,
            MethodDetectionContext(
                CONSUMER_REPO, CONSUMER_REPO, CONSUMER_REPO, snapshot.source_revision, GENERATION_ID
            ),
        )
        .retained_source_calls
    )


def _resolution(line: int):
    call = next(item for item in _consumer_calls() if item.start_line == line)
    api = JavaContractSource(
        CONTRACT_REPO,
        CONTRACT_REPO,
        _revision(CONTRACT_REPO),
        FIXTURE_ROOT / CONTRACT_REPO,
        ContractSourceRole.API,
    )
    mapping = ContractSourceMapping(
        call.repo_id,
        call.module_id,
        call.source_revision,
        api.repo_id,
        api.module_id,
        api.source_revision,
        "1.0",
        "workspace-contracts.yaml",
        1,
        1,
    )
    index = JavaContractIndex()
    visibility = index.visible_contracts(
        index.build((api,)),
        call.repo_id,
        call.module_id,
        call.source_revision,
        dict(call.protocol_settings)["version"],
        (mapping,),
        call.receiver_type,
    )
    return ContractMethodResolver().resolve(
        call,
        CallerIdentity.from_retained_call(call),
        visibility,
        ProtocolMetadata("dubbo", call.protocol_settings),
        lambda candidate, result, metadata: (
            bool(result.mappings) and dict(candidate.protocol_settings) == dict(metadata.settings)
        ),
    )


def _provider_facts(
    signature: str,
    *,
    group: str = "orders",
    version: str = "1.0",
    alias: str | None = None,
    binding_identity: str = "orders-service",
    generation_id: str = GENERATION_ID,
) -> tuple[ServiceOperation, OperationBinding, ImplementationMethod]:
    revision = _revision(PROVIDER_REPO)
    evidence = MethodEvidence(
        PROVIDER_REPO,
        PROVIDER_REPO,
        PROVIDER_REPO,
        revision,
        generation_id,
        "src/main/java/example/orders/provider/OrderServiceProvider.java",
        10,
        10,
        "fixture",
        "1",
        "provider_method",
        signature.replace(
            "example.orders.api.OrderService#",
            "example.orders.provider.OrderServiceProvider#",
            1,
        ),
        1.0,
    )
    implementation = ImplementationMethod(
        PROVIDER_REPO,
        PROVIDER_REPO,
        PROVIDER_REPO,
        revision,
        generation_id,
        "example.orders.provider.OrderServiceProvider",
        "getOrder",
        signature.replace(
            "example.orders.api.OrderService#",
            "example.orders.provider.OrderServiceProvider#",
            1,
        ),
        "src/main/java/example/orders/provider/OrderServiceProvider.java",
        (evidence.id,),
    )
    operation = ServiceOperation(
        PROVIDER_REPO,
        PROVIDER_REPO,
        PROVIDER_REPO,
        revision,
        generation_id,
        "provider",
        "example.orders.api.OrderService",
        "getOrder",
        signature,
        (evidence.id,),
        group,
        version,
        alias,
        binding_identity,
    )
    binding = OperationBinding(
        PROVIDER_REPO,
        PROVIDER_REPO,
        PROVIDER_REPO,
        revision,
        generation_id,
        f"dubbo-operation:{signature}|group={group}|version={version}|alias={alias or ''}",
        operation.id,
        implementation.id,
        (evidence.id,),
    )
    return operation, binding, implementation


def _detected_provider_facts(
    tmp_path: Path, signature: str
) -> tuple[ServiceOperation, OperationBinding, ImplementationMethod]:
    provider_root = tmp_path / PROVIDER_REPO
    shutil.copytree(FIXTURE_ROOT / PROVIDER_REPO, provider_root)
    # The provider fixture intentionally excludes API sources; stage them only so the detector can extract bindings.
    shutil.copytree(
        FIXTURE_ROOT / CONTRACT_REPO / "src/main/java/example/orders/api",
        provider_root / "src/main/java/example/orders/api",
    )
    revision = _revision(PROVIDER_REPO)
    facts = DubboMethodDetector().detect_methods(
        RepositorySnapshot(PROVIDER_REPO, revision, provider_root, frozenset({"java"})),
        MethodDetectionContext(PROVIDER_REPO, PROVIDER_REPO, PROVIDER_REPO, revision, GENERATION_ID),
    )
    operation = next(item for item in facts.operations if item.canonical_signature == signature)
    binding = next(item for item in facts.bindings if item.operation_id == operation.id)
    implementation = next(item for item in facts.implementations if item.id == binding.implementation_id)
    return operation, binding, implementation


def _authorized_provider() -> frozenset[AuthorizedProviderSource]:
    return frozenset({AuthorizedProviderSource(PROVIDER_REPO, PROVIDER_REPO, _revision(PROVIDER_REPO))})


def _matches_dubbo_identity(resolution, operation: ServiceOperation, binding: OperationBinding) -> bool:
    return (
        operation.group == "orders"
        and operation.version == "1.0"
        and operation.alias is None
        and binding.provider_endpoint_reference
        == f"dubbo-operation:{operation.canonical_signature}|group=orders|version=1.0|alias="
    )


@pytest.mark.unit
def test_d1_provider_binding_accepts_detector_implementation_with_class_signature(tmp_path: Path) -> None:
    resolution = _resolution(20)
    assert resolution.contract_method is not None
    operation, binding, implementation = _detected_provider_facts(
        tmp_path, resolution.contract_method.canonical_signature
    )

    result = ProviderMethodBinder().bind(
        resolution,
        (operation,),
        (binding,),
        (implementation,),
        GENERATION_ID,
        _authorized_provider(),
        _matches_dubbo_identity,
    )

    assert result.outcome is ProviderMethodBindingOutcome.DETERMINED
    assert result.provider_operation == operation
    assert result.binding == binding
    assert result.implementation == implementation
    assert implementation.class_fqcn == "example.orders.provider.OrderServiceProvider"
    assert implementation.canonical_signature != resolution.contract_method.canonical_signature


@pytest.mark.unit
def test_d1_missing_provider_fails_closed() -> None:
    result = ProviderMethodBinder().bind(
        _resolution(20), (), (), (), GENERATION_ID, _authorized_provider(), _matches_dubbo_identity
    )

    assert result.outcome is ProviderMethodBindingOutcome.PROVIDER_MISSING
    assert result.provider_operation is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("generation_id", "authorized_sources"),
    (
        ("stale-generation", _authorized_provider()),
        (GENERATION_ID, frozenset()),
    ),
)
def test_d1_provider_facts_must_be_current_and_authorized(
    generation_id: str, authorized_sources: frozenset[AuthorizedProviderSource]
) -> None:
    resolution = _resolution(20)
    assert resolution.contract_method is not None
    operation, binding, implementation = _provider_facts(
        resolution.contract_method.canonical_signature, generation_id=generation_id
    )

    result = ProviderMethodBinder().bind(
        resolution,
        (operation,),
        (binding,),
        (implementation,),
        GENERATION_ID,
        authorized_sources,
        _matches_dubbo_identity,
    )

    assert result.outcome is ProviderMethodBindingOutcome.PROVIDER_MISSING


@pytest.mark.unit
def test_d1_two_indistinguishable_providers_fail_closed_as_ambiguous() -> None:
    resolution = _resolution(20)
    assert resolution.contract_method is not None
    first = _provider_facts(resolution.contract_method.canonical_signature)
    second = _provider_facts(resolution.contract_method.canonical_signature, binding_identity="orders-service")
    second_operation = ServiceOperation(
        "second-provider",
        "second-provider",
        "second-provider",
        "second-revision",
        GENERATION_ID,
        "provider",
        first[0].declaring_interface_fqcn,
        first[0].operation_name,
        first[0].canonical_signature,
        first[0].evidence_ids,
        first[0].group,
        first[0].version,
        first[0].alias,
        first[0].binding_identity,
    )
    second_implementation = ImplementationMethod(
        "second-provider",
        "second-provider",
        "second-provider",
        "second-revision",
        GENERATION_ID,
        first[2].class_fqcn,
        first[2].method_name,
        first[2].canonical_signature,
        first[2].file_path,
        first[2].evidence_ids,
    )
    second_binding = OperationBinding(
        "second-provider",
        "second-provider",
        "second-provider",
        "second-revision",
        GENERATION_ID,
        second[1].provider_endpoint_reference,
        second_operation.id,
        second_implementation.id,
        second[1].evidence_ids,
    )
    authorized = _authorized_provider() | frozenset(
        {AuthorizedProviderSource("second-provider", "second-provider", "second-revision")}
    )

    result = ProviderMethodBinder().bind(
        resolution,
        (first[0], second_operation),
        (first[1], second_binding),
        (first[2], second_implementation),
        GENERATION_ID,
        authorized,
        _matches_dubbo_identity,
    )

    assert result.outcome is ProviderMethodBindingOutcome.PROVIDER_AMBIGUOUS


@pytest.mark.unit
def test_d1_provider_metadata_mismatch_fails_closed() -> None:
    resolution = _resolution(20)
    assert resolution.contract_method is not None
    operation, binding, implementation = _provider_facts(resolution.contract_method.canonical_signature, version="2.0")

    result = ProviderMethodBinder().bind(
        resolution,
        (operation,),
        (binding,),
        (implementation,),
        GENERATION_ID,
        _authorized_provider(),
        _matches_dubbo_identity,
    )

    assert result.outcome is ProviderMethodBindingOutcome.PROVIDER_IDENTITY_MISMATCH


@pytest.mark.unit
def test_d1_provider_identity_compares_retained_consumer_protocol_settings() -> None:
    resolution = _resolution(44)
    assert resolution.contract_method is not None
    operation, binding, implementation = _provider_facts(resolution.contract_method.canonical_signature)

    result = ProviderMethodBinder().bind(
        resolution,
        (operation,),
        (binding,),
        (implementation,),
        GENERATION_ID,
        _authorized_provider(),
        lambda candidate, provider, candidate_binding: (
            dict(candidate.protocol_metadata.settings).get("group") == provider.group
            and dict(candidate.protocol_metadata.settings).get("version") == provider.version
            and dict(candidate.protocol_metadata.settings).get("alias") == provider.alias
            and candidate_binding.provider_endpoint_reference
            == f"dubbo-operation:{provider.canonical_signature}|group={provider.group or ''}"
            f"|version={provider.version or ''}|alias={provider.alias or ''}"
        ),
    )

    assert result.outcome is ProviderMethodBindingOutcome.PROVIDER_IDENTITY_MISMATCH


@pytest.mark.unit
def test_d1_binding_without_current_implementation_evidence_fails_closed() -> None:
    resolution = _resolution(20)
    assert resolution.contract_method is not None
    operation, binding, _ = _provider_facts(resolution.contract_method.canonical_signature)

    result = ProviderMethodBinder().bind(
        resolution,
        (operation,),
        (binding,),
        (),
        GENERATION_ID,
        _authorized_provider(),
        _matches_dubbo_identity,
    )

    assert result.outcome is ProviderMethodBindingOutcome.IMPLEMENTATION_MISSING


@pytest.mark.unit
def test_d1_contract_only_result_does_not_treat_api_source_as_provider() -> None:
    result = ProviderMethodBinder().bind(
        _resolution(20),
        (),
        (),
        (),
        GENERATION_ID,
        frozenset({AuthorizedProviderSource(CONTRACT_REPO, CONTRACT_REPO, _revision(CONTRACT_REPO))}),
        _matches_dubbo_identity,
    )

    assert result.outcome is ProviderMethodBindingOutcome.PROVIDER_MISSING


@pytest.mark.unit
def test_d1_a14_distinct_callers_remain_distinct_through_provider_binding() -> None:
    resolutions = (_resolution(52), _resolution(53))
    assert all(item.contract_method is not None for item in resolutions)
    operation, binding, implementation = _provider_facts(resolutions[0].contract_method.canonical_signature)

    results = tuple(
        ProviderMethodBinder().bind(
            resolution,
            (operation,),
            (binding,),
            (implementation,),
            GENERATION_ID,
            _authorized_provider(),
            _matches_dubbo_identity,
        )
        for resolution in resolutions
    )

    assert {result.caller.retained_call_id for result in results} == {
        resolution.retained_call_id for resolution in resolutions
    }
    assert len({result.caller.retained_call_id for result in results}) == 2
