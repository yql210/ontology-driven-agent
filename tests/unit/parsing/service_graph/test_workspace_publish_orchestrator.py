from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.detectors.dubbo_method import DubboMethodDetector
from ontoagent.parsing.service_graph.graph_plan import GraphNode, GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import WriteReceipt
from ontoagent.parsing.service_graph.java_contract_index import (
    ContractSourceMapping,
    ContractSourceRole,
    JavaContractSource,
)
from ontoagent.parsing.service_graph.method_graph_writer import MethodGraphScope, MethodGraphWritePlan
from ontoagent.parsing.service_graph.methods import (
    ImplementationMethod,
    MethodEvidence,
    MethodFacts,
    OperationBinding,
    ServiceOperation,
)
from ontoagent.parsing.service_graph.models import DetectorFacts, RepositorySnapshot
from ontoagent.parsing.service_graph.provider_method_binder import AuthorizedProviderSource
from ontoagent.parsing.service_graph.resolver import ResolveResult
from ontoagent.parsing.service_graph.workspace.models import (
    Workspace,
    WorkspaceGeneration,
    WorkspaceGenerationState,
    WorkspacePublishResult,
    WorkspacePublishStatus,
    WorkspaceRepositorySnapshot,
    WorkspaceSourceDescriptor,
    WorkspaceSourceKind,
)
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import (
    WorkspacePublishStatus as OrchestratorStatus,
)
from ontoagent.parsing.service_graph.workspace.publish_orchestrator import (
    WorkspaceServiceGraphPublishComponents,
    WorkspaceServiceGraphPublishInput,
    WorkspaceServiceGraphPublishOrchestrator,
)
from ontoagent.parsing.service_graph.workspace_java_rpc_resolution import (
    FrozenSourceIdentity,
    WorkspaceJavaRpcAuthorization,
    WorkspaceJavaRpcResolutionAssembler,
    WorkspaceJavaRpcResolutionInput,
    prepare_authorized_contract_views,
)


@dataclass
class _Factory:
    components: WorkspaceServiceGraphPublishComponents
    namespaces: list[str]

    def create(self, namespace: str) -> WorkspaceServiceGraphPublishComponents:
        self.namespaces.append(namespace)
        return self.components


class _Registry:
    ids = ("detector-a",)

    def __init__(self, calls: list[str], *, fail: bool = False) -> None:
        self._calls = calls
        self._fail = fail

    def detect(self, snapshot: RepositorySnapshot, detector_id: str | None = None) -> DetectorFacts:
        self._calls.append(f"detect:{snapshot.repo_id}:{detector_id}")
        if self._fail:
            raise RuntimeError("detector failed")
        return DetectorFacts("detector-a", "1", snapshot.repo_id, snapshot.source_revision, (), (), (), ())


class _Resolver:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def resolve(self, batches: tuple[object, ...]) -> ResolveResult:
        self._calls.append("resolve")
        return ResolveResult((), (), ())


class _PlanBuilder:
    def __init__(self, calls: list[str], *, missing_repo: bool = False) -> None:
        self._calls = calls
        self._missing_repo = missing_repo

    def build(self, result: ResolveResult) -> GraphWritePlan:
        self._calls.append("plan")
        repo_ids = ("repo-a", "repo-b") if self._missing_repo else ("repo-a", "repo-b", "repo-c")
        nodes = tuple(
            GraphNode(repo_id, "Endpoint", {"id": repo_id, "repo_id": repo_id, "evidence_ids": (repo_id,)})
            for repo_id in repo_ids
        )
        return GraphWritePlan(nodes, ())


class _Writer:
    def __init__(self, calls: list[str], *, confirmed: bool = True) -> None:
        self._calls = calls
        self._confirmed = confirmed
        self.namespace = ""

    def write(self, plan: GraphWritePlan) -> WriteReceipt:
        self._calls.append("write")
        return WriteReceipt(self._confirmed, len(plan.nodes), len(plan.relations), plan, self.namespace)


class _MethodSink:
    def __init__(self, calls: list[str], scope: MethodGraphScope, *, confirmed: bool = True) -> None:
        self._calls = calls
        self.scope = scope
        self._confirmed = confirmed
        self._plan: MethodGraphWritePlan | None = None

    def write(self, plan: MethodGraphWritePlan) -> None:
        self._calls.append(f"method-write:{plan.scope.namespace}")
        self._plan = plan

    def readback(self, scope: MethodGraphScope) -> MethodGraphWritePlan:
        if not self._confirmed:
            raise ValueError("simulated receipt mismatch")
        assert self._plan is not None
        return self._plan


class _Repository:
    def __init__(
        self, calls: list[str], *, publication: WorkspacePublishStatus = WorkspacePublishStatus.PUBLISHED
    ) -> None:
        self._calls = calls
        self._publication = publication
        self.active = "old-generation"

    def create_workspace(self, workspace: Workspace) -> Workspace:
        self._calls.append("workspace")
        return workspace

    def create_build_task(self, task: object) -> object:
        self._calls.append("task")
        return task

    def create_generation(self, generation: WorkspaceGeneration) -> WorkspaceGeneration:
        self._calls.append("generation")
        return generation

    def advance_generation_state(
        self, generation: WorkspaceGeneration, target: WorkspaceGenerationState
    ) -> WorkspaceGeneration:
        self._calls.append(f"state:{target.value}")
        return generation.transition_to(target)

    def publish_generation(
        self, workspace_id: str, expected_active_generation_id: str | None, candidate_generation_id: str
    ) -> WorkspacePublishResult:
        self._calls.append(f"cas:{expected_active_generation_id}:{candidate_generation_id}")
        if self._publication is WorkspacePublishStatus.PUBLISHED:
            self.active = candidate_generation_id
        return WorkspacePublishResult(self._publication, self.active)


def _input(method_facts: tuple[MethodFacts, ...] = ()) -> WorkspaceServiceGraphPublishInput:
    workspace = Workspace("workspace-1", "Workspace")
    persisted = tuple(
        WorkspaceRepositorySnapshot(
            workspace.workspace_id,
            repo_id,
            "main",
            f"revision-{repo_id}",
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id in ("repo-a", "repo-b", "repo-c")
    )
    runtime = tuple(
        RepositorySnapshot(snapshot.repo_id, snapshot.source_revision, Path("."), frozenset({"java"}))
        for snapshot in persisted
    )
    return WorkspaceServiceGraphPublishInput(
        workspace, persisted, runtime, "request-1", "generation-1", None, (), method_facts
    )


def _method_facts(*, source_revision: str = "revision-repo-a", generation_id: str = "generation-1") -> MethodFacts:
    evidence = MethodEvidence(
        "repo-a",
        "module",
        "service",
        source_revision,
        generation_id,
        "src/Service.java",
        1,
        1,
        "generic-java",
        "1",
        "method",
        "Service.find",
        1.0,
    )
    operation = ServiceOperation(
        "repo-a",
        "module",
        "service",
        source_revision,
        generation_id,
        "provider",
        "example.ServiceApi",
        "find",
        "example.ServiceApi#find():void",
        (evidence.id,),
    )
    implementation = ImplementationMethod(
        "repo-a",
        "module",
        "service",
        source_revision,
        generation_id,
        "example.Service",
        "find",
        "example.Service#find():void",
        "src/Service.java",
        (evidence.id,),
    )
    binding = OperationBinding(
        "repo-a",
        "module",
        "service",
        source_revision,
        generation_id,
        "endpoint-ref",
        operation.id,
        implementation.id,
        (evidence.id,),
    )
    return MethodFacts(
        "generic-java",
        "1",
        "repo-a",
        source_revision,
        generation_id,
        (operation,),
        (implementation,),
        (),
        (binding,),
        (evidence,),
        (),
    )


def _orchestrator(
    calls: list[str],
    *,
    detector_fails: bool = False,
    confirmed: bool = True,
    missing_repo: bool = False,
    method_confirmed: bool = True,
    publication: WorkspacePublishStatus = WorkspacePublishStatus.PUBLISHED,
) -> tuple[WorkspaceServiceGraphPublishOrchestrator, _Factory, _Writer, _Repository]:
    writer = _Writer(calls, confirmed=confirmed)
    repository = _Repository(calls, publication=publication)

    def method_sink_factory(scope: MethodGraphScope) -> _MethodSink:
        return _MethodSink(calls, scope, confirmed=method_confirmed)

    components = WorkspaceServiceGraphPublishComponents(
        _Registry(calls, fail=detector_fails),
        _Resolver(calls),
        _PlanBuilder(calls, missing_repo=missing_repo),
        writer,
        repository,
        method_sink_factory,
    )
    factory = _Factory(components, [])
    return WorkspaceServiceGraphPublishOrchestrator(factory), factory, writer, repository


def test_publish_transitions_frozen_workspace_generation_and_uses_only_workspace_cas() -> None:
    calls: list[str] = []
    orchestrator, factory, writer, _ = _orchestrator(calls)
    candidate = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")
    writer.namespace = candidate

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.ACTIVE
    assert outcome.graph_write_confirmed
    assert outcome.to_dict()["status"] == "active"
    assert factory.namespaces == [candidate]
    assert calls == [
        "workspace",
        "task",
        "generation",
        "state:extracting",
        "detect:repo-a:detector-a",
        "detect:repo-b:detector-a",
        "detect:repo-c:detector-a",
        "state:resolving",
        "resolve",
        "plan",
        "state:writing",
        "write",
        "state:verifying",
        "cas:None:generation-1",
    ]
    assert "manifest" not in " ".join(calls)


def test_publish_detector_failure_marks_generation_failed_without_cas() -> None:
    calls: list[str] = []
    orchestrator, _, writer, _ = _orchestrator(calls, detector_fails=True)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.FAILED
    assert outcome.generation_state is WorkspaceGenerationState.FAILED
    assert not any(call.startswith("cas:") for call in calls)


def test_publish_unconfirmed_readback_marks_generation_failed_without_cas() -> None:
    calls: list[str] = []
    orchestrator, _, writer, _ = _orchestrator(calls, confirmed=False)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.FAILED
    assert outcome.generation_state is WorkspaceGenerationState.FAILED
    assert not any(call.startswith("cas:") for call in calls)


def test_publish_stale_cas_blocks_candidate_and_preserves_old_active_binding() -> None:
    calls: list[str] = []
    orchestrator, _, writer, repository = _orchestrator(calls, publication=WorkspacePublishStatus.STALE_ACTIVE)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.BLOCKED
    assert outcome.generation_state is WorkspaceGenerationState.BLOCKED
    assert repository.active == "old-generation"
    assert calls[-1] == "cas:None:generation-1"


def test_publish_missing_frozen_repository_from_plan_fails_before_write_and_cas() -> None:
    calls: list[str] = []
    orchestrator, _, writer, _ = _orchestrator(calls, missing_repo=True)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.FAILED
    assert outcome.generation_state is WorkspaceGenerationState.FAILED
    assert "write" not in calls
    assert not any(call.startswith("cas:") for call in calls)


def test_publish_defensively_rejects_less_than_three_frozen_repositories_before_factory_or_write() -> None:
    calls: list[str] = []
    orchestrator, factory, _, _ = _orchestrator(calls)
    valid = _input()
    malformed = object.__new__(WorkspaceServiceGraphPublishInput)
    for field_name, value in (
        ("workspace", valid.workspace),
        ("snapshots", valid.snapshots[:2]),
        ("repository_snapshots", valid.repository_snapshots[:2]),
        ("task_idempotency_key", valid.task_idempotency_key),
        ("generation_id", valid.generation_id),
        ("expected_active_generation_id", valid.expected_active_generation_id),
        ("owned_work_dirs", ()),
        ("method_facts", ()),
    ):
        object.__setattr__(malformed, field_name, value)

    with pytest.raises(ValueError, match="at least three unique"):
        orchestrator.publish(malformed)

    assert factory.namespaces == []
    assert calls == []


def test_publish_empty_method_facts_retains_endpoint_only_route() -> None:
    calls: list[str] = []
    orchestrator, _, writer, _ = _orchestrator(calls)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input())

    assert outcome.status is OrchestratorStatus.ACTIVE
    assert not any(call.startswith("method-") for call in calls)
    assert calls.count("cas:None:generation-1") == 1


def test_publish_method_facts_uses_endpoint_namespace_and_single_cas_after_both_receipts() -> None:
    calls: list[str] = []
    orchestrator, _, writer, _ = _orchestrator(calls)
    namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")
    writer.namespace = namespace

    outcome = orchestrator.publish(_input((_method_facts(),)))

    assert outcome.status is OrchestratorStatus.ACTIVE
    assert calls.index("write") < calls.index(f"method-write:{namespace}") < calls.index("state:verifying")
    assert calls.count("cas:None:generation-1") == 1


def test_publish_invalid_method_fact_is_rejected_before_endpoint_write() -> None:
    calls: list[str] = []
    orchestrator, factory, _, _ = _orchestrator(calls)
    valid = _input()
    malformed = object.__new__(WorkspaceServiceGraphPublishInput)
    for field_name, value in (
        ("workspace", valid.workspace),
        ("snapshots", valid.snapshots),
        ("repository_snapshots", valid.repository_snapshots),
        ("task_idempotency_key", valid.task_idempotency_key),
        ("generation_id", valid.generation_id),
        ("expected_active_generation_id", valid.expected_active_generation_id),
        ("owned_work_dirs", valid.owned_work_dirs),
        ("method_facts", (_method_facts(source_revision="stale-revision"),)),
    ):
        object.__setattr__(malformed, field_name, value)

    with pytest.raises(ValueError, match="workspace snapshot and generation"):
        orchestrator.publish(malformed)

    assert factory.namespaces == []
    assert calls == []


def test_publish_method_receipt_mismatch_fails_without_replacing_active_binding() -> None:
    calls: list[str] = []
    orchestrator, _, writer, repository = _orchestrator(calls, method_confirmed=False)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("workspace-1", "generation-1")

    outcome = orchestrator.publish(_input((_method_facts(),)))

    assert outcome.status is OrchestratorStatus.FAILED
    assert outcome.generation_state is WorkspaceGenerationState.FAILED
    assert repository.active == "old-generation"
    assert not any(call.startswith("cas:") for call in calls)


JAVA_RPC_FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures" / "java_rpc_contracts"


def _d1_revision(repo_id: str) -> str:
    manifest = json.loads((JAVA_RPC_FIXTURE_ROOT / "expected.json").read_text(encoding="utf-8"))
    repositories = manifest["repositories"]
    assert isinstance(repositories, list)
    repository = next(item for item in repositories if item["repo_id"] == repo_id)
    revision = repository["revision"]
    assert isinstance(revision, str)
    return revision


def _d1_request(
    *,
    mappings: tuple[ContractSourceMapping, ...],
    reverse: bool = False,
) -> WorkspaceServiceGraphPublishInput:
    repos = ("sample-order-contract", "sample-order-provider", "sample-checkout-consumer")
    snapshots = tuple(
        WorkspaceRepositorySnapshot(
            "d1-workspace",
            repo_id,
            "main",
            _d1_revision(repo_id),
            WorkspaceSourceDescriptor(WorkspaceSourceKind.GIT, f"https://example.test/{repo_id}.git"),
        )
        for repo_id in repos
    )
    runtime = tuple(
        RepositorySnapshot(repo_id, _d1_revision(repo_id), JAVA_RPC_FIXTURE_ROOT / repo_id, frozenset({"java"}))
        for repo_id in repos
    )
    if reverse:
        snapshots = tuple(reversed(snapshots))
        runtime = tuple(reversed(runtime))
    contract = JavaContractSource(
        "sample-order-contract",
        "sample-order-contract",
        _d1_revision("sample-order-contract"),
        JAVA_RPC_FIXTURE_ROOT / "sample-order-contract",
        ContractSourceRole.API,
    )
    authorization = WorkspaceJavaRpcAuthorization(
        (contract,),
        mappings,
        frozenset(
            {
                AuthorizedProviderSource(
                    "sample-order-provider", "sample-order-provider", _d1_revision("sample-order-provider")
                )
            }
        ),
        lambda resolution, operation, binding: (
            operation.group == "orders"
            and operation.version == "1.0"
            and operation.alias is None
            and binding.provider_endpoint_reference.endswith("|group=orders|version=1.0|alias=")
        ),
    )
    return WorkspaceServiceGraphPublishInput(
        Workspace("d1-workspace", "D1"),
        snapshots,
        runtime,
        "request-d1",
        "generation-d1",
        None,
        (),
        (),
        authorization,
    )


def _d1_mapping(consumer_repo_id: str) -> ContractSourceMapping:
    return ContractSourceMapping(
        consumer_repo_id,
        consumer_repo_id,
        _d1_revision(consumer_repo_id),
        "sample-order-contract",
        "sample-order-contract",
        _d1_revision("sample-order-contract"),
        "1.0",
        "workspace-contracts.yaml",
        1,
        1,
    )


def _d1_orchestrator(calls: list[str]) -> tuple[WorkspaceServiceGraphPublishOrchestrator, list[_MethodSink]]:
    orchestrator, factory, writer, _ = _orchestrator(calls)
    writer.namespace = WorkspaceServiceGraphPublishOrchestrator.namespace_for("d1-workspace", "generation-d1")
    sinks: list[_MethodSink] = []

    def method_sink_factory(scope: MethodGraphScope) -> _MethodSink:
        sink = _MethodSink(calls, scope)
        sinks.append(sink)
        return sink

    class _D1PlanBuilder:
        def build(self, result: ResolveResult) -> GraphWritePlan:
            repo_ids = ("sample-order-contract", "sample-order-provider", "sample-checkout-consumer")
            return GraphWritePlan(
                tuple(
                    GraphNode(repo_id, "Endpoint", {"id": repo_id, "repo_id": repo_id, "evidence_ids": (repo_id,)})
                    for repo_id in repo_ids
                ),
                (),
            )

    factory.components = replace(
        factory.components,
        plan_builder=_D1PlanBuilder(),
        method_graph_sink_factory=method_sink_factory,
        method_detectors=(DubboMethodDetector(),),
    )
    return orchestrator, sinks


@pytest.mark.unit
def test_publish_prepares_source_pinned_java_contract_views_and_assembles_d1_facts() -> None:
    calls: list[str] = []
    orchestrator, sinks = _d1_orchestrator(calls)
    request = _d1_request(mappings=(_d1_mapping("sample-order-provider"), _d1_mapping("sample-checkout-consumer")))

    outcome = orchestrator.publish(request)

    assert outcome.status is OrchestratorStatus.ACTIVE
    assert len(sinks) == 1 and sinks[0]._plan is not None
    facts = sinks[0]._plan.facts
    provider = next(item for item in facts if item.repo_id == "sample-order-provider")
    consumer = next(item for item in facts if item.repo_id == "sample-checkout-consumer")
    assert any(
        item.canonical_signature.endswith("#getOrder(java.lang.String):example.orders.api.OrderSummary")
        for item in provider.operations
    )
    assert consumer.consumer_calls
    assert "interface OrderService" not in (
        JAVA_RPC_FIXTURE_ROOT / "sample-order-provider/src/main/java/example/orders/provider/OrderServiceProvider.java"
    ).read_text(encoding="utf-8")
    frozen = frozenset(
        FrozenSourceIdentity(item.repo_id, item.repo_id, item.source_revision) for item in request.repository_snapshots
    )
    repositories = {(item.repo_id, item.repo_id, item.source_revision): item for item in request.repository_snapshots}
    index, _ = prepare_authorized_contract_views(request.java_rpc_authorization, repositories, frozen)  # type: ignore[arg-type]
    resolved = WorkspaceJavaRpcResolutionAssembler().resolve(
        WorkspaceJavaRpcResolutionInput(
            request.generation_id,
            facts,
            index,
            request.java_rpc_authorization.contract_mappings,  # type: ignore[union-attr]
            frozen,
            request.java_rpc_authorization.authorized_provider_sources,  # type: ignore[union-attr]
            request.java_rpc_authorization.matches_provider_identity,  # type: ignore[union-attr]
        )
    )
    a01 = next(item for item in resolved.determined if item.retained_call.start_line == 20)
    assert a01.contract.contract_method is not None
    assert (
        a01.binding is not None
        and a01.binding.provider_operation is not None
        and a01.binding.implementation is not None
    )


@pytest.mark.unit
def test_publish_java_rpc_requires_mapping_and_rejects_stale_pins() -> None:
    calls: list[str] = []
    orchestrator, sinks = _d1_orchestrator(calls)

    outcome = orchestrator.publish(_d1_request(mappings=()))
    stale = replace(_d1_mapping("sample-checkout-consumer"), consumer_source_revision="stale-revision")

    assert outcome.status is OrchestratorStatus.ACTIVE
    assert len(sinks) == 1 and sinks[0]._plan is not None
    assert not next(item for item in sinks[0]._plan.facts if item.repo_id == "sample-order-provider").operations
    assert not next(item for item in sinks[0]._plan.facts if item.repo_id == "sample-checkout-consumer").consumer_calls
    with pytest.raises(ValueError, match="current frozen"):
        _d1_request(mappings=(stale,))


@pytest.mark.unit
def test_publish_java_rpc_contract_preparation_is_repository_order_deterministic() -> None:
    mappings = (_d1_mapping("sample-order-provider"), _d1_mapping("sample-checkout-consumer"))
    forward_calls: list[str] = []
    reverse_calls: list[str] = []
    forward, _ = _d1_orchestrator(forward_calls)
    reverse, _ = _d1_orchestrator(reverse_calls)

    forward_outcome = forward.publish(_d1_request(mappings=mappings))
    reverse_outcome = reverse.publish(_d1_request(mappings=mappings, reverse=True))

    assert forward_outcome.status is reverse_outcome.status is OrchestratorStatus.ACTIVE
