"""Deterministic public evaluation for source-only Java/Dubbo resolution."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ontoagent.parsing.service_graph.contract_method_resolver import ContractMethodResolution
from ontoagent.parsing.service_graph.detector_sdk import MethodDetectionContext
from ontoagent.parsing.service_graph.detectors.dubbo_method import DubboMethodDetector
from ontoagent.parsing.service_graph.java_contract_index import (
    ContractSourceMapping,
    ContractSourceRole,
    JavaContractSource,
)
from ontoagent.parsing.service_graph.methods import OperationBinding, ServiceOperation
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.provider_method_binder import (
    AuthorizedProviderSource,
    ProviderMethodBindingOutcome,
)
from ontoagent.parsing.service_graph.workspace_java_rpc_resolution import (
    FrozenSourceIdentity,
    WorkspaceJavaRpcAuthorization,
    WorkspaceJavaRpcCallResolution,
    WorkspaceJavaRpcResolutionAssembler,
    WorkspaceJavaRpcResolutionInput,
    prepare_authorized_contract_views,
)

NOT_APPLICABLE = "N/A"
GENERATION_ID = "java-rpc-public-eval"


@dataclass(frozen=True)
class JavaRpcEvaluationMetrics:
    capture_rate: float | str
    supported_call_resolution_recall: float | str
    determined_edge_precision: float | str
    reason_distribution: dict[str, int]


@dataclass(frozen=True)
class JavaRpcCallReport:
    case_id: str
    status: str
    actual_outcome: str
    reason: str | None
    determined_chain: bool


@dataclass(frozen=True)
class JavaRpcEvaluationReport:
    outcome: str
    exit_code: int
    metrics: JavaRpcEvaluationMetrics
    calls: tuple[JavaRpcCallReport, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON-ready public evaluation report."""
        return {
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "metrics": asdict(self.metrics),
            "calls": [asdict(item) for item in self.calls],
        }


def load_manifest(path: Path) -> dict[str, Any]:
    """Load the checked-in public fixture manifest."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("repositories"), list)
        or not isinstance(value.get("cases"), dict)
    ):
        raise ValueError("expected Java/RPC manifest with repositories and cases")
    return value


def evaluate(manifest: Mapping[str, Any], fixture_root: Path) -> JavaRpcEvaluationReport:
    """Evaluate real detector, authorized view, and workspace resolution without persistence."""
    result, indexed_contracts = _resolve(manifest, fixture_root)
    reports = tuple(
        _evaluate_case(case_id, case, result.calls, indexed_contracts) for case_id, case in manifest["cases"].items()
    )
    metrics = _metrics(reports)
    failed = any(report.status != "passed" for report in reports)
    return JavaRpcEvaluationReport("failed" if failed else "passed", 1 if failed else 0, metrics, reports)


def _resolve(manifest: Mapping[str, Any], fixture_root: Path):
    repositories = _repositories(manifest, fixture_root)
    identities = frozenset(
        FrozenSourceIdentity(snapshot.repo_id, snapshot.repo_id, snapshot.source_revision)
        for snapshot in repositories.values()
    )
    authorization = _authorization(repositories)
    index, views = prepare_authorized_contract_views(authorization, repositories, identities)
    facts = tuple(
        DubboMethodDetector().detect_methods(
            snapshot,
            MethodDetectionContext(
                snapshot.repo_id,
                snapshot.repo_id,
                snapshot.repo_id,
                snapshot.source_revision,
                GENERATION_ID,
                views[(snapshot.repo_id, snapshot.repo_id, snapshot.source_revision)],
            ),
        )
        for snapshot in repositories.values()
    )
    resolution = WorkspaceJavaRpcResolutionAssembler().resolve(
        WorkspaceJavaRpcResolutionInput(
            GENERATION_ID,
            facts,
            index,
            authorization.contract_mappings,
            identities,
            authorization.authorized_provider_sources,
            authorization.matches_provider_identity,
        )
    )
    return resolution, index.contracts


def _repositories(manifest: Mapping[str, Any], fixture_root: Path) -> dict[tuple[str, str, str], RepositorySnapshot]:
    snapshots: dict[tuple[str, str, str], RepositorySnapshot] = {}
    for item in manifest["repositories"]:
        repo_id = item["repo_id"]
        revision = item["revision"]
        fixture_path = item.get("fixture_path", repo_id)
        if not all(isinstance(value, str) and value for value in (repo_id, revision, fixture_path)):
            raise ValueError("repository entries must provide nonblank IDs and revisions")
        snapshot = RepositorySnapshot(repo_id, revision, fixture_root / fixture_path, frozenset({"java"}))
        snapshots[(repo_id, repo_id, revision)] = snapshot
    return dict(sorted(snapshots.items()))


def _authorization(
    repositories: Mapping[tuple[str, str, str], RepositorySnapshot],
) -> WorkspaceJavaRpcAuthorization:
    by_repo = {snapshot.repo_id: snapshot for snapshot in repositories.values()}
    contract_sources = tuple(
        JavaContractSource(snapshot.repo_id, snapshot.repo_id, snapshot.source_revision, snapshot.root_path, role)
        for snapshot, role in (
            (by_repo["sample-order-contract"], ContractSourceRole.API),
            (by_repo["provider-client-module-client"], ContractSourceRole.CLIENT_MODULE),
        )
    )
    mappings = (
        _mapping(by_repo["sample-order-provider"], by_repo["sample-order-contract"], "1.0"),
        _mapping(by_repo["sample-checkout-consumer"], by_repo["sample-order-contract"], "1.0"),
        _mapping(by_repo["provider-client-module-provider"], by_repo["provider-client-module-client"], "2.0"),
        _mapping(by_repo["provider-client-module-client"], by_repo["provider-client-module-client"], "2.0"),
    )
    providers = frozenset(
        AuthorizedProviderSource(snapshot.repo_id, snapshot.repo_id, snapshot.source_revision)
        for snapshot in by_repo.values()
        if snapshot.repo_id.endswith("provider")
    )
    return WorkspaceJavaRpcAuthorization(contract_sources, mappings, providers, _matches_provider_identity)


def _mapping(consumer: RepositorySnapshot, contract: RepositorySnapshot, version: str) -> ContractSourceMapping:
    return ContractSourceMapping(
        consumer.repo_id,
        consumer.repo_id,
        consumer.source_revision,
        contract.repo_id,
        contract.repo_id,
        contract.source_revision,
        version,
        "expected.json",
        1,
        1,
    )


def _matches_provider_identity(
    resolution: ContractMethodResolution, operation: ServiceOperation, binding: OperationBinding
) -> bool:
    settings = dict(resolution.protocol_metadata.settings)
    return (
        settings.get("group") == operation.group
        and settings.get("version") == operation.version
        and settings.get("alias") == operation.alias
        and binding.provider_endpoint_reference.startswith(
            f"dubbo-operation:{operation.canonical_signature}|group={operation.group or ''}"
            f"|version={operation.version or ''}|alias={operation.alias or ''}"
        )
    )


def _evaluate_case(
    case_id: str,
    case: Mapping[str, Any],
    calls: tuple[WorkspaceJavaRpcCallResolution, ...],
    contracts: Mapping[str, object],
) -> JavaRpcCallReport:
    expected = case["expected_outcome"]
    source = case["source"]
    matching = [
        item
        for item in calls
        if item.retained_call.repo_id == source["repo_id"]
        and item.retained_call.file_path == source["path"]
        and item.retained_call.start_line == source["line"]
    ]
    if expected == "contract_only":
        signature = case["canonical_signature"]
        found = any(
            signature in {method.canonical_signature for method in contract.methods} for contract in contracts.values()
        )
        return JavaRpcCallReport(case_id, "passed" if found else "failed", "contract_only", None, False)
    if case_id == "A03":
        determined = any(
            item.binding is not None
            and item.binding.outcome is ProviderMethodBindingOutcome.DETERMINED
            and item.binding.implementation is not None
            and item.binding.implementation.repo_id == source["repo_id"]
            and item.binding.implementation.file_path == source["path"]
            for item in calls
        )
        return JavaRpcCallReport(
            case_id,
            "passed" if determined else "failed",
            "determined" if determined else "unresolved",
            None,
            determined,
        )
    if case_id == "A14":
        lines = set(case["distinct_source_lines"])
        matching = [
            item
            for item in calls
            if item.retained_call.repo_id == source["repo_id"] and item.retained_call.start_line in lines
        ]
    if not matching:
        return JavaRpcCallReport(case_id, "failed", "not_captured", "NOT_CAPTURED", False)
    if expected == "determined":
        determined = all(_determined_chain(item) for item in matching)
        return JavaRpcCallReport(
            case_id,
            "passed" if determined else "failed",
            "determined" if determined else "unresolved",
            None,
            determined,
        )
    determined = _determined_chain(matching[0])
    reason = _reason(matching[0])
    return JavaRpcCallReport(
        case_id,
        "passed" if not determined and reason == case["reason_code"] else "failed",
        "determined" if determined else "unresolved",
        reason,
        determined,
    )


def _determined_chain(item: WorkspaceJavaRpcCallResolution) -> bool:
    return (
        item.binding is not None
        and item.binding.outcome is ProviderMethodBindingOutcome.DETERMINED
        and item.binding.provider_operation is not None
        and item.binding.implementation is not None
    )


def _reason(item: WorkspaceJavaRpcCallResolution) -> str:
    if item.binding is not None:
        if item.binding.outcome is ProviderMethodBindingOutcome.PROVIDER_AMBIGUOUS:
            return "AMBIGUOUS_TARGET"
        if item.binding.outcome is ProviderMethodBindingOutcome.IMPLEMENTATION_MISSING:
            return "MISSING_IMPLEMENTATION"
        if item.binding.outcome is not ProviderMethodBindingOutcome.DETERMINED:
            return item.binding.outcome.value
        return "DETERMINED"
    return item.contract.outcome.value


def _metrics(reports: tuple[JavaRpcCallReport, ...]) -> JavaRpcEvaluationMetrics:
    supported = tuple(report for report in reports if report.case_id not in {"A02", "A03"})
    expected_determined = tuple(
        report for report in reports if report.case_id in {"A01", "A03", "A04", "A05", "A08", "A14"}
    )
    determined_reports = tuple(report for report in reports if report.determined_chain)
    captured = sum(report.actual_outcome != "not_captured" for report in supported)
    resolved = sum(report.determined_chain for report in expected_determined)
    return JavaRpcEvaluationMetrics(
        _ratio(captured, len(supported)),
        _ratio(resolved, len(expected_determined)),
        _ratio(resolved, len(determined_reports)),
        dict(
            sorted(
                Counter(
                    report.reason
                    for report in reports
                    if report.reason is not None and report.reason not in {"DETERMINED", "NOT_CAPTURED"}
                ).items()
            )
        ),
    )


def _ratio(numerator: int, denominator: int) -> float | str:
    return NOT_APPLICABLE if denominator == 0 else numerator / denominator
