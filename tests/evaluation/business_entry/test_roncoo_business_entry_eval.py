"""Tests for the static-only roncoo-pay business-entry harness."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.evaluation.business_entry.roncoo_business_entry_eval import (
    GoldValidationError,
    check_live_prerequisites,
    load_gold,
    validate_gold_against_source,
)

ROOT = Path(__file__).parent
GOLD_PATH = ROOT / "roncoo_pay_gold.json"
RONCOO_ROOT = Path("/opt/data/workspace/roncoo-pay")
FORBIDDEN_METRICS = {"accuracy", "recall", "precision", "latency", "scored_cases"}


def _write_gold(path: Path, gold: dict[str, object]) -> Path:
    path.write_text(json.dumps(gold), encoding="utf-8")
    return path


def _gold_copy() -> dict[str, object]:
    return copy.deepcopy(load_gold(GOLD_PATH))


def _assert_no_metrics(value: object) -> None:
    if isinstance(value, dict):
        assert not (FORBIDDEN_METRICS & set(value))
        for nested in value.values():
            _assert_no_metrics(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_metrics(nested)


def test_gold_has_fixed_twelve_case_structure() -> None:
    gold = load_gold(GOLD_PATH)

    assert gold["repo"]["commit"] == "981e19475e9d794cbb14cb101883e1d0e1a36e9d"
    assert [case["id"] for case in gold["cases"]] == [f"RONCOO-Q{number:02d}" for number in range(1, 13)]
    for case in gold["cases"]:
        assert case["required_entries"]
        assert case["required_symbols"]
        assert case["claims"]


def test_gold_protects_d0_qualified_cases() -> None:
    cases = {case["id"]: case for case in load_gold(GOLD_PATH)["cases"]}

    routes = {
        case_id: [entry["route"] for entry in cases[case_id]["required_entries"]]
        for case_id in ("RONCOO-Q01", "RONCOO-Q05", "RONCOO-Q06", "RONCOO-Q09", "RONCOO-Q10")
    }
    assert routes == {
        "RONCOO-Q01": ["/scanPay/initPay"],
        "RONCOO-Q05": ["/f2fPay/doPay"],
        "RONCOO-Q06": ["/scanPayNotify/notify/{payWayCode}"],
        "RONCOO-Q09": ["/sett/launchSett"],
        "RONCOO-Q10": ["/sett/audit", "/sett/remit"],
    }
    assert any(claim["kind"] == "caveat" and claim["state"] == "verified" for claim in cases["RONCOO-Q07"]["claims"])
    assert any(entry["kind"] == "worker" for entry in cases["RONCOO-Q08"]["required_entries"])
    assert any(claim["kind"] == "worker_trigger" for claim in cases["RONCOO-Q08"]["claims"])
    assert cases["RONCOO-Q10"]["required_flow"] == ["entry:entry_audit", "entry:entry_remit"]
    q11 = cases["RONCOO-Q11"]
    assert q11["question"] == "结算日汇总的核心服务在哪里？"
    assert q11["required_entries"] == [
        {
            "id": "entry_daily_settlement_collect",
            "path": "roncoo-pay-service/src/main/java/com/roncoo/pay/account/service/impl/RpSettHandleServiceImpl.java",
            "symbol": "dailySettlementCollect",
            "route": None,
            "kind": "service",
            "state": "verified",
            "line_hint": 86,
        }
    ]
    assert q11["required_symbols"] == [
        {"id": "sym_sett_collect_success", "token": "settCollectSuccess", "state": "verified"}
    ]
    assert q11["claims"] == [
        {
            "id": "claim_daily_settlement_collection",
            "kind": "behavior",
            "state": "verified",
            "text": "结算日汇总服务汇总账户历史并更新可结算金额。",
            "source_refs": ["entry:entry_daily_settlement_collect", "symbol:sym_sett_collect_success"],
        }
    ]
    assert q11["required_flow"] == ["entry:entry_daily_settlement_collect", "symbol:sym_sett_collect_success"]
    q12 = cases["RONCOO-Q12"]
    assert any(symbol["state"] == "unverified" for symbol in q12["required_symbols"])
    assert any(claim["kind"] == "boundary" and claim["state"] == "out_of_scope" for claim in q12["claims"])


@pytest.mark.skipif(not RONCOO_ROOT.is_dir(), reason="roncoo-pay checkout is not present locally")
def test_gold_static_validation_passes_against_fixed_source() -> None:
    report = validate_gold_against_source(load_gold(GOLD_PATH), RONCOO_ROOT).to_dict()

    assert report["status"] == "passed"
    assert all(case["status"] == "passed" for case in report["cases"])
    _assert_no_metrics(report)


@pytest.mark.parametrize(
    ("mutate"),
    [
        lambda gold: gold["cases"].append(copy.deepcopy(gold["cases"][0])),
        lambda gold: gold["cases"][0].update({"scope": "unknown"}),
        lambda gold: gold["cases"][0].update({"expected_status": "unknown"}),
        lambda gold: gold["cases"][0]["required_entries"][0].update({"path": "../escape.java"}),
        lambda gold: gold["cases"][0]["required_entries"][0].update({"route": "route"}),
        lambda gold: gold["cases"][0]["required_entries"][0].update({"kind": "unknown"}),
        lambda gold: gold["cases"][0]["required_entries"][0].update({"state": "unknown"}),
        lambda gold: gold["cases"][0]["required_symbols"][0].update({"state": "unknown"}),
        lambda gold: gold["cases"][0]["claims"][0].update({"state": "unknown"}),
        lambda gold: gold["cases"][0]["claims"][0].update({"kind": "unknown"}),
        lambda gold: gold["cases"][0].update({"required_entries": []}),
        lambda gold: gold["cases"][0].update({"required_symbols": []}),
    ],
)
def test_load_gold_rejects_malformed_gold(tmp_path: Path, mutate) -> None:
    gold = _gold_copy()
    mutate(gold)

    with pytest.raises(GoldValidationError):
        load_gold(_write_gold(tmp_path / "gold.json", gold))


def test_load_gold_rejects_invalid_evidence_references(tmp_path: Path) -> None:
    gold = _gold_copy()
    gold["cases"][0]["claims"][0]["source_refs"] = ["entry:missing"]

    with pytest.raises(GoldValidationError):
        load_gold(_write_gold(tmp_path / "gold.json", gold))


def test_static_validation_reports_commit_file_symbol_and_route_failures(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Sample.java").write_text(
        'class Sample { void initPayment() {} String route = "/other"; }', encoding="utf-8"
    )
    gold = _gold_copy()
    case = gold["cases"][0]
    case["required_entries"] = [
        {
            "id": "entry_sample",
            "path": "Sample.java",
            "symbol": "initPay",
            "route": "/expected",
            "kind": "http_entry",
            "state": "verified",
        }
    ]
    case["required_symbols"] = [{"id": "sym_init", "token": "initPay", "state": "verified"}]
    case["claims"] = [
        {
            "id": "claim_sample",
            "kind": "behavior",
            "state": "verified",
            "text": "sample",
            "source_refs": ["entry:entry_sample"],
        }
    ]
    case["required_flow"] = ["entry:entry_sample"]

    report = validate_gold_against_source(gold, repo, commit_getter=lambda _: gold["repo"]["commit"]).to_dict()

    reasons = {reason for case_report in report["cases"] for reason in case_report["reasons"]}
    assert {"ENTRY_SYMBOL_MISSING", "ENTRY_ROUTE_MISSING", "SYMBOL_TOKEN_MISSING"} <= reasons

    mismatch = validate_gold_against_source(gold, repo, commit_getter=lambda _: "wrong").to_dict()
    assert mismatch["status"] == "failed"
    assert mismatch["reasons"] == ["SOURCE_COMMIT_MISMATCH"]


@pytest.mark.parametrize(
    ("class_mapping", "method_mapping", "route", "expected_status"),
    [
        ('@RequestMapping(value = "/api")', '@RequestMapping("/pay")', "/api/pay", "passed"),
        (
            '@RequestMapping(path = "/api/")',
            '@RequestMapping(value = "/pay", method = RequestMethod.POST)',
            "/api/pay",
            "passed",
        ),
        ('@RequestMapping("/api")', '@RequestMapping(path = "/pay")', "/api/pay", "passed"),
        ('@RequestMapping(value = "/api")', '@RequestMapping("/pay")', "/api/payment", "failed"),
    ],
)
def test_static_validation_recognizes_composed_spring_routes(
    tmp_path: Path, class_mapping: str, method_mapping: str, route: str, expected_status: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Sample.java").write_text(
        f"{class_mapping}\nclass Sample {{\n{method_mapping}\nvoid handle() {{}}\n}}", encoding="utf-8"
    )
    gold = _gold_copy()
    case = gold["cases"][0]
    case["required_entries"] = [
        {
            "id": "entry_sample",
            "path": "Sample.java",
            "symbol": "handle",
            "route": route,
            "kind": "http_entry",
            "state": "verified",
        }
    ]
    case["required_symbols"] = [{"id": "sym_handle", "token": "handle", "state": "verified"}]
    case["claims"] = [
        {
            "id": "claim_sample",
            "kind": "behavior",
            "state": "verified",
            "text": "sample",
            "source_refs": ["entry:entry_sample"],
        }
    ]
    case["required_flow"] = ["entry:entry_sample"]

    report = validate_gold_against_source(gold, repo, commit_getter=lambda _: gold["repo"]["commit"]).to_dict()

    assert report["cases"][0]["status"] == expected_status
    if expected_status == "failed":
        assert "ENTRY_ROUTE_MISSING" in report["cases"][0]["reasons"]


def _route_fixture_gold(symbol: str, route: str) -> dict[str, object]:
    gold = _gold_copy()
    case = gold["cases"][0]
    case["required_entries"] = [
        {
            "id": "entry_sample",
            "path": "Sample.java",
            "symbol": symbol,
            "route": route,
            "kind": "http_entry",
            "state": "verified",
        }
    ]
    case["required_symbols"] = [{"id": "sym_sample", "token": symbol, "state": "verified"}]
    case["claims"] = [
        {
            "id": "claim_sample",
            "kind": "behavior",
            "state": "verified",
            "text": "sample",
            "source_refs": ["entry:entry_sample"],
        }
    ]
    case["required_flow"] = ["entry:entry_sample"]
    return gold


def test_spring_route_recognizes_second_class_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Sample.java").write_text(
        '@RequestMapping("/first")\nclass First {\n@RequestMapping("/pay")\nvoid handleFirst() {}\n}\n'
        '@RequestMapping("/second")\nclass Second {\n@RequestMapping("/pay")\nvoid handle() {}\n}',
        encoding="utf-8",
    )

    report = validate_gold_against_source(
        _route_fixture_gold("handle", "/second/pay"),
        repo,
        commit_getter=lambda _: "981e19475e9d794cbb14cb101883e1d0e1a36e9d",
    ).to_dict()

    assert report["cases"][0]["status"] == "passed"


def test_spring_route_does_not_leak_prefix_from_previous_class(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Sample.java").write_text(
        '@RequestMapping("/first")\nclass First {\n@RequestMapping("/pay")\nvoid handleFirst() {}\n}\n'
        'class Second {\n@RequestMapping("/pay")\nvoid handle() {}\n}',
        encoding="utf-8",
    )

    report = validate_gold_against_source(
        _route_fixture_gold("handle", "/first/pay"),
        repo,
        commit_getter=lambda _: "981e19475e9d794cbb14cb101883e1d0e1a36e9d",
    ).to_dict()

    assert report["cases"][0]["status"] == "failed"
    assert "ENTRY_ROUTE_MISSING" in report["cases"][0]["reasons"]


def test_spring_route_ignores_commented_request_mapping(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Sample.java").write_text(
        'class Sample {\n// @RequestMapping("/pay")\nvoid handle() {}\n}', encoding="utf-8"
    )

    report = validate_gold_against_source(
        _route_fixture_gold("handle", "/pay"), repo, commit_getter=lambda _: "981e19475e9d794cbb14cb101883e1d0e1a36e9d"
    ).to_dict()

    assert report["cases"][0]["status"] == "failed"
    assert "ENTRY_ROUTE_MISSING" in report["cases"][0]["reasons"]


def test_spring_route_requires_method_level_mapping(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Sample.java").write_text('@RequestMapping("/api")\nclass Sample {\nvoid handle() {}\n}', encoding="utf-8")

    report = validate_gold_against_source(
        _route_fixture_gold("handle", "/api"), repo, commit_getter=lambda _: "981e19475e9d794cbb14cb101883e1d0e1a36e9d"
    ).to_dict()

    assert report["cases"][0]["status"] == "failed"
    assert "ENTRY_ROUTE_MISSING" in report["cases"][0]["reasons"]


def test_preflight_ready_and_missing_are_metric_free(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("socket.create_connection", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/mvn")
    ready = check_live_prerequisites(tmp_path, commit_getter=lambda _: "abc").to_dict()
    assert ready["status"] == "ready"
    assert ready["missing"] == []

    def unavailable(*_args, **_kwargs):
        raise OSError("down")

    monkeypatch.setattr("socket.create_connection", unavailable)
    monkeypatch.setattr("shutil.which", lambda _: None)
    blocked = check_live_prerequisites(tmp_path, commit_getter=lambda _: "abc").to_dict()
    assert blocked["status"] == "blocked"
    assert blocked["missing"] == ["maven", "nebula_graphd:9669", "neo4j_bolt:7687", "ollama:11434"]
    _assert_no_metrics(ready)
    _assert_no_metrics(blocked)


def test_cli_writes_json_and_uses_mode_exit_codes(tmp_path: Path) -> None:
    static_output = tmp_path / "static.json"
    static = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.evaluation.business_entry.roncoo_business_entry_eval",
            "--gold",
            str(GOLD_PATH),
            "--repo",
            str(tmp_path),
            "--mode",
            "static",
            "--output",
            str(static_output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert static.returncode == 1
    assert json.loads(static_output.read_text(encoding="utf-8"))["mode"] == "static"

    preflight_output = tmp_path / "preflight.json"
    preflight = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.evaluation.business_entry.roncoo_business_entry_eval",
            "--gold",
            str(GOLD_PATH),
            "--repo",
            str(tmp_path),
            "--mode",
            "preflight",
            "--output",
            str(preflight_output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert preflight.returncode == 2
    assert json.loads(preflight_output.read_text(encoding="utf-8"))["mode"] == "preflight"
