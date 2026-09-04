"""Public deterministic offline evaluator for the neutral Service Graph fixture."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ontoagent.parsing.service_graph.detectors.dubbo import DubboDetector
from ontoagent.parsing.service_graph.detectors.messaging import MessagingDetector
from ontoagent.parsing.service_graph.detectors.spring_http import SpringHttpDetector
from ontoagent.parsing.service_graph.graph_plan import GraphPlanBuilder
from ontoagent.parsing.service_graph.graph_writer import GraphWriter, InMemoryGraphSink
from ontoagent.parsing.service_graph.models import RepositorySnapshot
from ontoagent.parsing.service_graph.query import ServiceGraphQuery, ServiceGraphQueryStatus
from ontoagent.parsing.service_graph.resolver import FactBatch, GraphEntity, ResolveResult, ServiceGraphResolver

GOLD_SCHEMA_VERSION = "1.0"
FIXTURE_COMMIT = "fixture-neutral-three-repo-v1"
REVISIONS = {
    "provider-orders": "fixture-provider-v1",
    "consumer-checkout": "fixture-consumer-v1",
    "isolated-catalog": "fixture-isolated-v1",
}
THRESHOLD_KEYS = frozenset(
    {
        "provider_precision",
        "consumer_precision",
        "cross_repo_matching_precision",
        "high_confidence_false_positive_rate",
    }
)


class GoldValidationError(ValueError):
    """Raised when the public Service Graph golden dataset is malformed."""


class EvaluationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class EvaluationMetrics:
    provider_precision: float
    consumer_precision: float
    cross_repo_matching_precision: float
    high_confidence_false_positive_rate: float


@dataclass(frozen=True)
class RecordReport:
    gold_id: str
    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationReport:
    outcome: EvaluationOutcome
    exit_code: int
    reasons: tuple[str, ...]
    metrics: EvaluationMetrics | None
    records: tuple[RecordReport, ...]


def load_gold(path: Path) -> dict[str, Any]:
    """Load and strictly validate the public, source-pinned golden dataset."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GoldValidationError(f"unable to read golden dataset: {error}") from error
    _validate_gold(value)
    return value


def evaluate(gold: Mapping[str, object], fixture_root: Path) -> EvaluationReport:
    """Evaluate real offline resolver, graph write/readback, and query assertions against gold."""
    try:
        _validate_gold(gold)
    except GoldValidationError as error:
        return _unverified((f"GOLD_INVALID:{error}",), ())
    if not fixture_root.is_dir():
        return _unverified(("FIXTURE_NOT_FOUND",), ())

    result, query = _build_query(fixture_root)
    records: list[RecordReport] = []
    failures: list[str] = []
    unverified: list[str] = []
    for record in gold["records"]:  # type: ignore[index]
        report = _evaluate_record(record, result, query)
        records.append(report)
        failures.extend(report.reasons)
        if any(
            reason
            in {
                "GENERATION_MISMATCH",
                "EVIDENCE_REQUIREMENT_FAILED",
                "QUERY_BLOCKED",
                "UNCONFIRMED_READBACK",
                "READBACK_VALIDATION_FAILED",
            }
            for reason in report.reasons
        ):
            unverified.extend(report.reasons)
    if unverified:
        return _unverified(tuple(sorted(set(unverified))), tuple(records))
    metrics = _metrics(result, gold["records"])  # type: ignore[index]
    threshold_reasons = _threshold_failures(metrics, gold["thresholds"])  # type: ignore[index]
    if failures or threshold_reasons:
        return EvaluationReport(
            EvaluationOutcome.FAILED,
            1,
            tuple(sorted(set([*failures, *threshold_reasons]))),
            metrics,
            tuple(records),
        )
    return EvaluationReport(EvaluationOutcome.PASSED, 0, (), metrics, tuple(records))


def _build_query(fixture_root: Path) -> tuple[ResolveResult, ServiceGraphQuery]:
    batches = []
    detectors = (SpringHttpDetector(), DubboDetector(), MessagingDetector())
    for repo_id, revision in REVISIONS.items():
        snapshot = RepositorySnapshot(repo_id, revision, fixture_root / repo_id, frozenset({"java", "yaml"}))
        batches.append(
            FactBatch(
                repo_id, revision, "generation-1", "main", tuple(detector.detect(snapshot) for detector in detectors)
            )
        )
    result = ServiceGraphResolver().resolve(tuple(batches))
    plan = GraphPlanBuilder().build(result)
    receipt = GraphWriter(InMemoryGraphSink(namespace="service-graph-offline-eval")).write(plan)
    return result, ServiceGraphQuery(plan, receipt)


def _evaluate_record(record: Mapping[str, object], result: ResolveResult, query: ServiceGraphQuery) -> RecordReport:
    reasons: list[str] = []
    gold_id = record["gold_id"]
    assert isinstance(gold_id, str)
    generation_id = record["generation_id"]
    assert isinstance(generation_id, str)
    provider_refs = _refs(record["expected_provider_refs"])
    consumer_refs = _refs(record["expected_consumer_refs"])
    protocol = record["protocol"]
    match_key = record["match_key"]
    assert isinstance(protocol, str) and isinstance(match_key, str)
    links = [link for link in result.resolved_links if (link.protocol, link.match_key) == (protocol, match_key)]
    entities = {entity.id: entity for entity in result.logical_entities}
    actual_provider_refs = {_entity_ref(entities[link.provider_fact_id]) for link in links}
    actual_consumer_refs = {_entity_ref(entities[link.consumer_fact_id]) for link in links}
    unresolved = record["expected_unresolved"]
    if unresolved is None:
        if actual_provider_refs != provider_refs or actual_consumer_refs != consumer_refs:
            reasons.append("RESOLUTION_MISMATCH")
    else:
        if links:
            reasons.append("UNEXPECTED_RESOLUTION")
        expected_unresolved = _unresolved_ref(unresolved)
        if not any(
            _matches_unresolved(item, protocol, record["canonical_endpoint_key"], expected_unresolved)
            for item in result.unresolved
        ):
            reasons.append("UNRESOLVED_MISMATCH")
    for ref, role in [*((ref, "provider") for ref in provider_refs), *((ref, "consumer") for ref in consumer_refs)]:
        response = (
            query.find_endpoint_providers(ref[0], generation_id, ref[2])
            if role == "provider"
            else query.find_endpoint_consumers(ref[0], generation_id, ref[2])
        )
        if response.status is ServiceGraphQueryStatus.BLOCKED:
            reasons.append(_query_reason(response.reasons[0].value))
            continue
        if not any(_node_ref(node.properties) == ref for node in response.nodes):
            reasons.append("QUERY_RESULT_MISMATCH")
    expected_evidence = record["expected_evidence"]
    assert isinstance(expected_evidence, list)
    actual_evidence = set()
    for link in links:
        dependency = query.find_service_dependencies(link.consumer_repo_id, generation_id, link.consumer_fact_id)
        if dependency.status is ServiceGraphQueryStatus.BLOCKED:
            reasons.append(_query_reason(dependency.reasons[0].value))
            continue
        for relation in dependency.relations:
            response = query.get_evidence(link.consumer_repo_id, generation_id, relation.id)
            if response.status is ServiceGraphQueryStatus.BLOCKED:
                reasons.append(_query_reason(response.reasons[0].value))
                continue
            actual_evidence.update(node.id for node in response.nodes)
    for ref in [*provider_refs, *consumer_refs]:
        response = query.find_endpoint_providers(ref[0], generation_id, ref[2])
        if response.status is ServiceGraphQueryStatus.READY:
            for node in response.nodes:
                actual_evidence.update(node.properties["evidence_ids"])
        response = query.find_endpoint_consumers(ref[0], generation_id, ref[2])
        if response.status is ServiceGraphQueryStatus.READY:
            for node in response.nodes:
                actual_evidence.update(node.properties["evidence_ids"])
    if set(expected_evidence) != actual_evidence:
        reasons.append("EVIDENCE_REQUIREMENT_FAILED")
    return RecordReport(gold_id, "passed" if not reasons else "failed", tuple(sorted(set(reasons))))


def _metrics(result: ResolveResult, records: object) -> EvaluationMetrics:
    assert isinstance(records, list)
    expected_pairs = {
        (
            record["protocol"],
            record["match_key"],
            next(iter(_refs(record["expected_provider_refs"]))),
            next(iter(_refs(record["expected_consumer_refs"]))),
        )
        for record in records
        if record["expected_unresolved"] is None
    }
    entities = {entity.id: entity for entity in result.logical_entities}
    actual = [
        (
            link.protocol,
            link.match_key,
            _entity_ref(entities[link.provider_fact_id]),
            _entity_ref(entities[link.consumer_fact_id]),
            link.confidence,
        )
        for link in result.resolved_links
    ]
    provider_precision = _precision(
        [item[:3] for item in actual], [(protocol, key, provider) for protocol, key, provider, _ in expected_pairs]
    )
    consumer_precision = _precision(
        [(protocol, key, consumer) for protocol, key, _, consumer, _ in actual],
        [(protocol, key, consumer) for protocol, key, _, consumer in expected_pairs],
    )
    matching_precision = _precision([item[:4] for item in actual], list(expected_pairs))
    high_confidence = [item for item in actual if item[4] >= 0.9]
    false_positives = sum(item[:4] not in expected_pairs for item in high_confidence)
    return EvaluationMetrics(
        provider_precision,
        consumer_precision,
        matching_precision,
        false_positives / len(high_confidence) if high_confidence else 0.0,
    )


def _precision(actual: list[object], expected: list[object]) -> float:
    return sum(item in expected for item in actual) / len(actual) if actual else 0.0


def _threshold_failures(metrics: EvaluationMetrics, thresholds: object) -> list[str]:
    assert isinstance(thresholds, Mapping)
    values = {
        "provider_precision": metrics.provider_precision,
        "consumer_precision": metrics.consumer_precision,
        "cross_repo_matching_precision": metrics.cross_repo_matching_precision,
    }
    reasons = [name.upper() + "_THRESHOLD_FAILED" for name, value in values.items() if value < thresholds[name]]
    if metrics.high_confidence_false_positive_rate > thresholds["high_confidence_false_positive_rate"]:
        reasons.append("HIGH_CONFIDENCE_FALSE_POSITIVE_RATE_THRESHOLD_FAILED")
    return reasons


def _validate_gold(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "fixture",
        "fixture_commit",
        "thresholds",
        "records",
    }:
        raise GoldValidationError("gold schema is invalid")
    if value["schema_version"] != GOLD_SCHEMA_VERSION or value["fixture"] != "neutral_three_repo":
        raise GoldValidationError("gold identity is invalid")
    if value["fixture_commit"] != FIXTURE_COMMIT:
        raise GoldValidationError("fixture commit is invalid")
    thresholds = value["thresholds"]
    if not isinstance(thresholds, Mapping) or set(thresholds) != THRESHOLD_KEYS:
        raise GoldValidationError("thresholds are invalid")
    if any(type(number) not in {int, float} or not 0 <= number <= 1 for number in thresholds.values()):
        raise GoldValidationError("threshold values are invalid")
    records = value["records"]
    if not isinstance(records, list) or not records:
        raise GoldValidationError("records must be non-empty")
    ids: set[str] = set()
    for record in records:
        _validate_record(record, ids)


def _validate_record(value: object, ids: set[str]) -> None:
    required = {
        "gold_id",
        "fixture_commit",
        "source_revisions",
        "generation_id",
        "protocol",
        "canonical_endpoint_key",
        "match_key",
        "expected_provider_refs",
        "expected_consumer_refs",
        "expected_unresolved",
        "expected_evidence",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise GoldValidationError("record fields are invalid")
    gold_id = value["gold_id"]
    if not isinstance(gold_id, str) or not gold_id.startswith("OSG-I7-") or gold_id in ids:
        raise GoldValidationError("gold_id is invalid or duplicated")
    ids.add(gold_id)
    if value["fixture_commit"] != FIXTURE_COMMIT or not isinstance(value["generation_id"], str):
        raise GoldValidationError("record fixture identity is invalid")
    if value["protocol"] not in {"HTTP", "DUBBO", "MQ"} or not all(
        isinstance(value[name], str) and value[name] for name in ("canonical_endpoint_key", "match_key")
    ):
        raise GoldValidationError("record protocol or keys are invalid")
    revisions = value["source_revisions"]
    if (
        not isinstance(revisions, Mapping)
        or not revisions
        or any(REVISIONS.get(repo_id) != revision for repo_id, revision in revisions.items())
    ):
        raise GoldValidationError("source revisions are invalid")
    provider_refs = _refs(value["expected_provider_refs"])
    consumer_refs = _refs(value["expected_consumer_refs"])
    if value["expected_unresolved"] is None and (not provider_refs or not consumer_refs):
        raise GoldValidationError("resolved records require provider and consumer refs")
    if value["expected_unresolved"] is not None:
        _unresolved_ref(value["expected_unresolved"])
    evidence = value["expected_evidence"]
    if (
        not isinstance(evidence, list)
        or not evidence
        or len(set(evidence)) != len(evidence)
        or not all(isinstance(item, str) and len(item) == 32 for item in evidence)
    ):
        raise GoldValidationError("evidence requirements are invalid")


def _refs(value: object) -> set[tuple[str, str, str]]:
    if not isinstance(value, list):
        raise GoldValidationError("endpoint refs must be lists")
    refs = set()
    for ref in value:
        if not isinstance(ref, Mapping) or set(ref) != {"repo_id", "source_revision", "canonical_key"}:
            raise GoldValidationError("endpoint ref fields are invalid")
        repo_id, revision, canonical_key = ref.values()
        if REVISIONS.get(repo_id) != revision or not isinstance(canonical_key, str) or not canonical_key:
            raise GoldValidationError("endpoint ref values are invalid")
        refs.add((repo_id, revision, canonical_key))
    return refs


def _unresolved_ref(value: object) -> tuple[str, str, str, str]:
    if not isinstance(value, Mapping) or set(value) != {"repo_id", "source_revision", "role", "reason_code"}:
        raise GoldValidationError("unresolved reference is invalid")
    repo_id, revision, role, reason = value.values()
    if (
        REVISIONS.get(repo_id) != revision
        or role not in {"provider", "consumer"}
        or reason not in {"NO_PROVIDER_MATCH", "NO_CONSUMER_MATCH"}
    ):
        raise GoldValidationError("unresolved reference values are invalid")
    return repo_id, revision, role, reason


def _entity_ref(entity: GraphEntity) -> tuple[str, str, str]:
    return entity.repo_id, entity.source_revision, entity.canonical_key


def _node_ref(properties: Mapping[str, object]) -> tuple[str, str, str]:
    return properties["repo_id"], properties["source_revision"], properties["canonical_key"]  # type: ignore[return-value]


def _matches_unresolved(item: object, protocol: str, endpoint_key: object, expected: tuple[str, str, str, str]) -> bool:
    return (
        item.protocol == protocol  # type: ignore[union-attr]
        and item.canonical_key == endpoint_key  # type: ignore[union-attr]
        and (item.repo_id, item.source_revision, item.role, item.reason_code) == expected  # type: ignore[union-attr]
    )


def _query_reason(reason: str) -> str:
    reasons = {
        "generation_mismatch": "GENERATION_MISMATCH",
        "unconfirmed_readback": "UNCONFIRMED_READBACK",
        "receipt_count_mismatch": "READBACK_VALIDATION_FAILED",
        "receipt_readback_mismatch": "READBACK_VALIDATION_FAILED",
    }
    return reasons.get(reason, "QUERY_BLOCKED")


def _unverified(reasons: tuple[str, ...], records: tuple[RecordReport, ...]) -> EvaluationReport:
    return EvaluationReport(EvaluationOutcome.UNVERIFIED, 2, reasons, None, records)
