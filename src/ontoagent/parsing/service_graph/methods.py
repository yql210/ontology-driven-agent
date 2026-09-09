from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from typing import Any

METHOD_UNRESOLVED_REASONS = frozenset(
    {
        "AMBIGUOUS_TARGET",
        "CONTRACT_CONFLICT",
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
    binding_identity: str | None = None
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identity(self.repo_id, self.module_id, self.service_id, self.source_revision, self.generation_id)
        if self.role not in {"provider", "consumer"}:
            raise ValueError("role must be provider or consumer")
        for name in ("declaring_interface_fqcn", "operation_name", "canonical_signature"):
            _require_nonblank(getattr(self, name), name)
        _require_evidence_ids(self.evidence_ids)
        for name in ("group", "version", "alias", "binding_identity"):
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
            "binding_identity": self.binding_identity,
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
class RetainedSourceCall:
    """An immutable source-pinned method call retained before target resolution."""

    repo_id: str
    module_id: str
    service_id: str
    source_revision: str
    generation_id: str
    file_path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    caller_implementation_id: str
    receiver_declaration: str | None
    receiver_type: str | None
    receiver_missing_reason: str | None
    receiver_evidence_ids: tuple[str, ...]
    method_name: str
    argument_summaries: tuple[str, ...]
    argument_types: tuple[str | None, ...]
    argument_evidence_ids: tuple[tuple[str, ...], ...]
    protocol_settings: tuple[tuple[str, str], ...]
    resolution_stage: str
    resolution_status: str
    resolution_reason: str | None
    evidence_ids: tuple[str, ...]
    id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identity(self.repo_id, self.module_id, self.service_id, self.source_revision, self.generation_id)
        for name in ("file_path", "caller_implementation_id", "method_name", "resolution_stage", "resolution_status"):
            _require_nonblank(getattr(self, name), name)
        _optional_nonblank(self.receiver_declaration, "receiver_declaration")
        _optional_nonblank(self.receiver_type, "receiver_type")
        _optional_nonblank(self.receiver_missing_reason, "receiver_missing_reason")
        _optional_nonblank(self.resolution_reason, "resolution_reason")
        if (
            not isinstance(self.start_line, int)
            or isinstance(self.start_line, bool)
            or self.start_line < 1
            or not isinstance(self.end_line, int)
            or isinstance(self.end_line, bool)
            or self.end_line < self.start_line
            or not isinstance(self.start_column, int)
            or isinstance(self.start_column, bool)
            or self.start_column < 1
            or not isinstance(self.end_column, int)
            or isinstance(self.end_column, bool)
            or self.end_column < 1
            or (self.end_line == self.start_line and self.end_column < self.start_column)
        ):
            raise ValueError("invalid source range")
        if self.receiver_type is None and self.receiver_missing_reason is None:
            raise ValueError("receiver_missing_reason is required when receiver_type is unknown")
        if self.receiver_type is not None and self.receiver_missing_reason is not None:
            raise ValueError("receiver_missing_reason must be absent when receiver_type is known")
        _require_evidence_ids(self.receiver_evidence_ids)
        if type(self.argument_summaries) is not tuple or any(
            not isinstance(summary, str) or not summary.strip() for summary in self.argument_summaries
        ):
            raise ValueError("argument_summaries must be a tuple of nonblank strings")
        if type(self.argument_types) is not tuple or len(self.argument_types) != len(self.argument_summaries):
            raise ValueError("argument_types must match argument_summaries")
        if any(
            argument_type is not None and (not isinstance(argument_type, str) or not argument_type.strip())
            for argument_type in self.argument_types
        ):
            raise ValueError("argument_types must contain nonblank strings or None")
        if type(self.argument_evidence_ids) is not tuple or len(self.argument_evidence_ids) != len(
            self.argument_summaries
        ):
            raise ValueError("argument_evidence_ids must match argument_summaries")
        for argument_evidence in self.argument_evidence_ids:
            _require_evidence_ids(argument_evidence)
        if type(self.protocol_settings) is not tuple or any(
            type(setting) is not tuple
            or len(setting) != 2
            or any(not isinstance(value, str) or not value.strip() for value in setting)
            for setting in self.protocol_settings
        ):
            raise ValueError("protocol_settings must be a tuple of nonblank string pairs")
        _require_evidence_ids(self.evidence_ids)
        attributed_evidence_ids = {
            *self.receiver_evidence_ids,
            *(evidence_id for item in self.argument_evidence_ids for evidence_id in item),
        }
        if not attributed_evidence_ids.issubset(self.evidence_ids):
            raise ValueError("receiver and argument evidence_ids must be retained call evidence_ids")
        object.__setattr__(self, "id", _stable_id({"kind": "retained_source_call", **self._identity()}))

    def _identity(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "module_id": self.module_id,
            "service_id": self.service_id,
            "source_revision": self.source_revision,
            "generation_id": self.generation_id,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "caller_implementation_id": self.caller_implementation_id,
            "receiver_declaration": self.receiver_declaration,
            "receiver_type": self.receiver_type,
            "receiver_missing_reason": self.receiver_missing_reason,
            "method_name": self.method_name,
            "argument_summaries": list(self.argument_summaries),
            "argument_types": list(self.argument_types),
            "protocol_settings": [list(setting) for setting in self.protocol_settings],
            "resolution_stage": self.resolution_stage,
            "resolution_status": self.resolution_status,
            "resolution_reason": self.resolution_reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._identity(),
            "receiver_evidence_ids": list(self.receiver_evidence_ids),
            "argument_evidence_ids": [list(item) for item in self.argument_evidence_ids],
            "evidence_ids": list(self.evidence_ids),
            "id": self.id,
        }


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
    retained_source_calls: tuple[RetainedSourceCall, ...] = ()

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
            self.retained_source_calls,
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
            (self.retained_source_calls, RetainedSourceCall),
        )
        if any(any(type(item) is not expected for item in items) for items, expected in expected_types):
            raise ValueError("method facts collections contain an invalid type")
        local_records = (
            *self.operations,
            *self.implementations,
            *self.consumer_calls,
            *self.bindings,
            *self.unresolved,
            *self.retained_source_calls,
        )
        if any(
            item.repo_id != self.repo_id
            or item.source_revision != self.source_revision
            or item.generation_id != self.generation_id
            for item in local_records
        ):
            raise ValueError("nested method fact identity mismatch")
        if any(evidence.generation_id != self.generation_id for evidence in self.evidences):
            raise ValueError("method evidence generation mismatch")
        for name, items in (
            ("operations", self.operations),
            ("implementations", self.implementations),
            ("consumer_calls", self.consumer_calls),
            ("bindings", self.bindings),
            ("evidences", self.evidences),
            ("unresolved", self.unresolved),
            ("retained_source_calls", self.retained_source_calls),
        ):
            object.__setattr__(self, name, self._canonicalize_records(name, items))
        evidence_ids = {evidence.id for evidence in self.evidences}
        if len(evidence_ids) != len(self.evidences):
            raise ValueError("duplicate method evidence id")
        evidence_backed = (
            *self.operations,
            *self.implementations,
            *self.consumer_calls,
            *self.bindings,
            *self.unresolved,
            *self.retained_source_calls,
        )
        if any(evidence_id not in evidence_ids for item in evidence_backed for evidence_id in item.evidence_ids):
            raise ValueError("missing method evidence")
        implementation_ids = {item.id for item in self.implementations}
        operation_ids = {item.id for item in self.operations}
        if any(call.caller_implementation_id not in implementation_ids for call in self.consumer_calls):
            raise ValueError("consumer call references unknown implementation")
        if any(call.caller_implementation_id not in implementation_ids for call in self.retained_source_calls):
            raise ValueError("retained source call references unknown implementation")
        if any(binding.operation_id not in operation_ids for binding in self.bindings):
            raise ValueError("binding references unknown operation")
        if any(
            binding.implementation_id is not None and binding.implementation_id not in implementation_ids
            for binding in self.bindings
        ):
            raise ValueError("binding references unknown implementation")

    @staticmethod
    def _canonicalize_records(name: str, items: tuple[Any, ...]) -> tuple[Any, ...]:
        by_id: dict[str, Any] = {}
        for item in items:
            existing = by_id.get(item.id)
            if existing is None:
                by_id[item.id] = item
                continue
            if not hasattr(item, "evidence_ids") or not hasattr(existing, "evidence_ids"):
                if existing != item:
                    raise ValueError(f"conflicting duplicate method {name} id")
                continue
            if any(
                getattr(existing, field_name) != getattr(item, field_name)
                for field_name in existing.__dataclass_fields__
                if field_name != "evidence_ids"
            ):
                raise ValueError(f"conflicting duplicate method {name} id")
            by_id[item.id] = replace(existing, evidence_ids=tuple(sorted({*existing.evidence_ids, *item.evidence_ids})))
        return tuple(sorted(by_id.values(), key=lambda item: item.id))

    def to_dict(self) -> dict[str, Any]:
        result = {
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
        if self.retained_source_calls:
            result["retained_source_calls"] = [
                item.to_dict() for item in sorted(self.retained_source_calls, key=lambda item: item.id)
            ]
        return result
