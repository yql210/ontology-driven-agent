"""Isolated Neo4j write/readback slice for immutable method facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .methods import (
    ConsumerMethodCall,
    ImplementationMethod,
    MethodEvidence,
    MethodFacts,
    MethodUnresolved,
    OperationBinding,
    ServiceOperation,
)
from .workspace.models import WorkspaceGeneration


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _require_nonblank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
    return value.strip()


@dataclass(frozen=True)
class MethodGraphScope:
    """The explicit immutable workspace boundary for a method graph write."""

    namespace: str
    generation: WorkspaceGeneration

    def __post_init__(self) -> None:
        _require_nonblank(self.namespace, "namespace")
        if type(self.generation) is not WorkspaceGeneration:
            raise ValueError("generation must be a WorkspaceGeneration")

    @property
    def workspace_id(self) -> str:
        return self.generation.workspace_id

    @property
    def generation_id(self) -> str:
        return self.generation.generation_id

    @property
    def revisions_by_repo(self) -> dict[str, str]:
        return {snapshot.repo_id: snapshot.source_revision for snapshot in self.generation.snapshots}


@dataclass(frozen=True)
class MethodGraphWritePlan:
    """A deterministic, scope-validated collection of method detector outputs."""

    scope: MethodGraphScope
    facts: tuple[MethodFacts, ...]

    def __post_init__(self) -> None:
        if type(self.scope) is not MethodGraphScope or type(self.facts) is not tuple:
            raise ValueError("method graph plan has invalid types")
        if any(type(fact) is not MethodFacts for fact in self.facts):
            raise ValueError("method graph plan facts must be MethodFacts")
        revisions = self.scope.revisions_by_repo
        if any(
            fact.generation_id != self.scope.generation_id or revisions.get(fact.repo_id) != fact.source_revision
            for fact in self.facts
        ):
            raise ValueError("method facts must match a workspace snapshot and generation")
        if len({self._fact_id(fact) for fact in self.facts}) != len(self.facts):
            raise ValueError("duplicate method facts")
        object.__setattr__(self, "facts", tuple(sorted(self.facts, key=self._fact_id)))
        self._validate_references()

    @staticmethod
    def _fact_id(fact: MethodFacts) -> str:
        return hashlib.sha256(_canonical_json(fact.to_dict()).encode()).hexdigest()

    @property
    def fingerprint(self) -> str:
        payload = {
            "namespace": self.scope.namespace,
            "workspace_id": self.scope.workspace_id,
            "generation_id": self.scope.generation_id,
            "snapshots": sorted(self.scope.revisions_by_repo.items()),
            "facts": [fact.to_dict() for fact in self.facts],
        }
        return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()

    @property
    def operation_ids(self) -> tuple[str, ...]:
        return tuple(item.id for fact in self.facts for item in fact.operations)

    @property
    def node_count(self) -> int:
        return sum(
            len(fact.operations)
            + len(fact.implementations)
            + len(fact.consumer_calls)
            + len(fact.bindings)
            + len(fact.evidences)
            + len(fact.unresolved)
            + sum(call.target_kind == "endpoint" for call in fact.consumer_calls)
            for fact in self.facts
        )

    @property
    def relation_count(self) -> int:
        return (
            sum(
                len(item.evidence_ids)
                for fact in self.facts
                for item in (
                    *fact.operations,
                    *fact.implementations,
                    *fact.consumer_calls,
                    *fact.bindings,
                    *fact.unresolved,
                )
            )
            + sum(1 + (binding.implementation_id is not None) for fact in self.facts for binding in fact.bindings)
            + sum(2 for fact in self.facts for _ in fact.consumer_calls)
        )

    def _validate_references(self) -> None:
        operations = {item.id for fact in self.facts for item in fact.operations}
        operation_references = {
            item.declaring_interface_fqcn: item.id for fact in self.facts for item in fact.operations
        }
        implementations = {item.id for fact in self.facts for item in fact.implementations}
        evidences = {item.id for fact in self.facts for item in fact.evidences}
        for fact in self.facts:
            for item in (
                *fact.operations,
                *fact.implementations,
                *fact.consumer_calls,
                *fact.bindings,
                *fact.unresolved,
            ):
                if any(evidence_id not in evidences for evidence_id in item.evidence_ids):
                    raise ValueError("method graph has orphan evidence")
            if any(call.caller_implementation_id not in implementations for call in fact.consumer_calls):
                raise ValueError("method graph has orphan caller")
            if any(
                call.target_kind == "operation"
                and call.target_reference not in operations
                and call.target_reference not in operation_references
                and not call.target_reference.startswith("spring-http:")
                for call in fact.consumer_calls
            ):
                raise ValueError("method graph has orphan call target")

    def operation_id_for(self, reference: str) -> str:
        operations = {item.id: item.id for fact in self.facts for item in fact.operations}
        if reference in operations:
            return reference
        matches = [
            item.id for fact in self.facts for item in fact.operations if item.declaring_interface_fqcn == reference
        ]
        if len(matches) != 1:
            raise ValueError("method graph has ambiguous call target")
        return matches[0]


class MethodGraphSink(Protocol):
    @property
    def scope(self) -> MethodGraphScope: ...

    def write(self, plan: MethodGraphWritePlan) -> None: ...

    def readback(self, scope: MethodGraphScope) -> MethodGraphWritePlan: ...


@dataclass(frozen=True)
class MethodWriteReceipt:
    confirmed: bool
    node_count: int
    relation_count: int
    fingerprint: str
    readback: MethodGraphWritePlan | None
    scope: MethodGraphScope


class InMemoryMethodGraphSink:
    """Small strict sink used to exercise plan and receipt contracts without Neo4j."""

    def __init__(self, scope: MethodGraphScope) -> None:
        self._scope = scope
        self._plan: MethodGraphWritePlan | None = None

    @property
    def scope(self) -> MethodGraphScope:
        return self._scope

    def write(self, plan: MethodGraphWritePlan) -> None:
        if plan.scope != self._scope:
            raise ValueError("method graph namespace or scope mismatch")
        self._plan = plan

    def readback(self, scope: MethodGraphScope) -> MethodGraphWritePlan:
        if scope != self._scope:
            raise ValueError("method graph namespace or scope mismatch")
        if self._plan is None:
            raise ValueError("method graph has not been written")
        return self._plan


class MethodGraphWriter:
    """Writes a method plan and accepts it only after exact scoped readback."""

    def __init__(self, sink: MethodGraphSink) -> None:
        self._sink = sink

    def write(self, plan: MethodGraphWritePlan) -> MethodWriteReceipt:
        if plan.scope != self._sink.scope:
            raise ValueError("method graph namespace or scope mismatch")
        self._sink.write(plan)
        try:
            readback = self._sink.readback(plan.scope)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            readback = None
        confirmed = readback == plan and readback is not None and readback.fingerprint == plan.fingerprint
        return MethodWriteReceipt(
            confirmed,
            readback.node_count if readback is not None else 0,
            readback.relation_count if readback is not None else 0,
            plan.fingerprint,
            readback,
            plan.scope,
        )


def fact_from_dict(data: Mapping[str, object]) -> MethodFacts:
    """Recreate immutable facts from the canonical payload stored by the Neo4j adapter."""

    def items(key: str, factory):
        value = data.get(key)
        if not isinstance(value, list):
            raise ValueError(f"method fact {key} must be a list")
        return tuple(factory(item) for item in value)

    def values(item: object, names: tuple[str, ...]) -> list[object]:
        if not isinstance(item, Mapping) or any(name not in item for name in names):
            raise ValueError("malformed method fact item")
        return [item[name] for name in names]

    def fact_item(item: object, cls):
        names = tuple(cls.__dataclass_fields__)[:-1]
        result = values(item, names)
        if "evidence_ids" in names:
            result[names.index("evidence_ids")] = tuple(result[names.index("evidence_ids")])
        return cls(*result)

    evidence = items("evidences", lambda item: fact_item(item, MethodEvidence))
    operations = items("operations", lambda item: fact_item(item, ServiceOperation))
    implementations = items("implementations", lambda item: fact_item(item, ImplementationMethod))
    calls = items("consumer_calls", lambda item: fact_item(item, ConsumerMethodCall))
    bindings = items("bindings", lambda item: fact_item(item, OperationBinding))
    unresolved = items("unresolved", lambda item: fact_item(item, MethodUnresolved))
    return MethodFacts(
        _require_nonblank(data.get("detector_id"), "detector_id"),
        _require_nonblank(data.get("detector_version"), "detector_version"),
        _require_nonblank(data.get("repo_id"), "repo_id"),
        _require_nonblank(data.get("source_revision"), "source_revision"),
        _require_nonblank(data.get("generation_id"), "generation_id"),
        operations,
        implementations,
        calls,
        bindings,
        evidence,
        unresolved,
    )
