from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from ontoagent.api.cli import main
from tests.evaluation.open_service_graph.open_service_graph_eval import (
    EvaluationMetrics,
    EvaluationOutcome,
    EvaluationReport,
)

PROJECT_ROOT = Path(__file__).parents[2]
GOLD_PATH = PROJECT_ROOT / "tests/evaluation/open_service_graph/golden_dataset.json"
FIXTURE_ROOT = PROJECT_ROOT / "tests/fixtures/service_graph/neutral_three_repo"


@pytest.fixture
def runner() -> CliRunner:
    """Create a Click command runner."""
    return CliRunner()


def test_service_graph_eval_and_compatibility_alias_are_registered(runner: CliRunner) -> None:
    """Both public evaluator command names are visible in the main CLI help."""
    result = runner.invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "service-graph-eval" in result.output
    assert "evaluate-open-service-graph" in result.output


def test_service_graph_eval_defaults_to_checked_in_data(runner: CliRunner) -> None:
    """The default command evaluates the checked-in Golden data and neutral fixture."""
    result = runner.invoke(main, ["service-graph-eval"])

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["outcome"] == "passed"
    assert report["exit_code"] == 0
    assert report["metrics"] == {
        "provider_precision": 1.0,
        "consumer_precision": 1.0,
        "cross_repo_matching_precision": 1.0,
        "high_confidence_false_positive_rate": 0.0,
    }
    assert report["reasons"] == []


def test_evaluate_open_service_graph_remains_a_compatibility_alias(runner: CliRunner) -> None:
    """The legacy command evaluates the same checked-in data and fixture."""
    result = runner.invoke(main, ["evaluate-open-service-graph"])

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["outcome"] == "passed"
    assert report["exit_code"] == 0


def test_service_graph_eval_accepts_existing_custom_paths(runner: CliRunner) -> None:
    """Explicit existing Golden and fixture paths are passed to the real evaluator."""
    result = runner.invoke(
        main,
        ["service-graph-eval", "--golden", str(GOLD_PATH), "--fixture-root", str(FIXTURE_ROOT)],
    )

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["outcome"] == "passed"
    assert report["metrics"]["cross_repo_matching_precision"] == 1.0


@pytest.mark.parametrize(
    ("outcome", "evaluator_exit_code", "expected_exit_code"),
    [
        (EvaluationOutcome.FAILED, 1, 2),
        (EvaluationOutcome.UNVERIFIED, 2, 3),
    ],
)
def test_service_graph_eval_maps_nonzero_evaluator_outcomes(
    runner: CliRunner,
    outcome: EvaluationOutcome,
    evaluator_exit_code: int,
    expected_exit_code: int,
) -> None:
    """Metric failures and blocked evaluations use their distinct CLI exit codes."""
    report = EvaluationReport(
        outcome=outcome,
        exit_code=evaluator_exit_code,
        reasons=("TEST_REASON",),
        metrics=EvaluationMetrics(0.5, 0.5, 0.5, 0.5) if outcome is EvaluationOutcome.FAILED else None,
        records=(),
    )

    with patch("tests.evaluation.open_service_graph.open_service_graph_eval.evaluate", return_value=report):
        result = runner.invoke(main, ["service-graph-eval"])

    assert result.exit_code == expected_exit_code
    payload = json.loads(result.output)
    assert payload["outcome"] == outcome.value
    assert payload["exit_code"] == expected_exit_code
    assert payload["reasons"] == ["TEST_REASON"]


def test_service_graph_eval_rejects_missing_custom_paths(runner: CliRunner, tmp_path: Path) -> None:
    """Click rejects missing Golden and fixture paths before evaluation starts."""
    result = runner.invoke(main, ["service-graph-eval", "--golden", str(tmp_path / "missing.json")])

    assert result.exit_code != 0
    assert "Invalid value" in result.output
    assert "does not exist" in result.output
