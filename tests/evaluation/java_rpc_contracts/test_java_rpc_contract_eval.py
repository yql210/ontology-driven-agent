from __future__ import annotations

from pathlib import Path

import pytest

from tests.evaluation.java_rpc_contracts.java_rpc_contract_eval import (
    NOT_APPLICABLE,
    evaluate,
    load_manifest,
)

FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "java_rpc_contracts"


@pytest.mark.unit
def test_public_java_rpc_evaluator_runs_real_source_only_resolution_and_scores_manifest() -> None:
    report = evaluate(load_manifest(FIXTURE_ROOT / "expected.json"), FIXTURE_ROOT)

    assert report.outcome == "passed"
    assert report.exit_code == 0
    assert report.metrics.capture_rate == 1.0
    assert report.metrics.supported_call_resolution_recall == 1.0
    assert report.metrics.determined_edge_precision == 1.0
    assert report.metrics.reason_distribution == {
        "AMBIGUOUS_TARGET": 1,
        "DYNAMIC_TARGET": 1,
        "MISSING_IMPLEMENTATION": 1,
        "VERSION_CONFLICT": 1,
    }
    assert report.to_dict()["calls"]
    assert {item.case_id for item in report.calls if item.status == "passed"} >= {
        "A01",
        "A03",
        "A04",
        "A05",
        "A08",
        "A14",
        "A09",
    }
    assert not {item.case_id for item in report.calls if item.status == "failed"}
    assert {
        item.case_id: item.actual_outcome for item in report.calls if item.case_id in {"A09", "A10", "A11", "A12"}
    } == {"A09": "unresolved", "A10": "unresolved", "A11": "unresolved", "A12": "unresolved"}
    assert all(
        item.determined_chain for item in report.calls if item.case_id in {"A01", "A03", "A04", "A05", "A08", "A14"}
    )


@pytest.mark.unit
def test_public_java_rpc_evaluator_serializes_zero_denominators_as_na() -> None:
    manifest = load_manifest(FIXTURE_ROOT / "expected.json")
    manifest["cases"] = {"A02": manifest["cases"]["A02"]}

    report = evaluate(manifest, FIXTURE_ROOT)

    assert report.outcome == "passed"
    assert report.metrics.capture_rate == NOT_APPLICABLE
    assert report.metrics.supported_call_resolution_recall == NOT_APPLICABLE
    assert report.metrics.determined_edge_precision == NOT_APPLICABLE
    assert report.to_dict()["metrics"] == {
        "capture_rate": "N/A",
        "supported_call_resolution_recall": "N/A",
        "determined_edge_precision": "N/A",
        "reason_distribution": {},
    }
