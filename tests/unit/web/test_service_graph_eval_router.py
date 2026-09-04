"""Tests for the read-only Open Service Graph evaluation endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ontoagent.api.web.app import create_app
from tests.evaluation.open_service_graph.open_service_graph_eval import (
    EvaluationMetrics,
    EvaluationOutcome,
    EvaluationReport,
)


@pytest.fixture
def client() -> TestClient:
    """Create an app without entering its external-service lifespan."""
    return TestClient(create_app())


@pytest.mark.unit
def test_service_graph_eval_is_registered(client: TestClient) -> None:
    """The public evaluation route is mounted under the Web API prefix."""
    assert "/api/service-graph/eval" in client.get("/openapi.json").json()["paths"]


@pytest.mark.unit
def test_service_graph_eval_default_returns_json_safe_passing_metrics(client: TestClient) -> None:
    """The default request evaluates only the checked-in offline assets."""
    response = client.get("/api/service-graph/eval")

    assert response.status_code == 200
    assert response.json() == {
        "outcome": "passed",
        "exit_code": 0,
        "metrics": {
            "provider_precision": 1.0,
            "consumer_precision": 1.0,
            "cross_repo_matching_precision": 1.0,
            "high_confidence_false_positive_rate": 0.0,
        },
        "reasons": [],
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "metrics", "expected_status"),
    [
        (EvaluationOutcome.FAILED, EvaluationMetrics(0.5, 0.5, 0.5, 0.5), 422),
        (EvaluationOutcome.UNVERIFIED, None, 503),
    ],
)
def test_service_graph_eval_maps_failed_and_blocked_reports(
    client: TestClient,
    outcome: EvaluationOutcome,
    metrics: EvaluationMetrics | None,
    expected_status: int,
) -> None:
    """Metric failures and integrity blocks retain distinct HTTP outcomes."""
    report = EvaluationReport(outcome, 1, ("TEST_REASON",), metrics, ())
    with patch("ontoagent.api.web.router.service_graph_eval.run_evaluation", return_value=report):
        response = client.get("/api/service-graph/eval")

    assert response.status_code == expected_status
    assert response.json()["outcome"] == outcome.value
    assert response.json()["exit_code"] == report.exit_code
    assert response.json()["reasons"] == ["TEST_REASON"]


@pytest.mark.unit
def test_service_graph_eval_does_not_construct_external_backends() -> None:
    """This route is runnable without Neo4j, Chroma, or application lifespan setup."""
    report = EvaluationReport(EvaluationOutcome.PASSED, 0, (), EvaluationMetrics(1.0, 1.0, 1.0, 0.0), ())
    with (
        patch("ontoagent.api.web.app.create_graph_store") as create_graph_store,
        patch("ontoagent.api.web.router.service_graph_eval.run_evaluation", return_value=report),
    ):
        response = TestClient(create_app()).get("/api/service-graph/eval")

    assert response.status_code == 200
    create_graph_store.assert_not_called()
