from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REASONS = frozenset(
    {
        "DYNAMIC_URL",
        "AMBIGUOUS_HTTP_METHOD",
        "UNMAPPED_SERVICE_BASE",
        "UNSUPPORTED_CALL_SHAPE",
    }
)


def _require_nonblank(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
    return value.strip()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_path(value: str) -> str:
    path = re.sub(r"/+", "/", value.strip())
    if not path.startswith("/"):
        path = "/" + path
    if path != "/":
        path = path.rstrip("/")
    return path


def _evidence_id(values: dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(values).encode("utf-8")).hexdigest()
    return digest[:32]


@dataclass(frozen=True)
class RepositorySnapshot:
    repo_id: str
    source_revision: str
    root_path: Path
    languages: frozenset[str]

    def __post_init__(self) -> None:
        _require_nonblank(self.repo_id, "repo_id")
        _require_nonblank(self.source_revision, "source_revision")
        normalized = frozenset(
            language.strip().lower() for language in self.languages if isinstance(language, str) and language.strip()
        )
        if not normalized:
            raise ValueError("languages must be non-empty")
        object.__setattr__(self, "languages", normalized)
        if not isinstance(self.root_path, Path):
            object.__setattr__(self, "root_path", Path(self.root_path))


@dataclass(frozen=True)
class Evidence:
    repo_id: str
    source_revision: str
    file_path: str
    start_line: int
    end_line: int
    detector_id: str
    detector_version: str
    evidence_type: str
    subject: str
    confidence: float
    id: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "repo_id",
            "source_revision",
            "file_path",
            "detector_id",
            "detector_version",
            "evidence_type",
            "subject",
        ):
            _require_nonblank(getattr(self, name), name)
        if (
            not isinstance(self.start_line, int)
            or isinstance(self.start_line, bool)
            or self.start_line < 1
            or not isinstance(self.end_line, int)
            or isinstance(self.end_line, bool)
            or self.end_line < self.start_line
        ):
            raise ValueError("invalid line range")
        if not isinstance(self.confidence, (int, float)) or not math.isfinite(self.confidence):
            raise ValueError("invalid confidence")
        if not 0 <= self.confidence <= 1:
            raise ValueError("invalid confidence")
        identity = {
            name: getattr(self, name)
            for name in (
                "repo_id",
                "source_revision",
                "file_path",
                "start_line",
                "end_line",
                "detector_id",
                "detector_version",
                "evidence_type",
                "subject",
            )
        }
        object.__setattr__(self, "id", _evidence_id(identity))


@dataclass(frozen=True)
class ServiceDefinition:
    repo_id: str
    name: str
    source_kind: str
    evidence_id: str

    def __post_init__(self) -> None:
        for name in ("repo_id", "name", "source_kind", "evidence_id"):
            _require_nonblank(getattr(self, name), name)


@dataclass(frozen=True)
class HttpEndpoint:
    repo_id: str
    service_name: str
    role: str
    fact_kind: str
    method: str
    normalized_path: str
    file_path: str
    evidence_id: str
    client_kind: str | None = None
    raw_target: str | None = None
    unresolved_reason: str | None = None

    def __post_init__(self) -> None:
        if self.role not in {"provider", "consumer"}:
            raise ValueError("invalid role")
        method = self.method.strip().upper()
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise ValueError("invalid method")
        for name in (
            "repo_id",
            "service_name",
            "fact_kind",
            "normalized_path",
            "file_path",
            "evidence_id",
        ):
            _require_nonblank(getattr(self, name), name)
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "normalized_path", _normalize_path(self.normalized_path))

    @property
    def canonical_key(self) -> str:
        return f"HTTP|{self.method}|{self.normalized_path}|{self.service_name}"


@dataclass(frozen=True)
class RpcEndpoint:
    repo_id: str
    service_name: str
    role: str
    fact_kind: str
    interface_name: str
    method: str
    group: str
    version: str
    file_path: str
    evidence_id: str
    raw_target: str | None = None
    unresolved_reason: str | None = None

    def __post_init__(self) -> None:
        if self.role not in {"provider", "consumer"}:
            raise ValueError("invalid role")
        for name in (
            "repo_id",
            "service_name",
            "fact_kind",
            "interface_name",
            "method",
            "file_path",
            "evidence_id",
        ):
            _require_nonblank(getattr(self, name), name)
        if not isinstance(self.group, str) or not isinstance(self.version, str):
            raise ValueError("group and version must be strings")
        object.__setattr__(self, "group", self.group.strip() or "-")
        object.__setattr__(self, "version", self.version.strip() or "-")
        for name in ("raw_target", "unresolved_reason"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be nonblank when provided")
        if self.unresolved_reason is not None and self.unresolved_reason not in REASONS:
            raise ValueError("invalid unresolved_reason")

    @property
    def canonical_key(self) -> str:
        return f"DUBBO|{self.group}|{self.interface_name}|{self.method}|{self.version}"


@dataclass(frozen=True)
class MessageEndpoint:
    repo_id: str
    broker: str
    role: str
    topic_or_queue: str
    consumer_group: str
    file_path: str
    evidence_id: str
    raw_target: str | None = None
    unresolved_reason: str | None = None

    def __post_init__(self) -> None:
        if self.broker not in {"kafka", "rabbitmq"} or self.role not in {"producer", "consumer"}:
            raise ValueError("invalid message endpoint")
        for name in ("repo_id", "topic_or_queue", "file_path", "evidence_id"):
            _require_nonblank(getattr(self, name), name)
        if not isinstance(self.consumer_group, str):
            raise ValueError("consumer_group must be a string")
        object.__setattr__(
            self, "consumer_group", "-" if self.role == "producer" else (self.consumer_group.strip() or "-")
        )
        for name in ("raw_target", "unresolved_reason"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be nonblank when provided")
        if self.unresolved_reason is not None and self.unresolved_reason not in REASONS:
            raise ValueError("invalid unresolved_reason")

    @property
    def canonical_key(self) -> str:
        return f"MQ|{self.broker}|{self.topic_or_queue}|{self.consumer_group}"


@dataclass(frozen=True)
class UnresolvedFact:
    repo_id: str
    file_path: str
    evidence_id: str
    reason_code: str
    raw_target: str

    def __post_init__(self) -> None:
        if self.reason_code not in REASONS:
            raise ValueError("invalid reason_code")
        for name in ("repo_id", "file_path", "evidence_id", "raw_target"):
            _require_nonblank(getattr(self, name), name)


@dataclass(frozen=True)
class DetectorFacts:
    detector_id: str
    detector_version: str
    repo_id: str
    source_revision: str
    services: tuple[ServiceDefinition, ...]
    http_endpoints: tuple[HttpEndpoint, ...]
    evidences: tuple[Evidence, ...]
    unresolved: tuple[UnresolvedFact, ...]
    evidence_links: tuple[str, ...] = ()
    endpoint_evidence_links: tuple[tuple[str, tuple[str, ...]], ...] = ()
    rpc_endpoints: tuple[RpcEndpoint, ...] = ()
    message_endpoints: tuple[MessageEndpoint, ...] = ()

    def __post_init__(self) -> None:
        for name in ("detector_id", "detector_version", "repo_id", "source_revision"):
            _require_nonblank(getattr(self, name), name)
        evidence_by_id = {evidence.id: evidence for evidence in self.evidences}
        if len(evidence_by_id) != len(self.evidences):
            raise ValueError("duplicate evidence id")
        nested = (
            *self.services,
            *self.http_endpoints,
            *self.rpc_endpoints,
            *self.message_endpoints,
            *self.evidences,
            *self.unresolved,
        )
        for item in nested:
            if item.repo_id != self.repo_id:
                raise ValueError("nested repo mismatch")
        for evidence in self.evidences:
            if evidence.source_revision != self.source_revision:
                raise ValueError("revision mismatch")
        references = (
            [service.evidence_id for service in self.services]
            + [endpoint.evidence_id for endpoint in self.http_endpoints]
            + [endpoint.evidence_id for endpoint in self.rpc_endpoints]
            + [endpoint.evidence_id for endpoint in self.message_endpoints]
            + [item.evidence_id for item in self.unresolved]
            + list(self.evidence_links)
        )
        if any(reference not in evidence_by_id for reference in references):
            raise ValueError("missing evidence")
        endpoint_keys = {
            endpoint.canonical_key for endpoint in (*self.http_endpoints, *self.rpc_endpoints, *self.message_endpoints)
        }
        if len(self.endpoint_evidence_links) != len({key for key, _ in self.endpoint_evidence_links}):
            raise ValueError("duplicate endpoint evidence link")
        for endpoint_key, linked_ids in self.endpoint_evidence_links:
            if endpoint_key not in endpoint_keys:
                raise ValueError("missing endpoint")
            if any(reference not in evidence_by_id for reference in linked_ids):
                raise ValueError("missing evidence")

    @staticmethod
    def _as_dict(item: Any) -> dict[str, Any]:
        return {name: value for name, value in item.__dict__.items() if name != "id"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "repo_id": self.repo_id,
            "source_revision": self.source_revision,
            "services": [self._as_dict(item) for item in sorted(self.services, key=lambda value: value.name)],
            "http_endpoints": [
                {**self._as_dict(item), "canonical_key": item.canonical_key}
                for item in sorted(self.http_endpoints, key=lambda value: value.canonical_key)
            ],
            "rpc_endpoints": [
                {**self._as_dict(item), "canonical_key": item.canonical_key}
                for item in sorted(self.rpc_endpoints, key=lambda value: value.canonical_key)
            ],
            "message_endpoints": [
                {**self._as_dict(item), "canonical_key": item.canonical_key}
                for item in sorted(self.message_endpoints, key=lambda value: value.canonical_key)
            ],
            "evidences": [
                {**self._as_dict(item), "id": item.id} for item in sorted(self.evidences, key=lambda value: value.id)
            ],
            "unresolved": [
                self._as_dict(item)
                for item in sorted(
                    self.unresolved,
                    key=lambda value: (value.reason_code, value.raw_target, value.file_path),
                )
            ],
            "evidence_links": sorted(self.evidence_links),
            "endpoint_evidence_links": [
                {"endpoint": endpoint, "evidence_ids": list(evidence_ids)}
                for endpoint, evidence_ids in sorted(self.endpoint_evidence_links)
            ],
        }
