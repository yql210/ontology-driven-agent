"""Deterministic contract-method resolution for retained source calls.

This module intentionally stops at the authorized contract declaration. Provider
binding and graph persistence belong to later stages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ontoagent.parsing.service_graph.java_contract_index import (
    JavaContractMethod,
    JavaContractVisibilityResult,
    JavaContractVisibilityStatus,
)
from ontoagent.parsing.service_graph.methods import RetainedSourceCall


class ContractMethodResolutionOutcome(StrEnum):
    """The complete set of outcomes emitted by contract-method resolution."""

    DETERMINED = "determined"
    CONTRACT_MISSING = "CONTRACT_MISSING"
    CONTRACT_CONFLICT = "CONTRACT_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    ARGUMENT_TYPE_UNKNOWN = "ARGUMENT_TYPE_UNKNOWN"
    METHOD_NOT_FOUND = "METHOD_NOT_FOUND"
    OVERLOAD_AMBIGUOUS = "OVERLOAD_AMBIGUOUS"


@dataclass(frozen=True)
class CallerIdentity:
    """The caller identity that must exactly agree with a retained source call."""

    repo_id: str
    module_id: str
    source_revision: str
    generation_id: str
    implementation_id: str

    def __post_init__(self) -> None:
        for name in ("repo_id", "module_id", "source_revision", "generation_id", "implementation_id"):
            _require_nonblank(getattr(self, name), name)

    @classmethod
    def from_retained_call(cls, call: RetainedSourceCall) -> CallerIdentity:
        """Create the caller identity pinned by ``call``."""
        if type(call) is not RetainedSourceCall:
            raise ValueError("call must be a RetainedSourceCall")
        return cls(
            call.repo_id, call.module_id, call.source_revision, call.generation_id, call.caller_implementation_id
        )


@dataclass(frozen=True)
class ProtocolMetadata:
    """Protocol data supplied by an adapter, without binding a provider."""

    protocol: str
    settings: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require_nonblank(self.protocol, "protocol")
        if type(self.settings) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or any(not isinstance(value, str) or not value.strip() for value in item)
            for item in self.settings
        ):
            raise ValueError("settings must be a tuple of nonblank string pairs")


ProtocolCompatibilityPredicate = Callable[[RetainedSourceCall, JavaContractVisibilityResult, ProtocolMetadata], bool]


@dataclass(frozen=True)
class ContractMethodResolution:
    """One source-pinned contract-stage decision, never a provider binding."""

    retained_call_id: str
    caller: CallerIdentity
    stage: str
    outcome: ContractMethodResolutionOutcome
    contract_method: JavaContractMethod | None
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonblank(self.retained_call_id, "retained_call_id")
        if type(self.caller) is not CallerIdentity:
            raise ValueError("caller must be a CallerIdentity")
        if self.stage != "CONTRACT_RESOLUTION":
            raise ValueError("stage must be CONTRACT_RESOLUTION")
        if type(self.outcome) is not ContractMethodResolutionOutcome:
            raise ValueError("outcome must be a ContractMethodResolutionOutcome")
        if self.outcome is ContractMethodResolutionOutcome.DETERMINED:
            if type(self.contract_method) is not JavaContractMethod:
                raise ValueError("determined resolution requires a JavaContractMethod")
        elif self.contract_method is not None:
            raise ValueError("unresolved resolution cannot contain a contract method")
        if (
            type(self.evidence_ids) is not tuple
            or not self.evidence_ids
            or any(not isinstance(item, str) or not item.strip() for item in self.evidence_ids)
        ):
            raise ValueError("evidence_ids must be a non-empty tuple of nonblank strings")


class ContractMethodResolver:
    """Resolve a retained call against an already-authorized contract view."""

    def resolve(
        self,
        call: RetainedSourceCall,
        caller: CallerIdentity,
        visibility: JavaContractVisibilityResult,
        protocol_metadata: ProtocolMetadata,
        is_compatible: ProtocolCompatibilityPredicate,
    ) -> ContractMethodResolution:
        """Return the exact authorized contract method or a fail-closed outcome."""
        if type(call) is not RetainedSourceCall:
            raise ValueError("call must be a RetainedSourceCall")
        if type(caller) is not CallerIdentity:
            raise ValueError("caller must be a CallerIdentity")
        if type(visibility) is not JavaContractVisibilityResult:
            raise ValueError("visibility must be a JavaContractVisibilityResult")
        if type(protocol_metadata) is not ProtocolMetadata:
            raise ValueError("protocol_metadata must be a ProtocolMetadata")
        if not callable(is_compatible):
            raise ValueError("is_compatible must be callable")
        if caller != CallerIdentity.from_retained_call(call):
            raise ValueError("caller must match the retained source call identity")

        visibility_outcome = _visibility_outcome(visibility.status)
        if visibility_outcome is not None:
            return self._unresolved(call, caller, visibility_outcome)
        if call.receiver_type is None or call.resolution_reason == "DYNAMIC_TARGET":
            return self._unresolved(call, caller, ContractMethodResolutionOutcome.CONTRACT_MISSING)
        compatible = is_compatible(call, visibility, protocol_metadata)
        if type(compatible) is not bool:
            raise ValueError("is_compatible must return bool")
        if not compatible:
            return self._unresolved(call, caller, ContractMethodResolutionOutcome.CONTRACT_MISSING)
        if any(argument_type is None for argument_type in call.argument_types):
            return self._unresolved(call, caller, ContractMethodResolutionOutcome.ARGUMENT_TYPE_UNKNOWN)

        methods = tuple(
            method
            for contract in visibility.contracts
            if contract.fqcn == call.receiver_type
            for method in contract.methods
            if method.name == call.method_name and method.parameter_types == call.argument_types
        )
        if not methods:
            return self._unresolved(call, caller, ContractMethodResolutionOutcome.METHOD_NOT_FOUND)
        candidates = tuple(sorted(methods, key=lambda method: method.canonical_signature))
        if len(candidates) != 1:
            return self._unresolved(call, caller, ContractMethodResolutionOutcome.OVERLOAD_AMBIGUOUS)
        return ContractMethodResolution(
            call.id,
            caller,
            "CONTRACT_RESOLUTION",
            ContractMethodResolutionOutcome.DETERMINED,
            candidates[0],
            call.evidence_ids,
        )

    @staticmethod
    def _unresolved(
        call: RetainedSourceCall, caller: CallerIdentity, outcome: ContractMethodResolutionOutcome
    ) -> ContractMethodResolution:
        return ContractMethodResolution(call.id, caller, "CONTRACT_RESOLUTION", outcome, None, call.evidence_ids)


def _visibility_outcome(status: JavaContractVisibilityStatus) -> ContractMethodResolutionOutcome | None:
    return {
        JavaContractVisibilityStatus.NO_EVIDENCE: ContractMethodResolutionOutcome.CONTRACT_MISSING,
        JavaContractVisibilityStatus.VERSION_CONFLICT: ContractMethodResolutionOutcome.VERSION_CONFLICT,
        JavaContractVisibilityStatus.CONTRACT_CONFLICT: ContractMethodResolutionOutcome.CONTRACT_CONFLICT,
        JavaContractVisibilityStatus.VISIBLE: None,
    }[status]


def _require_nonblank(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
