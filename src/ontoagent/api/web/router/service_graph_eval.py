"""Read-only endpoint for the checked-in Open Service Graph evaluation."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(tags=["service-graph"])

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_GOLDEN_PATH = _PROJECT_ROOT / "tests" / "evaluation" / "open_service_graph" / "golden_dataset.json"
_FIXTURE_ROOT = _PROJECT_ROOT / "tests" / "fixtures" / "service_graph" / "neutral_three_repo"
_EVALUATOR_PATH = _PROJECT_ROOT / "tests" / "evaluation" / "open_service_graph" / "open_service_graph_eval.py"


class ServiceGraphEvaluationResponse(BaseModel):
    """JSON-safe summary of an offline Open Service Graph evaluation."""

    outcome: str
    exit_code: int
    metrics: dict[str, float] | None
    reasons: list[str]


def _load_evaluator() -> ModuleType:
    """Load the checked-in offline evaluator in source and installed Web contexts."""
    module_name = "tests.evaluation.open_service_graph.open_service_graph_eval"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != "tests":
            raise

    spec = importlib.util.spec_from_file_location("ontoagent_open_service_graph_web_eval", _EVALUATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"offline evaluator is unavailable at {_EVALUATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_evaluation() -> Any:
    """Run the evaluator exclusively against the repository's pinned offline assets."""
    evaluator = _load_evaluator()
    return evaluator.evaluate(evaluator.load_gold(_GOLDEN_PATH), _FIXTURE_ROOT)


@router.get(
    "/service-graph/eval",
    response_model=ServiceGraphEvaluationResponse,
    responses={422: {"description": "Metric thresholds failed"}, 503: {"description": "Evaluation integrity blocked"}},
)
def evaluate_service_graph() -> JSONResponse:
    """Evaluate the pinned Golden dataset without constructing external graph backends."""
    try:
        report = run_evaluation()
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        return JSONResponse(
            status_code=503,
            content={"outcome": "unverified", "exit_code": 2, "metrics": None, "reasons": [str(error)]},
        )

    payload = {
        "outcome": report.outcome.value,
        "exit_code": report.exit_code,
        "metrics": asdict(report.metrics) if report.metrics is not None else None,
        "reasons": list(report.reasons),
    }
    status_code = {"passed": 200, "failed": 422, "unverified": 503}.get(payload["outcome"], 503)
    return JSONResponse(status_code=status_code, content=payload)
