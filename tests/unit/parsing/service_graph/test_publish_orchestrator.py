from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ontoagent.parsing.service_graph.generation_manifest import (
    ActiveServiceGraphBinding,
    ManifestBlockReason,
    ManifestResolution,
    ManifestResolutionStatus,
    Neo4jNamespace,
    ServiceGraphManifest,
)
from ontoagent.parsing.service_graph.graph_plan import GraphWritePlan
from ontoagent.parsing.service_graph.graph_writer import WriteReceipt
from ontoagent.parsing.service_graph.models import DetectorFacts, RepositorySnapshot
from ontoagent.parsing.service_graph.neo4j_manifest_repository import (
    ManifestPublicationResult,
    ManifestPublicationStatus,
)
from ontoagent.parsing.service_graph.publish_orchestrator import (
    ServiceGraphPublishComponents,
    ServiceGraphPublishInput,
    ServiceGraphPublishOrchestrator,
    ServiceGraphPublishStatus,
)
from ontoagent.parsing.service_graph.resolver import ResolveResult


@dataclass
class _Factory:
    components: ServiceGraphPublishComponents

    def create(self, namespace: Neo4jNamespace) -> ServiceGraphPublishComponents:
        assert namespace.value == "test-namespace"
        return self.components


class _Registry:
    ids = ("detector-a", "detector-b")

    def __init__(self, calls: list[str], *, invalid: bool = False) -> None:
        self._calls = calls
        self._invalid = invalid

    def detect(self, snapshot: RepositorySnapshot, detector_id: str | None = None) -> DetectorFacts:
        assert detector_id is not None
        self._calls.append(f"detect:{snapshot.repo_id}:{detector_id}")
        repo_id = "wrong-repo" if self._invalid else snapshot.repo_id
        return DetectorFacts(detector_id, "1", repo_id, snapshot.source_revision, (), (), (), ())


class _Resolver:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def resolve(self, batches: tuple[object, ...]) -> ResolveResult:
        self._calls.append("resolve")
        return ResolveResult((), (), ())


class _PlanBuilder:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def build(self, result: ResolveResult) -> GraphWritePlan:
        self._calls.append("plan")
        return GraphWritePlan((), ())


class _Writer:
    def __init__(self, calls: list[str], *, confirmed: bool = True) -> None:
        self._calls = calls
        self._confirmed = confirmed

    def write(self, plan: GraphWritePlan) -> WriteReceipt:
        self._calls.append("write")
        return WriteReceipt(self._confirmed, 0, 0, plan, "test-namespace")


class _Repository:
    def __init__(
        self,
        calls: list[str],
        *,
        building_error: bool = False,
        verified: ManifestResolution | None = None,
        publications: dict[str, ManifestPublicationResult] | None = None,
    ) -> None:
        self._calls = calls
        self._building_error = building_error
        self._verified = verified or ManifestResolution(
            ManifestResolutionStatus.READY,
            ActiveServiceGraphBinding(
                ServiceGraphManifest("placeholder", "placeholder", "placeholder", Neo4jNamespace("placeholder"))
            ),
            (),
        )
        self._publications = publications or {}

    def persist_building(self, manifest: object) -> object:
        self._calls.append(f"building:{manifest.repo_id}")
        if self._building_error:
            raise RuntimeError("store unavailable")
        return object()

    def persist_verified(self, manifest: object, plan: GraphWritePlan, receipt: WriteReceipt) -> ManifestResolution:
        self._calls.append(f"verified:{manifest.repo_id}")
        return self._verified

    def publish_active(
        self,
        repo_id: str,
        namespace: Neo4jNamespace,
        expected_active_generation_id: str | None,
        candidate_generation_id: str,
    ) -> ManifestPublicationResult:
        self._calls.append(f"publish:{repo_id}:{expected_active_generation_id}")
        return self._publications.get(
            repo_id, ManifestPublicationResult(ManifestPublicationStatus.PUBLISHED, candidate_generation_id)
        )


def _input(repo_id: str, expected_active_generation_id: str | None = None) -> ServiceGraphPublishInput:
    return ServiceGraphPublishInput(
        RepositorySnapshot(repo_id, f"revision-{repo_id}", Path("."), frozenset({"java"})),
        f"generation-{repo_id}",
        "main",
        expected_active_generation_id,
    )


def _orchestrator(
    calls: list[str],
    *,
    invalid: bool = False,
    confirmed: bool = True,
    building_error: bool = False,
    verified: ManifestResolution | None = None,
    publications: dict[str, ManifestPublicationResult] | None = None,
) -> ServiceGraphPublishOrchestrator:
    components = ServiceGraphPublishComponents(
        _Registry(calls, invalid=invalid),
        _Resolver(calls),
        _PlanBuilder(calls),
        _Writer(calls, confirmed=confirmed),
        _Repository(calls, building_error=building_error, verified=verified, publications=publications),
    )
    return ServiceGraphPublishOrchestrator(_Factory(components))


def test_publish_orders_all_confirmations_before_any_active_compare_and_set() -> None:
    calls: list[str] = []

    result = _orchestrator(calls).publish(Neo4jNamespace("test-namespace"), (_input("repo-a"), _input("repo-b")))

    assert result.status is ServiceGraphPublishStatus.ACTIVE
    assert result.graph_write_confirmed
    assert [receipt.active_published for receipt in result.publication_receipts] == [True, True]
    assert calls == [
        "detect:repo-a:detector-a",
        "detect:repo-a:detector-b",
        "detect:repo-b:detector-a",
        "detect:repo-b:detector-b",
        "resolve",
        "plan",
        "building:repo-a",
        "building:repo-b",
        "write",
        "verified:repo-a",
        "verified:repo-b",
        "publish:repo-a:None",
        "publish:repo-b:None",
    ]


def test_publish_blocks_detector_fact_identity_mismatch_before_graph_write() -> None:
    calls: list[str] = []

    result = _orchestrator(calls, invalid=True).publish(Neo4jNamespace("test-namespace"), (_input("repo-a"),))

    assert result.status is ServiceGraphPublishStatus.BLOCKED
    assert not result.graph_write_confirmed
    assert calls == ["detect:repo-a:detector-a"]


def test_publish_blocks_unconfirmed_graph_without_verifying_or_activating() -> None:
    calls: list[str] = []

    result = _orchestrator(calls, confirmed=False).publish(Neo4jNamespace("test-namespace"), (_input("repo-a"),))

    assert result.status is ServiceGraphPublishStatus.BLOCKED
    assert not result.graph_write_confirmed
    assert calls[-1] == "write"
    assert not any(call.startswith("verified:") or call.startswith("publish:") for call in calls)


def test_publish_fails_when_building_manifest_cannot_be_persisted() -> None:
    calls: list[str] = []

    result = _orchestrator(calls, building_error=True).publish(Neo4jNamespace("test-namespace"), (_input("repo-a"),))

    assert result.status is ServiceGraphPublishStatus.FAILED
    assert not result.graph_write_confirmed
    assert calls == ["detect:repo-a:detector-a", "detect:repo-a:detector-b", "resolve", "plan", "building:repo-a"]


def test_publish_blocks_when_verified_manifest_is_rejected() -> None:
    calls: list[str] = []
    blocked = ManifestResolution(ManifestResolutionStatus.BLOCKED, None, (ManifestBlockReason.UNCONFIRMED_RECEIPT,))

    result = _orchestrator(calls, verified=blocked).publish(Neo4jNamespace("test-namespace"), (_input("repo-a"),))

    assert result.status is ServiceGraphPublishStatus.BLOCKED
    assert result.graph_write_confirmed
    assert calls[-1] == "verified:repo-a"
    assert not any(call.startswith("publish:") for call in calls)


def test_publish_blocks_stale_compare_and_set_without_claiming_activation() -> None:
    calls: list[str] = []
    publications = {"repo-a": ManifestPublicationResult(ManifestPublicationStatus.REJECTED_STALE_ACTIVE, "other")}

    result = _orchestrator(calls, publications=publications).publish(
        Neo4jNamespace("test-namespace"), (_input("repo-a", "old"),)
    )

    assert result.status is ServiceGraphPublishStatus.BLOCKED
    assert result.graph_write_confirmed
    assert not result.publication_receipts[0].active_published
    assert result.publication_receipts[0].publication_status is ManifestPublicationStatus.REJECTED_STALE_ACTIVE


def test_publish_reports_partial_multi_repo_activation_as_blocked() -> None:
    calls: list[str] = []
    publications = {"repo-b": ManifestPublicationResult(ManifestPublicationStatus.REJECTED_STALE_ACTIVE, "other")}

    result = _orchestrator(calls, publications=publications).publish(
        Neo4jNamespace("test-namespace"), (_input("repo-a"), _input("repo-b", "old"))
    )

    assert result.status is ServiceGraphPublishStatus.BLOCKED
    assert result.graph_write_confirmed
    assert [receipt.active_published for receipt in result.publication_receipts] == [True, False]
    assert calls[-1] == "publish:repo-b:old"
