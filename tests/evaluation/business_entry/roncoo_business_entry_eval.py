"""Static source validation and dependency preflight for the fixed D0 gold set."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GOLD_SCHEMA_VERSION = "1.0"
CASE_ID_PATTERN = re.compile(r"RONCOO-Q(?:0[1-9]|1[0-2])$")
EVIDENCE_ID_PATTERN = re.compile(r"(?:entry|sym|claim)_[a-z0-9_]+$")
JAVA_TOKEN_PATTERN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*$")
SCOPES = {"entry", "flow", "compensation"}
ENTRY_KINDS = {"http_entry", "service", "worker", "scheduled_job"}
STATES = {"verified", "unverified", "out_of_scope"}
CLAIM_KINDS = {"branch", "behavior", "caveat", "worker_trigger", "workflow", "boundary"}
FORBIDDEN_METRICS = {"accuracy", "recall", "precision", "latency", "scored_cases"}


class GoldValidationError(ValueError):
    """Raised when a gold document does not satisfy the fixed schema."""


@dataclass(frozen=True)
class StaticValidationReport:
    schema_version: str
    mode: str
    status: str
    gold: dict[str, str]
    repo_commit: str | None
    timestamp: str
    reasons: list[str]
    cases: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LivePrerequisiteReport:
    schema_version: str
    mode: str
    status: str
    gold: dict[str, str] | None
    repo_commit: str | None
    timestamp: str
    missing: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _require_mapping(value: object, label: str, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GoldValidationError(f"{label} must be an object")
    keys = set(value)
    if keys - allowed:
        raise GoldValidationError(f"{label} has unknown fields: {sorted(keys - allowed)}")
    if required - keys:
        raise GoldValidationError(f"{label} is missing fields: {sorted(required - keys)}")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldValidationError(f"{label} must be non-blank text")
    return value


def _require_state(value: object, label: str) -> str:
    value = _require_text(value, label)
    if value not in STATES:
        raise GoldValidationError(f"{label} has an invalid state")
    return value


def _validate_source_ref(ref: object, evidence: dict[str, dict[str, Any]], claim_state: str, label: str) -> None:
    ref = _require_text(ref, label)
    match = re.fullmatch(r"(entry|symbol):((?:entry|sym)_[a-z0-9_]+)", ref)
    if match is None:
        raise GoldValidationError(f"{label} has invalid reference grammar")
    kind, evidence_id = match.groups()
    if (kind == "entry") != evidence_id.startswith("entry_"):
        raise GoldValidationError(f"{label} has mismatched reference kind")
    target = evidence.get(evidence_id)
    if target is None:
        raise GoldValidationError(f"{label} references missing evidence")
    if target["state"] != claim_state:
        raise GoldValidationError(f"{label} references evidence with a different state")


def _validate_case(case: object, index: int) -> dict[str, Any]:
    item = _require_mapping(
        case,
        f"cases[{index}]",
        {
            "id",
            "question",
            "scope",
            "expected_status",
            "required_entries",
            "required_symbols",
            "claims",
            "required_flow",
            "notes",
        },
        {
            "id",
            "question",
            "scope",
            "expected_status",
            "required_entries",
            "required_symbols",
            "claims",
            "required_flow",
        },
    )
    case_id = _require_text(item["id"], f"cases[{index}].id")
    if CASE_ID_PATTERN.fullmatch(case_id) is None:
        raise GoldValidationError("case id must be RONCOO-Q01 through RONCOO-Q12")
    _require_text(item["question"], f"{case_id}.question")
    if item["scope"] not in SCOPES:
        raise GoldValidationError(f"{case_id}.scope is invalid")
    if item["expected_status"] != "found":
        raise GoldValidationError(f"{case_id}.expected_status must be found")
    entries = item["required_entries"]
    symbols = item["required_symbols"]
    claims = item["claims"]
    flow = item["required_flow"]
    if not isinstance(entries, list) or not entries:
        raise GoldValidationError(f"{case_id}.required_entries must be non-empty")
    if not isinstance(symbols, list) or not symbols:
        raise GoldValidationError(f"{case_id}.required_symbols must be non-empty")
    if not isinstance(claims, list) or not claims:
        raise GoldValidationError(f"{case_id}.claims must be non-empty")
    if not isinstance(flow, list):
        raise GoldValidationError(f"{case_id}.required_flow must be a list")
    if "notes" in item and not isinstance(item["notes"], str):
        raise GoldValidationError(f"{case_id}.notes must be text")

    evidence: dict[str, dict[str, Any]] = {}
    for entry_index, entry in enumerate(entries):
        entry = _require_mapping(
            entry,
            f"{case_id}.required_entries[{entry_index}]",
            {"id", "path", "symbol", "route", "kind", "state", "line_hint"},
            {"id", "path", "symbol", "route", "kind", "state"},
        )
        entry_id = _require_text(entry["id"], "entry.id")
        if not entry_id.startswith("entry_") or EVIDENCE_ID_PATTERN.fullmatch(entry_id) is None or entry_id in evidence:
            raise GoldValidationError(f"{case_id} has duplicate or invalid entry id")
        path = _require_text(entry["path"], "entry.path")
        if Path(path).is_absolute() or ".." in Path(path).parts or not path.endswith(".java"):
            raise GoldValidationError("entry.path must be a relative Java path without traversal")
        _require_text(entry["symbol"], "entry.symbol")
        route = entry["route"]
        if route is not None and (not isinstance(route, str) or not route.startswith("/")):
            raise GoldValidationError("entry.route must be null or begin with /")
        if entry["kind"] not in ENTRY_KINDS:
            raise GoldValidationError("entry.kind is invalid")
        _require_state(entry["state"], "entry.state")
        if "line_hint" in entry and (
            not isinstance(entry["line_hint"], int) or isinstance(entry["line_hint"], bool) or entry["line_hint"] < 1
        ):
            raise GoldValidationError("entry.line_hint must be a positive integer")
        evidence[entry_id] = entry
    for symbol_index, symbol in enumerate(symbols):
        symbol = _require_mapping(
            symbol, f"{case_id}.required_symbols[{symbol_index}]", {"id", "token", "state"}, {"id", "token", "state"}
        )
        symbol_id = _require_text(symbol["id"], "symbol.id")
        if (
            not symbol_id.startswith("sym_")
            or EVIDENCE_ID_PATTERN.fullmatch(symbol_id) is None
            or symbol_id in evidence
        ):
            raise GoldValidationError(f"{case_id} has duplicate or invalid symbol id")
        token = _require_text(symbol["token"], "symbol.token")
        if JAVA_TOKEN_PATTERN.fullmatch(token) is None:
            raise GoldValidationError("symbol.token must be a Java identifier")
        _require_state(symbol["state"], "symbol.state")
        evidence[symbol_id] = symbol
    for claim_index, claim in enumerate(claims):
        claim = _require_mapping(
            claim,
            f"{case_id}.claims[{claim_index}]",
            {"id", "kind", "state", "text", "source_refs"},
            {"id", "kind", "state", "text", "source_refs"},
        )
        claim_id = _require_text(claim["id"], "claim.id")
        if not claim_id.startswith("claim_") or EVIDENCE_ID_PATTERN.fullmatch(claim_id) is None:
            raise GoldValidationError("claim.id is invalid")
        if claim["kind"] not in CLAIM_KINDS:
            raise GoldValidationError("claim.kind is invalid")
        state = _require_state(claim["state"], "claim.state")
        _require_text(claim["text"], "claim.text")
        refs = claim["source_refs"]
        if not isinstance(refs, list):
            raise GoldValidationError("claim.source_refs must be a list")
        if state == "verified" and not refs:
            raise GoldValidationError("verified claims require source references")
        if state == "out_of_scope" and refs:
            raise GoldValidationError("out_of_scope claims cannot have source references")
        for ref_index, ref in enumerate(refs):
            _validate_source_ref(ref, evidence, state, f"{case_id}.{claim_id}.source_refs[{ref_index}]")
    for flow_index, ref in enumerate(flow):
        _validate_source_ref(ref, evidence, "verified", f"{case_id}.required_flow[{flow_index}]")
    return item


def load_gold(path: Path) -> dict[str, Any]:
    """Load and strictly validate the fixed D0 JSON gold document."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GoldValidationError(f"unable to read gold: {error}") from error
    gold = _require_mapping(
        document, "gold", {"schema_version", "source", "repo", "cases"}, {"schema_version", "source", "repo", "cases"}
    )
    if gold["schema_version"] != GOLD_SCHEMA_VERSION:
        raise GoldValidationError("unsupported gold schema_version")
    source = _require_mapping(gold["source"], "source", {"kind", "siyuan_doc_id"}, {"kind", "siyuan_doc_id"})
    if source["kind"] != "human_ground_truth":
        raise GoldValidationError("source.kind must be human_ground_truth")
    _require_text(source["siyuan_doc_id"], "source.siyuan_doc_id")
    repo = _require_mapping(
        gold["repo"], "repo", {"name", "commit", "relative_root_required"}, {"name", "commit", "relative_root_required"}
    )
    _require_text(repo["name"], "repo.name")
    commit = _require_text(repo["commit"], "repo.commit")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None or repo["relative_root_required"] is not True:
        raise GoldValidationError("repo identity is invalid")
    cases = gold["cases"]
    if not isinstance(cases, list) or len(cases) != 12:
        raise GoldValidationError("gold must contain exactly 12 cases")
    validated = [_validate_case(case, index) for index, case in enumerate(cases)]
    ids = [case["id"] for case in validated]
    if ids != [f"RONCOO-Q{number:02d}" for number in range(1, 13)] or len(set(ids)) != 12:
        raise GoldValidationError("case IDs must be unique and ordered RONCOO-Q01 through RONCOO-Q12")
    return gold


def _git_commit(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, check=False, text=True
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _token_present(text: str, token: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_$]){re.escape(token)}(?![A-Za-z0-9_$])", text) is not None


_REQUEST_MAPPING_PATTERN = re.compile(r"@RequestMapping\s*(?:\((?P<body>[^)]*)\))?")
_CLASS_PATTERN = re.compile(r"\bclass\s+[A-Za-z_$][A-Za-z0-9_$]*\b")


def _mask_java_comments(text: str) -> str:
    """Replace Java comments with spaces while retaining line/position structure."""
    return re.sub(r"//[^\r\n]*|/\*.*?\*/", lambda match: re.sub(r"[^\r\n]", " ", match.group(0)), text, flags=re.DOTALL)


def _brace_depths(text: str) -> list[int]:
    depths = [0] * (len(text) + 1)
    depth = 0
    for index, char in enumerate(text):
        depths[index] = depth
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
    depths[-1] = depth
    return depths


def _mapping_paths(annotation: re.Match[str]) -> list[str]:
    body = annotation.group("body") or ""
    assigned = re.findall(r"(?:value|path)\s*=\s*(?:\{\s*)?\"([^\"]*)\"", body)
    if assigned:
        return assigned
    bare = re.match(r"\s*\"([^\"]*)\"", body)
    return [bare.group(1)] if bare else [""]


def _join_route(prefix: str, suffix: str) -> str:
    return "/" + "/".join(part.strip("/") for part in (prefix, suffix) if part.strip("/"))


def _spring_route_present(text: str, symbol: str, route: str) -> bool:
    text = _mask_java_comments(text)
    mappings = list(_REQUEST_MAPPING_PATTERN.finditer(text))
    if not mappings:
        return False
    depths = _brace_depths(text)
    classes: list[tuple[re.Match[str], int, int]] = []
    for class_match in _CLASS_PATTERN.finditer(text):
        open_brace = text.find("{", class_match.end())
        if open_brace < 0:
            continue
        body_depth = depths[open_brace] + 1
        close_brace = len(text)
        for index in range(open_brace + 1, len(text)):
            if text[index] == "}" and depths[index] < body_depth:
                close_brace = index
                break
        classes.append((class_match, open_brace, close_brace))
    for method_match in re.finditer(rf"\b{re.escape(symbol)}\s*\(", text):
        enclosing = next(
            (
                item
                for item in reversed(classes)
                if item[1] < method_match.start() < item[2] and depths[method_match.start()] >= depths[item[1]] + 1
            ),
            None,
        )
        if enclosing is None:
            continue
        class_match, open_brace, _ = enclosing
        class_depth = depths[class_match.start()]
        prior_boundary = max(
            (
                item[2]
                for item in classes
                if item[0].start() < class_match.start() and depths[item[0].start()] == class_depth
            ),
            default=0,
        )
        class_mapping = next(
            (
                item
                for item in reversed(mappings)
                if item.end() <= class_match.start()
                and depths[item.start()] == class_depth
                and item.end() > prior_boundary
            ),
            None,
        )
        class_paths = _mapping_paths(class_mapping) if class_mapping else [""]
        method_mapping = next(
            (
                item
                for item in reversed(mappings)
                if open_brace < item.start() < method_match.start()
                and depths[item.start()] == depths[method_match.start()]
            ),
            None,
        )
        if method_mapping is None:
            continue
        between = text[method_mapping.end() : method_match.start()]
        if "}" in between or ";" in between:
            continue
        for prefix in class_paths:
            for suffix in _mapping_paths(method_mapping):
                if _join_route(prefix, suffix) == route:
                    return True
    return False


def validate_gold_against_source(
    gold: dict[str, Any], repo_root: Path, *, commit_getter: Callable[[Path], str | None] = _git_commit
) -> StaticValidationReport:
    """Validate verified D0 evidence against the pinned source tree without querying OntoAgent."""
    repo_root = repo_root.resolve()
    commit = commit_getter(repo_root)
    identity = {"name": gold["repo"]["name"], "commit": gold["repo"]["commit"]}
    if commit != gold["repo"]["commit"]:
        return StaticValidationReport(
            GOLD_SCHEMA_VERSION, "static", "failed", identity, commit, _timestamp(), ["SOURCE_COMMIT_MISMATCH"], []
        )
    all_java = list(repo_root.rglob("*.java")) if repo_root.is_dir() else []
    all_source_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in all_java)
    case_reports: list[dict[str, Any]] = []
    for case in gold["cases"]:
        reasons: list[str] = []
        entry_texts: list[str] = []
        for entry in case["required_entries"]:
            if entry["state"] != "verified":
                continue
            target = (repo_root / entry["path"]).resolve()
            if repo_root not in target.parents or not target.is_file():
                reasons.append("ENTRY_FILE_MISSING")
                continue
            try:
                text = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                reasons.append("ENTRY_FILE_UNREADABLE")
                continue
            entry_texts.append(text)
            if not _token_present(text, entry["symbol"]):
                reasons.append("ENTRY_SYMBOL_MISSING")
            if entry["route"] is not None and (
                (entry["kind"] == "http_entry" and not _spring_route_present(text, entry["symbol"], entry["route"]))
                or (entry["kind"] != "http_entry" and entry["route"] not in text)
            ):
                reasons.append("ENTRY_ROUTE_MISSING")
        source_text = "\n".join([*entry_texts, all_source_text])
        for symbol in case["required_symbols"]:
            if symbol["state"] == "verified" and not _token_present(source_text, symbol["token"]):
                reasons.append("SYMBOL_TOKEN_MISSING")
        reasons = sorted(set(reasons))
        case_reports.append({"id": case["id"], "status": "passed" if not reasons else "failed", "reasons": reasons})
    failed = any(case["status"] == "failed" for case in case_reports)
    return StaticValidationReport(
        GOLD_SCHEMA_VERSION,
        "static",
        "failed" if failed else "passed",
        identity,
        commit,
        _timestamp(),
        [],
        case_reports,
    )


def check_live_prerequisites(
    repo_root: Path, *, gold: dict[str, Any] | None = None, commit_getter: Callable[[Path], str | None] = _git_commit
) -> LivePrerequisiteReport:
    """Check required services and Maven without starting, installing, or querying anything."""
    requirements = [("neo4j_bolt:7687", 7687), ("nebula_graphd:9669", 9669), ("ollama:11434", 11434)]
    missing: list[str] = []
    for name, port in requirements:
        try:
            connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        except OSError:
            missing.append(name)
    if shutil.which("mvn") is None:
        missing.append("maven")
    repo_root = repo_root.resolve()
    commit = commit_getter(repo_root)
    if commit is None:
        missing.append("source_commit")
    if gold is not None and commit != gold["repo"]["commit"]:
        missing.append("source_commit")
    identity = None if gold is None else {"name": gold["repo"]["name"], "commit": gold["repo"]["commit"]}
    missing = sorted(set(missing))
    return LivePrerequisiteReport(
        GOLD_SCHEMA_VERSION, "preflight", "ready" if not missing else "blocked", identity, commit, _timestamp(), missing
    )


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if output is None:
        print(payload)
    else:
        output.write_text(f"{payload}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run fixed-source static validation or dependency preflight."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--mode", choices=("static", "preflight"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        gold = load_gold(args.gold)
    except GoldValidationError as error:
        parser.error(str(error))
    report = (
        validate_gold_against_source(gold, args.repo)
        if args.mode == "static"
        else check_live_prerequisites(args.repo, gold=gold)
    )
    data = report.to_dict()
    _write_report(data, args.output)
    if args.mode == "preflight":
        return 0 if data["status"] == "ready" else 2
    return 0 if data["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
