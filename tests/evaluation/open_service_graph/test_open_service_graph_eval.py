from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.graph_writer import GraphWriter
from tests.evaluation.open_service_graph.open_service_graph_eval import (
    EvaluationOutcome,
    GoldValidationError,
    evaluate,
    load_gold,
)

ROOT = Path(__file__).parent
GOLD_PATH = ROOT / "golden_dataset.json"
FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures/service_graph/neutral_three_repo"


def _gold_copy() -> dict[str, object]:
    return copy.deepcopy(load_gold(GOLD_PATH))


def _write_gold(path: Path, gold: dict[str, object]) -> Path:
    path.write_text(json.dumps(gold), encoding="utf-8")
    return path


def test_golden_dataset_has_stable_cross_protocol_and_negative_cases() -> None:
    gold = load_gold(GOLD_PATH)

    assert [record["gold_id"] for record in gold["records"]] == [
        "OSG-I7-HTTP-POST-001",
        "OSG-I7-DUBBO-GET-001",
        "OSG-I7-KAFKA-ORDER-EVENTS-001",
        "OSG-I7-HTTP-UNRESOLVED-001",
        "OSG-I7-ISOLATED-CATALOG-001",
    ]
    assert {record["protocol"] for record in gold["records"]} == {"HTTP", "DUBBO", "MQ"}
    assert all(record["fixture_commit"] for record in gold["records"])
    assert all(record["expected_evidence"] for record in gold["records"])


def test_offline_evaluator_scores_actual_resolver_plan_writer_and_query_results() -> None:
    report = evaluate(load_gold(GOLD_PATH), FIXTURE_ROOT)

    assert report.outcome is EvaluationOutcome.PASSED
    assert report.exit_code == 0
    assert report.reasons == ()
    assert report.metrics.provider_precision == 1.0
    assert report.metrics.consumer_precision == 1.0
    assert report.metrics.cross_repo_matching_precision == 1.0
    assert report.metrics.high_confidence_false_positive_rate == 0.0
    assert all(record.status == "passed" for record in report.records)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda gold: gold["records"][0].update({"generation_id": "wrong-generation"}), "GENERATION_MISMATCH"),
        (lambda gold: gold["records"][0]["expected_evidence"].pop(), "EVIDENCE_REQUIREMENT_FAILED"),
    ],
)
def test_offline_evaluator_fails_closed_for_generation_or_evidence_failures(mutation, reason: str) -> None:
    gold = _gold_copy()
    mutation(gold)

    report = evaluate(gold, FIXTURE_ROOT)

    assert report.outcome is EvaluationOutcome.UNVERIFIED
    assert report.exit_code == 2
    assert reason in report.reasons
    assert report.metrics is None


def test_offline_evaluator_fails_closed_for_unconfirmed_readback(monkeypatch: pytest.MonkeyPatch) -> None:
    original_write = GraphWriter.write

    def unconfirmed_write(self: GraphWriter, plan):
        return replace(original_write(self, plan), confirmed=False)

    monkeypatch.setattr(GraphWriter, "write", unconfirmed_write)

    report = evaluate(load_gold(GOLD_PATH), FIXTURE_ROOT)

    assert report.outcome is EvaluationOutcome.UNVERIFIED
    assert report.exit_code == 2
    assert "UNCONFIRMED_READBACK" in report.reasons
    assert report.metrics is None


def test_load_gold_rejects_incomplete_or_duplicate_public_records(tmp_path: Path) -> None:
    incomplete = _gold_copy()
    incomplete["records"][0].pop("expected_provider_refs")
    with pytest.raises(GoldValidationError):
        load_gold(_write_gold(tmp_path / "incomplete.json", incomplete))

    duplicate = _gold_copy()
    duplicate["records"].append(copy.deepcopy(duplicate["records"][0]))
    with pytest.raises(GoldValidationError):
        load_gold(_write_gold(tmp_path / "duplicate.json", duplicate))
