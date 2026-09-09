from __future__ import annotations

import json
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.java_contract_index import (
    ContractSourceMapping,
    ContractSourceRole,
    JavaContractIndex,
    JavaContractSource,
    JavaContractVisibilityStatus,
)

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "java_rpc_contracts"


def _manifest() -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / "expected.json").read_text(encoding="utf-8"))


def _source(manifest: dict[str, object], repo_id: str, role: ContractSourceRole) -> JavaContractSource:
    repositories = manifest["repositories"]
    assert isinstance(repositories, list)
    repository = next(item for item in repositories if item["repo_id"] == repo_id)
    fixture_path = repository.get("fixture_path", repo_id)
    assert isinstance(fixture_path, str)
    revision = repository["revision"]
    assert isinstance(revision, str)
    return JavaContractSource(repo_id, repo_id, revision, FIXTURE_ROOT / fixture_path, role)


def _mapping(
    consumer_repo_id: str,
    source: JavaContractSource,
    version: str = "1.0",
    consumer_source_revision: str | None = None,
) -> ContractSourceMapping:
    manifest = _manifest()
    repositories = manifest["repositories"]
    assert isinstance(repositories, list)
    consumer = next(item for item in repositories if item["repo_id"] == consumer_repo_id)
    manifest_consumer_revision = consumer["revision"]
    assert isinstance(manifest_consumer_revision, str)
    revision = consumer_source_revision or manifest_consumer_revision
    return ContractSourceMapping(
        consumer_repo_id,
        consumer_repo_id,
        revision,
        source.repo_id,
        source.module_id,
        source.source_revision,
        version,
        "workspace-contracts.yaml",
        4,
        4,
    )


@pytest.mark.unit
def test_a01_contract_index_extracts_api_contract_with_exact_source_pinned_signatures() -> None:
    manifest = _manifest()
    api_source = _source(manifest, "sample-order-contract", ContractSourceRole.API)

    result = JavaContractIndex().build((api_source,))

    assert result.conflicts == ()
    contract = result.contracts["example.orders.api.OrderService"]
    assert contract.source.role is ContractSourceRole.API
    assert not contract.source.is_provider
    assert [method.canonical_signature for method in contract.methods] == [
        "example.orders.api.OrderService#cancelOrder(java.lang.String):void",
        "example.orders.api.OrderService#getOrder(java.lang.String):example.orders.api.OrderSummary",
        "example.orders.api.OrderService#getOrder(long):example.orders.api.OrderSummary",
    ]
    assert all(method.source.repo_id == "sample-order-contract" for method in contract.methods)
    assert all(method.source.file_path.endswith("OrderService.java") for method in contract.methods)


@pytest.mark.unit
def test_a02_contract_index_extracts_client_module_contract_without_treating_it_as_provider() -> None:
    manifest = _manifest()
    client_source = _source(manifest, "provider-client-module-client", ContractSourceRole.CLIENT_MODULE)

    result = JavaContractIndex().build((client_source,))

    contract = result.contracts["example.inventory.api.InventoryService"]
    assert contract.source.role is ContractSourceRole.CLIENT_MODULE
    assert not contract.source.is_provider
    assert [method.canonical_signature for method in contract.methods] == [
        "example.inventory.api.InventoryService#reserve(java.lang.String):java.lang.String"
    ]


@pytest.mark.unit
def test_a03_contract_index_is_order_deterministic_and_deduplicates_method_identity_across_sources() -> None:
    manifest = _manifest()
    api_source = _source(manifest, "sample-order-contract", ContractSourceRole.API)
    duplicate_source = JavaContractSource(
        "sample-order-contract-copy",
        "sample-order-contract-copy",
        "copy-revision",
        api_source.root_path,
        ContractSourceRole.API,
    )

    forward = JavaContractIndex().build((api_source, duplicate_source))
    reverse = JavaContractIndex().build((duplicate_source, api_source))

    assert forward == reverse
    contract = forward.contracts["example.orders.api.OrderService"]
    assert len(contract.methods) == 3
    assert len(contract.methods[0].sources) == 2


@pytest.mark.unit
def test_a09_visibility_requires_explicit_source_pinned_mapping_and_reports_conflict(tmp_path: Path) -> None:
    manifest = _manifest()
    api_source = _source(manifest, "sample-order-contract", ContractSourceRole.API)
    client_source = _source(manifest, "provider-client-module-client", ContractSourceRole.CLIENT_MODULE)
    index = JavaContractIndex()
    result = index.build((api_source, client_source))

    consumer_revision = "3333333333333333333333333333333333333333"
    no_evidence = index.visible_contracts(
        result,
        "sample-checkout-consumer",
        "sample-checkout-consumer",
        consumer_revision,
        "1.0",
    )
    assert no_evidence.status is JavaContractVisibilityStatus.NO_EVIDENCE
    assert no_evidence.contracts == ()

    with pytest.raises(ValueError, match="consumer_source_revision must be nonblank"):
        index.visible_contracts(
            result,
            "sample-checkout-consumer",
            "sample-checkout-consumer",
            " ",
            "1.0",
        )

    visible = index.visible_contracts(
        result,
        "sample-checkout-consumer",
        "sample-checkout-consumer",
        consumer_revision,
        "1.0",
        (_mapping("sample-checkout-consumer", api_source),),
    )
    assert visible.status is JavaContractVisibilityStatus.VISIBLE
    assert [contract.fqcn for contract in visible.contracts] == ["example.orders.api.OrderService"]

    version_conflict = index.visible_contracts(
        result,
        "sample-checkout-consumer",
        "sample-checkout-consumer",
        consumer_revision,
        "2.0",
        (_mapping("sample-checkout-consumer", api_source),),
        "example.orders.api.OrderService",
    )
    assert version_conflict.status is JavaContractVisibilityStatus.VERSION_CONFLICT
    assert version_conflict.contracts == ()

    # A09: a competing source pin for the same FQCN with an incompatible contract is never selected by order.
    conflict_file = tmp_path / "src/main/java/example/orders/api/OrderService.java"
    conflict_file.parent.mkdir(parents=True)
    conflict_file.write_text(
        """package example.orders.api;

public interface OrderService {
    String incompatible(String id);
}
""",
        encoding="utf-8",
    )
    conflicting_source = JavaContractSource(
        "sample-order-contract-v2",
        "sample-order-contract-v2",
        "conflicting-revision",
        tmp_path,
        ContractSourceRole.API,
    )
    conflicted = index.build((api_source, conflicting_source))
    conflicted_in_reverse = index.build((conflicting_source, api_source))
    assert [item.fqcn for item in conflicted.conflicts] == ["example.orders.api.OrderService"]
    assert conflicted.contracts == {}
    assert conflicted_in_reverse == conflicted

    another_revision = "another-consumer-revision"
    stale_mapping = _mapping(
        "sample-checkout-consumer",
        api_source,
        consumer_source_revision=another_revision,
    )
    stale_visibility = index.visible_contracts(
        result,
        "sample-checkout-consumer",
        "sample-checkout-consumer",
        consumer_revision,
        "1.0",
        (stale_mapping,),
    )
    assert stale_visibility.status is JavaContractVisibilityStatus.NO_EVIDENCE
    assert stale_visibility.contracts == ()

    conflict_visibility = index.visible_contracts(
        conflicted,
        "sample-checkout-consumer",
        "sample-checkout-consumer",
        consumer_revision,
        "1.0",
        (_mapping("sample-checkout-consumer", conflicting_source),),
        "example.orders.api.OrderService",
    )
    assert conflict_visibility.status is JavaContractVisibilityStatus.CONTRACT_CONFLICT
    assert conflict_visibility.contracts == ()
    assert [item.fqcn for item in conflict_visibility.conflicts] == ["example.orders.api.OrderService"]
