"""Pure provider binding for already-determined contract methods."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ontoagent.parsing.service_graph.contract_method_resolver import (
    ContractMethodResolution,
    ContractMethodResolutionOutcome,
)
from ontoagent.parsing.service_graph.methods import ImplementationMethod, OperationBinding, ServiceOperation


class ProviderMethodBindingOutcome(StrEnum):
    """Fail-closed outcomes for the provider binding stage."""

    DETERMINED = "determined"
    PROVIDER_MISSING = "PROVIDER_MISSING"
    PROVIDER_AMBIGUOUS = "PROVIDER_AMBIGUOUS"
    PROVIDER_IDENTITY_MISMATCH = "PROVIDER_IDENTITY_MISMATCH"
    IMPLEMENTATION_MISSING = "IMPLEMENTATION_MISSING"


@dataclass(frozen=True)
class AuthorizedProviderSource:
    """One provider source snapshot that the caller explicitly authorizes."""

    repo_id: str
    module_id: str
    source_revision: str

    def __post_init__(self) -> None:
        for name in ("repo_id", "module_id", "source_revision"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be nonblank")

    @property
    def identity(self) -> tuple[str, str, str]:
        """Return the source-pinned identity used to authorize facts."""
        return self.repo_id, self.module_id, self.source_revision


ProviderBindingIdentityPredicate = Callable[[ContractMethodResolution, ServiceOperation, OperationBinding], bool]


@dataclass(frozen=True)
class ProviderMethodBindingResult:
    """A provider-stage outcome retaining its source-pinned contract decision."""

    contract_resolution: ContractMethodResolution
    outcome: ProviderMethodBindingOutcome
    provider_operation: ServiceOperation | None
    binding: OperationBinding | None
    implementation: ImplementationMethod | None

    def __post_init__(self) -> None:
        if type(self.contract_resolution) is not ContractMethodResolution:
            raise ValueError("contract_resolution must be a ContractMethodResolution")
        if type(self.outcome) is not ProviderMethodBindingOutcome:
            raise ValueError("outcome must be a ProviderMethodBindingOutcome")
        values = (self.provider_operation, self.binding, self.implementation)
        if self.outcome is ProviderMethodBindingOutcome.DETERMINED:
            if any(value is None for value in values):
                raise ValueError("determined result requires provider, binding, and implementation")
        elif any(value is not None for value in values):
            raise ValueError("unresolved result cannot contain provider facts")

    @property
    def caller(self) -> ContractMethodResolution:
        """Return the full source-pinned caller and selected-contract decision."""
        return self.contract_resolution

    @property
    def retained_call_id(self) -> str:
        """Return the retained source call ID without collapsing caller identity."""
        return self.contract_resolution.retained_call_id


class ProviderMethodBinder:
    """Bind a determined contract method to exactly one authorized provider implementation."""

    def bind(
        self,
        resolution: ContractMethodResolution,
        operations: tuple[ServiceOperation, ...],
        bindings: tuple[OperationBinding, ...],
        implementations: tuple[ImplementationMethod, ...],
        current_generation_id: str,
        authorized_provider_sources: frozenset[AuthorizedProviderSource],
        matches_provider_identity: ProviderBindingIdentityPredicate,
    ) -> ProviderMethodBindingResult:
        """Return a determined binding only when exactly one eligible binding has implementation evidence."""
        self._validate_inputs(
            resolution,
            operations,
            bindings,
            implementations,
            current_generation_id,
            authorized_provider_sources,
            matches_provider_identity,
        )
        if resolution.outcome is not ContractMethodResolutionOutcome.DETERMINED:
            raise ValueError("resolution must be determined")
        assert resolution.contract_method is not None

        authorized = {source.identity for source in authorized_provider_sources}
        matching_operations = tuple(
            operation
            for operation in operations
            if operation.role == "provider"
            and operation.canonical_signature == resolution.contract_method.canonical_signature
            and operation.declaring_interface_fqcn == resolution.contract_method.declaring_interface_fqcn
            and self._eligible(operation, current_generation_id, authorized)
        )
        if not matching_operations:
            return self._unresolved(resolution, ProviderMethodBindingOutcome.PROVIDER_MISSING)

        matching_bindings_by_id = {
            binding.id: (operation, binding)
            for operation in matching_operations
            for binding in bindings
            if binding.operation_id == operation.id
            and self._eligible(binding, current_generation_id, authorized)
            and self._same_source(operation, binding)
            and self._matches(matches_provider_identity, resolution, operation, binding)
        }
        matching_bindings = tuple(matching_bindings_by_id.values())
        if not matching_bindings:
            return self._unresolved(resolution, ProviderMethodBindingOutcome.PROVIDER_IDENTITY_MISMATCH)

        eligible_by_binding_id = {
            binding.id: (operation, binding, implementation)
            for operation, binding in matching_bindings
            for implementation in implementations
            if binding.implementation_id == implementation.id
            and self._eligible(implementation, current_generation_id, authorized)
            and self._same_source(binding, implementation)
        }
        eligible = tuple(eligible_by_binding_id.values())
        if not eligible:
            return self._unresolved(resolution, ProviderMethodBindingOutcome.IMPLEMENTATION_MISSING)
        if len(eligible) != 1:
            return self._unresolved(resolution, ProviderMethodBindingOutcome.PROVIDER_AMBIGUOUS)
        operation, binding, implementation = eligible[0]
        return ProviderMethodBindingResult(
            resolution, ProviderMethodBindingOutcome.DETERMINED, operation, binding, implementation
        )

    @staticmethod
    def _validate_inputs(
        resolution: ContractMethodResolution,
        operations: tuple[ServiceOperation, ...],
        bindings: tuple[OperationBinding, ...],
        implementations: tuple[ImplementationMethod, ...],
        current_generation_id: str,
        authorized_provider_sources: frozenset[AuthorizedProviderSource],
        matches_provider_identity: ProviderBindingIdentityPredicate,
    ) -> None:
        if type(resolution) is not ContractMethodResolution:
            raise ValueError("resolution must be a ContractMethodResolution")
        for name, values, expected in (
            ("operations", operations, ServiceOperation),
            ("bindings", bindings, OperationBinding),
            ("implementations", implementations, ImplementationMethod),
        ):
            if type(values) is not tuple or any(type(value) is not expected for value in values):
                raise ValueError(f"{name} must be a tuple of {expected.__name__} values")
        if not isinstance(current_generation_id, str) or not current_generation_id.strip():
            raise ValueError("current_generation_id must be nonblank")
        if type(authorized_provider_sources) is not frozenset or any(
            type(source) is not AuthorizedProviderSource for source in authorized_provider_sources
        ):
            raise ValueError("authorized_provider_sources must be a frozenset of AuthorizedProviderSource values")
        if not callable(matches_provider_identity):
            raise ValueError("matches_provider_identity must be callable")

    @staticmethod
    def _eligible(
        fact: ServiceOperation | OperationBinding | ImplementationMethod,
        current_generation_id: str,
        authorized_sources: set[tuple[str, str, str]],
    ) -> bool:
        return (
            fact.generation_id == current_generation_id
            and (
                fact.repo_id,
                fact.module_id,
                fact.source_revision,
            )
            in authorized_sources
        )

    @staticmethod
    def _same_source(
        left: ServiceOperation | OperationBinding,
        right: OperationBinding | ImplementationMethod,
    ) -> bool:
        return (left.repo_id, left.module_id, left.source_revision) == (
            right.repo_id,
            right.module_id,
            right.source_revision,
        )

    @staticmethod
    def _matches(
        predicate: ProviderBindingIdentityPredicate,
        resolution: ContractMethodResolution,
        operation: ServiceOperation,
        binding: OperationBinding,
    ) -> bool:
        matches = predicate(resolution, operation, binding)
        if type(matches) is not bool:
            raise ValueError("matches_provider_identity must return bool")
        return matches

    @staticmethod
    def _unresolved(
        resolution: ContractMethodResolution, outcome: ProviderMethodBindingOutcome
    ) -> ProviderMethodBindingResult:
        return ProviderMethodBindingResult(resolution, outcome, None, None, None)
