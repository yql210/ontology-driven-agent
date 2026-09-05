from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

METHOD_UNRESOLVED_REASONS = frozenset(
    {
        "AMBIGUOUS_TARGET",
        "DYNAMIC_TARGET",
        "IDENTITY_MISMATCH",
        "MISSING_DECLARATION",
        "MISSING_IMPLEMENTATION",
        "UNSUPPORTED_TARGET_SHAPE",
    }
)


def _require_nonblank(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
    return value.strip()


def _require_identity(repo_id: str, module_id: str, service_id: str, source_revision: str, generation_id: str) -> None:
    for name, value in (
        ("repo_id", repo_id),
        ("module_id", module_id),
        ("service_id", service_id),
        ("source_revision", source_revision),
        ("generation_id", generation_id),
    ):
        _require_nonblank(value, name)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()[:32]


def _require_evidence_ids(value: tuple[str, ...]) -> None:
    if type(value) is not tuple or not value:
        raise ValueError("evidence_ids must be a non-empty tuple")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("evidence_ids must contain nonblank strings")
    if len(set(value)) != len(value):
        raise ValueError("evidence_ids must be unique")


def _optional_nonblank(value: str | None, name: str) -> None:
    if value is not None:
        _require_nonblank(value, name)


@dataclass(frozen=True)
class MethodEvidence:
    repo_id: str
    module_id: str
    service_id: str
    source_revision: str
    generation_id: str
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
        _require_identity(self.repo_id, self.module_id, self.service_id, self.source_revision, self.generation_id)
        for name in ("file_path", "detector_id", "detector_version", "evidence_type", "subject"):
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
        object.__setattr__(self, "id", _stable_id(self.to_dict(include_id=False)))

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "repo_id": self.repo_id,
            "module_id": self.module_id,
            "service_id": self.service_id,
            "source_revision": self.source_revision,
            "generation_id": self.generation_id,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "evidence_type": self.evidence_type,
            "subject": self.subject,
            "confidence": self.confidence,
        }
        if include_id:
            result["id"] = self.id
        return result


@dataclass(frozen=True)
class ServiceOperation:
    repo_id: str
    module_id: str
    service_id: str
    source_revision: str
    generation_id: str
    role: str
    declaring_interface_fqcn: str
    operation_name: str
    canonical_signature: str
    evidence_ids: tuple[str, ...]
    group: str | None = None
    version: str | None = None
    alias: str | None = None
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identity(self.repo_id, self.module_id, self.service_id, self.source_revision, self.generation_id)
        if self.role not in {"provider", "consumer"}:
            raise ValueError("role must be provider or consumer")
        for name in ("declaring_interface_fqcn", "operation_name", "canonical_signature"):
            _require_nonblank(getattr(self, name), name)
        _require_evidence_ids(self.evidence_ids)
        for name in ("group", "version", "alias"):
            _optional_nonblank(getattr(self, name), name)
        object.__setattr__(self, "id", _stable_id({"kind": "service_operation", **self._identity()}))

    @property
    def display_name(self) -> str:
        return f"{self.declaring_interface_fqcn}.{self.operation_name}"

    @property
    def canonical_key(self) -> str:
        return self.canonical_signature

    def _identity(self) -> dict[str, str | None]:
        return {
            "repo_id": self.repo_id,
            "module_id": self.module_id,
            "service_id": self.service_id,
            "source_revision": self.source_revision,
            "generation_id": self.generation_id,
            "role": self.role,
            "declaring_interface_fqcn": self.declaring_interface_fqcn,
            "operation_name": self.operation_name,
            "canonical_signature": self.canonical_signature,
            "group": self.group,
            "version": self.version,
            "alias": self.alias,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._identity(),
            "evidence_ids": list(self.evidence_ids),
            "id": self.id,
            "display_name": self.display_name,
        }


@dataclass(frozen=True)
class ImplementationMethod:
    repo_id: str
    module_id: str
    service_id: str
    source_revision: str
    generation_id: str
    class_fqcn: str
    method_name: str
    canonical_signature: str
    file_path: str
    evidence_ids: tuple[str, ...]
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identity(self.repo_id, self.module_id, self.service_id, self.source_revision, self.generation_id)
        for name in ("class_fqcn", "method_name", "canonical_signature", "file_path"):
            _require_nonblank(getattr(self, name), name)
        _require_evidence_ids(self.evidence_ids)
        object.__setattr__(self, "id", _stable_id({"kind": "implementation_method", **self._identity()}))

    def _identity(self) -> dict[str, str]:
        return {
            "repo_id": self.repo_id,
            "module_id": self.module_id,
            "service_id": self.service_id,
            "source_revision": self.source_revision,
            "generation_id": self.generation_id,
            "class_fqcn": self.class_fqcn,
            "method_name": self.method_name,
            "canonical_signature": self.canonical_signature,
            "file_path": self.file_path,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._identity(), "evidence_ids": list(self.evidence_ids), "id": self.id}


@dataclass(frozen=True)
class ConsumerMethodCall:
    repo_id: str
    module_id: str
    service_id: str
    source_revision: str
    generation_id: str
    caller_implementation_id: str
    target_reference: str
    target_kind: str
    evidence_ids: tuple[str, ...]
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identity(self.repo_id, self.module_id, self.service_id, self.source_revision, self.generation_id)
        for name in ("caller_implementation_id", "target_reference"):
            _require_nonblank(getattr(self, name), name)
        if self.target_kind not in {"operation", "endpoint"}:
            raise ValueError("target_kind must be operation or endpoint")
        _require_evidence_ids(self.evidence_ids)
        object.__setattr__(self, "id", _stable_id({"kind": "consumer_method_call", **self._identity()}))

    def _identity(self) -> dict[str, str]:
        return {
            "repo_id": self.repo_id,
            "module_id": self.module_id,
            "service_id": self.service_id,
            "source_revision": self.source_revision,
            "generation_id": self.generation_id,
            "caller_implementation_id": self.caller_implementation_id,
            "target_reference": self.target_reference,
            "target_kind": self.target_kind,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._identity(), "evidence_ids": list(self.evidence_ids), "id": self.id}


@dataclass(frozen=True)
class OperationBinding:
    repo_id: str
    module_id: str
    service_id: str
    source_revision: str
    generation_id: str
    provider_endpoint_reference: str
    operation_id: str
    implementation_id: str | None
    evidence_ids: tuple[str, ...]
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identity(self.repo_id, self.module_id, self.service_id, self.source_revision, self.generation_id)
        for name in ("provider_endpoint_reference", "operation_id"):
            _require_nonblank(getattr(self, name), name)
        _optional_nonblank(self.implementation_id, "implementation_id")
        _require_evidence_ids(self.evidence_ids)
        object.__setattr__(self, "id", _stable_id({"kind": "operation_binding", **self._identity()}))

    def _identity(self) -> dict[str, str | None]:
        return {
            "repo_id": self.repo_id,
            "module_id": self.module_id,
            "service_id": self.service_id,
            "source_revision": self.source_revision,
            "generation_id": self.generation_id,
            "provider_endpoint_reference": self.provider_endpoint_reference,
            "operation_id": self.operation_id,
            "implementation_id": self.implementation_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._identity(), "evidence_ids": list(self.evidence_ids), "id": self.id}


@dataclass(frozen=True)
class MethodUnresolved:
    repo_id: str
    module_id: str
    service_id: str
    source_revision: str
    generation_id: str
    reason_code: str
    subject: str
    evidence_ids: tuple[str, ...]
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identity(self.repo_id, self.module_id, self.service_id, self.source_revision, self.generation_id)
        if self.reason_code not in METHOD_UNRESOLVED_REASONS:
            raise ValueError("invalid reason_code")
        _require_nonblank(self.subject, "subject")
        _require_evidence_ids(self.evidence_ids)
        object.__setattr__(self, "id", _stable_id({"kind": "method_unresolved", **self._identity()}))

    def _identity(self) -> dict[str, str]:
        return {
            "repo_id": self.repo_id,
            "module_id": self.module_id,
            "service_id": self.service_id,
            "source_revision": self.source_revision,
            "generation_id": self.generation_id,
            "reason_code": self.reason_code,
            "subject": self.subject,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._identity(), "evidence_ids": list(self.evidence_ids), "id": self.id}


@dataclass(frozen=True)
class MethodFacts:
    detector_id: str
    detector_version: str
    repo_id: str
    source_revision: str
    generation_id: str
    operations: tuple[ServiceOperation, ...]
    implementations: tuple[ImplementationMethod, ...]
    consumer_calls: tuple[ConsumerMethodCall, ...]
    bindings: tuple[OperationBinding, ...]
    evidences: tuple[MethodEvidence, ...]
    unresolved: tuple[MethodUnresolved, ...]

    def __post_init__(self) -> None:
        for name in ("detector_id", "detector_version", "repo_id", "source_revision", "generation_id"):
            _require_nonblank(getattr(self, name), name)
        collections = (
            self.operations,
            self.implementations,
            self.consumer_calls,
            self.bindings,
            self.evidences,
            self.unresolved,
        )
        if any(type(items) is not tuple for items in collections):
            raise ValueError("method facts collections must be tuples")
        expected_types = (
            (self.operations, ServiceOperation),
            (self.implementations, ImplementationMethod),
            (self.consumer_calls, ConsumerMethodCall),
            (self.bindings, OperationBinding),
            (self.evidences, MethodEvidence),
            (self.unresolved, MethodUnresolved),
        )
        if any(any(type(item) is not expected for item in items) for items, expected in expected_types):
            raise ValueError("method facts collections contain an invalid type")
        nested = (
            *self.operations,
            *self.implementations,
            *self.consumer_calls,
            *self.bindings,
            *self.evidences,
            *self.unresolved,
        )
        if any(
            item.repo_id != self.repo_id
            or item.source_revision != self.source_revision
            or item.generation_id != self.generation_id
            for item in nested
        ):
            raise ValueError("nested method fact identity mismatch")
        for name, items in (
            ("operations", self.operations),
            ("implementations", self.implementations),
            ("consumer_calls", self.consumer_calls),
            ("bindings", self.bindings),
            ("evidences", self.evidences),
            ("unresolved", self.unresolved),
        ):
            object.__setattr__(self, name, tuple(sorted(items, key=lambda item: item.id)))
        evidence_ids = {evidence.id for evidence in self.evidences}
        if len(evidence_ids) != len(self.evidences):
            raise ValueError("duplicate method evidence id")
        evidence_backed = (
            *self.operations,
            *self.implementations,
            *self.consumer_calls,
            *self.bindings,
            *self.unresolved,
        )
        if any(evidence_id not in evidence_ids for item in evidence_backed for evidence_id in item.evidence_ids):
            raise ValueError("missing method evidence")
        implementation_ids = {item.id for item in self.implementations}
        operation_ids = {item.id for item in self.operations}
        if any(call.caller_implementation_id not in implementation_ids for call in self.consumer_calls):
            raise ValueError("consumer call references unknown implementation")
        if any(binding.operation_id not in operation_ids for binding in self.bindings):
            raise ValueError("binding references unknown operation")
        if any(
            binding.implementation_id is not None and binding.implementation_id not in implementation_ids
            for binding in self.bindings
        ):
            raise ValueError("binding references unknown implementation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "repo_id": self.repo_id,
            "source_revision": self.source_revision,
            "generation_id": self.generation_id,
            "operations": [item.to_dict() for item in sorted(self.operations, key=lambda item: item.id)],
            "implementations": [item.to_dict() for item in sorted(self.implementations, key=lambda item: item.id)],
            "consumer_calls": [item.to_dict() for item in sorted(self.consumer_calls, key=lambda item: item.id)],
            "bindings": [item.to_dict() for item in sorted(self.bindings, key=lambda item: item.id)],
            "evidences": [item.to_dict() for item in sorted(self.evidences, key=lambda item: item.id)],
            "unresolved": [item.to_dict() for item in sorted(self.unresolved, key=lambda item: item.id)],
        }
