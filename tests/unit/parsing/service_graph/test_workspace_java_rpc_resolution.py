from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

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
    MethodFacts,
    OperationBinding,
    ServiceOperation,
)
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.provider_method_binder import AuthorizedProviderSource
from ontoagent.parsing.service_graph.workspace_java_rpc_resolution import (
    FrozenSourceIdentity,
    WorkspaceJavaRpcResolutionAssembler,
    WorkspaceJavaRpcResolutionInput,
)

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "java_rpc_contracts"
GENERATION_ID = "workspace-generation"
CONSUMER = "sample-checkout-consumer"
CONTRACT = "sample-order-contract"
PROVIDER = "sample-order-provider"


def _manifest() -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / "expected.json").read_text(encoding="utf-8"))


def _revision(repo_id: str) -> str:
    repositories = _manifest()["repositories"]
    assert isinstance(repositories, list)
    item = next(repository for repository in repositories if repository["repo_id"] == repo_id)
    revision = item["revision"]
    assert isinstance(revision, str)
    return revision


def _facts(repo_id: str, source_revision: str | None = None, generation_id: str = GENERATION_ID) -> MethodFacts:
    revision = source_revision or _revision(repo_id)
    return DubboMethodDetector().detect_methods(
        RepositorySnapshot(repo_id, revision, FIXTURE_ROOT / repo_id, frozenset({"java"})),
        MethodDetectionContext(repo_id, repo_id, repo_id, revision, generation_id),
    )


def _source_pinned_provider_method_facts(
    source_revision: str | None = None, generation_id: str = GENERATION_ID
) -> MethodFacts:
    """Supply provider facts as assembly evidence, not detector E2E evidence.

    D1 provider sources deliberately exclude the API contract. These facts model
    the later authorized-contract-view integration while retaining only provider
    repository, module, and revision identities.
    """
    revision = source_revision or _revision(PROVIDER)
    signature = "example.orders.api.OrderService#getOrder(java.lang.String):example.orders.api.OrderSummary"
    evidence = MethodEvidence(
        PROVIDER,
        PROVIDER,
        PROVIDER,
        revision,
        generation_id,
        "src/main/java/example/orders/provider/OrderServiceProvider.java",
        10,
        10,
        "assembly-evidence",
        "1",
        "provider_method",
        "example.orders.provider.OrderServiceProvider#getOrder(java.lang.String):example.orders.api.OrderSummary",
        1.0,
    )
    implementation = ImplementationMethod(
        PROVIDER,
        PROVIDER,
        PROVIDER,
        revision,
        generation_id,
        "example.orders.provider.OrderServiceProvider",
        "getOrder",
        "example.orders.provider.OrderServiceProvider#getOrder(java.lang.String):example.orders.api.OrderSummary",
        "src/main/java/example/orders/provider/OrderServiceProvider.java",
        (evidence.id,),
    )
    operation = ServiceOperation(
        PROVIDER,
        PROVIDER,
        PROVIDER,
        revision,
        generation_id,
        "provider",
        "example.orders.api.OrderService",
        "getOrder",
        signature,
        (evidence.id,),
        "orders",
        "1.0",
        None,
        "orders-service",
    )
    binding = OperationBinding(
        PROVIDER,
        PROVIDER,
        PROVIDER,
        revision,
        generation_id,
        f"dubbo-operation:{signature}|group=orders|version=1.0|alias=",
        operation.id,
        implementation.id,
        (evidence.id,),
    )
    return MethodFacts(
        "assembly-evidence",
        "1",
        PROVIDER,
        revision,
        generation_id,
        (operation,),
        (implementation,),
        (),
        (binding,),
        (evidence,),
        (),
    )


def _input(
    *,
    facts: tuple[MethodFacts, ...] | None = None,
    mappings: tuple[ContractSourceMapping, ...] | None = None,
    generation_id: str = GENERATION_ID,
    revisions: dict[str, str] | None = None,
) -> WorkspaceJavaRpcResolutionInput:
    revisions = revisions or {repo_id: _revision(repo_id) for repo_id in (CONSUMER, CONTRACT, PROVIDER)}
    consumer_facts = _facts(CONSUMER, revisions[CONSUMER], generation_id)
    provider_facts = _facts(PROVIDER, revisions[PROVIDER], generation_id)
    contract = JavaContractSource(
        CONTRACT, CONTRACT, revisions[CONTRACT], FIXTURE_ROOT / CONTRACT, ContractSourceRole.API
    )
    index = JavaContractIndex().build((contract,))
    all_facts = facts or (consumer_facts, provider_facts)
    retained = next(call for call in consumer_facts.retained_source_calls if call.start_line == 20)
    all_mappings = (
        mappings
        if mappings is not None
        else (
            ContractSourceMapping(
                retained.repo_id,
                retained.module_id,
                retained.source_revision,
                contract.repo_id,
                contract.module_id,
                contract.source_revision,
                "1.0",
                "workspace-contracts.yaml",
                1,
                1,
            ),
        )
    )
    frozen = frozenset(
        FrozenSourceIdentity(repo_id, repo_id, revisions[repo_id]) for repo_id in (CONSUMER, CONTRACT, PROVIDER)
    )
    return WorkspaceJavaRpcResolutionInput(
        generation_id,
        all_facts,
        index,
        all_mappings,
        frozen,
        frozenset({AuthorizedProviderSource(PROVIDER, PROVIDER, revisions[PROVIDER])}),
        lambda resolution, operation, binding: (
            operation.group == "orders"
            and operation.version == "1.0"
            and operation.alias is None
            and binding.provider_endpoint_reference.endswith("|group=orders|version=1.0|alias=")
        ),
    )


@pytest.mark.unit
def test_a01_assembly_evidence_is_determined_and_order_independent() -> None:
    """Assembly-level evidence only; provider detector closure is not exercised."""
    consumer_facts = _facts(CONSUMER)
    provider_facts = _source_pinned_provider_method_facts()

    result = WorkspaceJavaRpcResolutionAssembler().resolve(_input(facts=(consumer_facts, provider_facts)))
    reversed_result = WorkspaceJavaRpcResolutionAssembler().resolve(_input(facts=(provider_facts, consumer_facts)))

    assert result == reversed_result
    determined = next(item for item in result.calls if item.retained_call.start_line == 20)
    assert determined.binding is not None
    assert determined.binding.provider_operation is not None
    assert determined.binding.implementation is not None


@pytest.mark.unit
def test_d1_provider_detector_without_authorized_contract_view_emits_no_provider_facts() -> None:
    """The unmodified provider fixture cannot close facts until contract-view integration exists."""
    provider_facts = _facts(PROVIDER)

    assert provider_facts.operations == ()
    assert provider_facts.bindings == ()
    assert {item.reason_code for item in provider_facts.unresolved} == {"MISSING_DECLARATION"}


@pytest.mark.unit
def test_contract_only_and_unresolved_cases_remain_stage_structured() -> None:
    result = WorkspaceJavaRpcResolutionAssembler().resolve(_input())

    by_line = {item.retained_call.start_line: item for item in result.calls}
    assert by_line[20].contract.outcome.value == "determined"
    assert by_line[20].binding is not None
    assert by_line[20].binding.outcome.value == "PROVIDER_MISSING"
    assert by_line[36].contract.outcome.value == "VERSION_CONFLICT"
    assert by_line[48].contract.outcome.value == "DYNAMIC_TARGET"
    assert by_line[48].binding is None


@pytest.mark.unit
def test_a14_keeps_distinct_retained_call_ids() -> None:
    result = WorkspaceJavaRpcResolutionAssembler().resolve(_input())

    callers = tuple(item for item in result.calls if item.retained_call.method_name == "cancelOrder")

    assert len(callers) == 2
    assert len({item.retained_call_id for item in callers}) == 2


@pytest.mark.unit
def test_a15_contract_source_revision_change_reresolves_unchanged_consumer_in_new_generation() -> None:
    """A contract revision is an explicit source-pinned input to each generation."""
    first_generation = "generation-contract-v1"
    second_generation = "generation-contract-v2"
    first_revisions = {repo_id: _revision(repo_id) for repo_id in (CONSUMER, CONTRACT, PROVIDER)}
    second_revisions = {**first_revisions, CONTRACT: "contract-revision-v2"}

    first = WorkspaceJavaRpcResolutionAssembler().resolve(
        _input(
            facts=(
                _facts(CONSUMER, first_revisions[CONSUMER], first_generation),
                _source_pinned_provider_method_facts(first_revisions[PROVIDER], first_generation),
            ),
            generation_id=first_generation,
            revisions=first_revisions,
        )
    )
    second = WorkspaceJavaRpcResolutionAssembler().resolve(
        _input(
            facts=(
                _facts(CONSUMER, second_revisions[CONSUMER], second_generation),
                _source_pinned_provider_method_facts(second_revisions[PROVIDER], second_generation),
            ),
            generation_id=second_generation,
            revisions=second_revisions,
        )
    )

    first_call = next(item for item in first.determined if item.retained_call.start_line == 20)
    second_call = next(item for item in second.determined if item.retained_call.start_line == 20)
    assert first_call.retained_call.source_revision == second_call.retained_call.source_revision
    assert first_call.contract.contract_method is not None
    assert second_call.contract.contract_method is not None
    assert first_call.contract.contract_method.source.source_revision == first_revisions[CONTRACT]
    assert second_call.contract.contract_method.source.source_revision == second_revisions[CONTRACT]
    assert first_call.binding is not None
    assert second_call.binding is not None
    assert first_call.binding.provider_operation is not None
    assert second_call.binding.provider_operation is not None
    assert first_call.binding.provider_operation.generation_id == first_generation
    assert second_call.binding.provider_operation.generation_id == second_generation


@pytest.mark.unit
def test_a16_provider_revision_with_no_current_binding_is_unresolved_without_reusing_old_provider_facts() -> None:
    """Provider bindings must be recomputed from the current source-pinned facts."""
    first_generation = "generation-provider-v1"
    second_generation = "generation-provider-v2"
    first_revisions = {repo_id: _revision(repo_id) for repo_id in (CONSUMER, CONTRACT, PROVIDER)}
    second_revisions = {**first_revisions, PROVIDER: "provider-revision-v2"}

    first = WorkspaceJavaRpcResolutionAssembler().resolve(
        _input(
            facts=(
                _facts(CONSUMER, first_revisions[CONSUMER], first_generation),
                _source_pinned_provider_method_facts(first_revisions[PROVIDER], first_generation),
            ),
            generation_id=first_generation,
            revisions=first_revisions,
        )
    )
    second = WorkspaceJavaRpcResolutionAssembler().resolve(
        _input(
            facts=(
                _facts(CONSUMER, second_revisions[CONSUMER], second_generation),
                _facts(PROVIDER, second_revisions[PROVIDER], second_generation),
            ),
            generation_id=second_generation,
            revisions=second_revisions,
        )
    )

    first_call = next(item for item in first.calls if item.retained_call.start_line == 20)
    assert first_call.binding is not None
    assert first_call.binding.outcome.value == "determined"
    second_call = next(item for item in second.calls if item.retained_call.start_line == 20)
    assert second_call.contract.outcome.value == "determined"
    assert second_call.binding is not None
    assert second_call.binding.outcome.value == "PROVIDER_MISSING"
    assert second_call.binding.provider_operation is None


@pytest.mark.unit
def test_rejects_mapping_outside_frozen_identities() -> None:
    invalid = ContractSourceMapping(
        CONSUMER,
        CONSUMER,
        _revision(CONSUMER),
        "unfrozen-contract",
        "unfrozen-contract",
        "unfrozen-revision",
        "1.0",
        "workspace-contracts.yaml",
        1,
        1,
    )

    with pytest.raises(ValueError, match="frozen"):
        WorkspaceJavaRpcResolutionAssembler().resolve(_input(mappings=(invalid,)))


@pytest.mark.unit
def test_no_mapping_and_contract_source_provider_authorization_fail_closed() -> None:
    no_mapping = WorkspaceJavaRpcResolutionAssembler().resolve(_input(mappings=()))
    contract_provider = replace(
        _input(),
        authorized_provider_sources=frozenset({AuthorizedProviderSource(CONTRACT, CONTRACT, _revision(CONTRACT))}),
    )

    assert next(item for item in no_mapping.calls if item.retained_call.start_line == 20).contract.outcome.value == (
        "CONTRACT_MISSING"
    )
    with pytest.raises(ValueError, match="cannot be authorized providers"):
        WorkspaceJavaRpcResolutionAssembler().resolve(contract_provider)
