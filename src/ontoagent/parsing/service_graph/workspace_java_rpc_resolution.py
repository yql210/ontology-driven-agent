"""Pure workspace assembly for source-pinned Java/Dubbo method resolution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .contract_method_resolver import (
    CallerIdentity,
    ContractMethodResolution,
    ContractMethodResolver,
    ProtocolMetadata,
)
from .detector_sdk import (
    AuthorizedContractDeclaration,
    AuthorizedContractMethod,
    AuthorizedContractSource,
    AuthorizedContractView,
)
from .java_contract_index import (
    ContractSourceMapping,
    JavaContractIndex,
    JavaContractIndexResult,
    JavaContractSource,
    JavaContractVisibilityResult,
    JavaContractVisibilityStatus,
    JavaSourceRange,
)
from .methods import ImplementationMethod, MethodFacts, OperationBinding, RetainedSourceCall, ServiceOperation
from .provider_method_binder import (
    AuthorizedProviderSource,
    ProviderBindingIdentityPredicate,
    ProviderMethodBinder,
    ProviderMethodBindingOutcome,
    ProviderMethodBindingResult,
)


def _deny_provider_identity(
    resolution: ContractMethodResolution, operation: ServiceOperation, binding: OperationBinding
) -> bool:
    return False


@dataclass(frozen=True)
class FrozenSourceIdentity:
    """One repository/module/revision snapshot admitted to a workspace generation."""

    repo_id: str
    module_id: str
    source_revision: str

    def __post_init__(self) -> None:
        for name in ("repo_id", "module_id", "source_revision"):
            _require_nonblank(getattr(self, name), name)

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.repo_id, self.module_id, self.source_revision


@dataclass(frozen=True)
class WorkspaceJavaRpcAuthorization:
    """Explicit Java source and provider authorization for one frozen workspace generation."""

    contract_sources: tuple[JavaContractSource, ...]
    contract_mappings: tuple[ContractSourceMapping, ...]
    authorized_provider_sources: frozenset[AuthorizedProviderSource]
    matches_provider_identity: ProviderBindingIdentityPredicate = _deny_provider_identity

    def __post_init__(self) -> None:
        if type(self.contract_sources) is not tuple or any(
            type(item) is not JavaContractSource for item in self.contract_sources
        ):
            raise ValueError("contract_sources must be a tuple of JavaContractSource values")
        if type(self.contract_mappings) is not tuple or any(
            type(item) is not ContractSourceMapping for item in self.contract_mappings
        ):
            raise ValueError("contract_mappings must be a tuple of ContractSourceMapping values")
        if type(self.authorized_provider_sources) is not frozenset or any(
            type(item) is not AuthorizedProviderSource for item in self.authorized_provider_sources
        ):
            raise ValueError("authorized_provider_sources must be a frozenset of AuthorizedProviderSource values")
        if not callable(self.matches_provider_identity):
            raise ValueError("matches_provider_identity must be callable")


def prepare_authorized_contract_views(
    authorization: WorkspaceJavaRpcAuthorization,
    repositories: Mapping[tuple[str, str, str], object],
    frozen_source_identities: frozenset[FrozenSourceIdentity],
) -> tuple[JavaContractIndexResult, Mapping[tuple[str, str, str], AuthorizedContractView]]:
    """Build one deny-by-default, source-pinned contract view for each frozen repository identity."""
    if type(authorization) is not WorkspaceJavaRpcAuthorization:
        raise ValueError("authorization must be a WorkspaceJavaRpcAuthorization")
    if type(frozen_source_identities) is not frozenset or any(
        type(item) is not FrozenSourceIdentity for item in frozen_source_identities
    ):
        raise ValueError("frozen_source_identities must be a frozenset of FrozenSourceIdentity values")
    frozen = {item.identity for item in frozen_source_identities}
    if not frozen:
        raise ValueError("frozen_source_identities must not be empty")
    if set(repositories) != frozen:
        raise ValueError("repositories must match current frozen source identities")
    source_identities = {source.identity for source in authorization.contract_sources}
    if len(source_identities) != len(authorization.contract_sources) or not source_identities.issubset(frozen):
        raise ValueError("contract sources must be current frozen source identities")
    for source in authorization.contract_sources:
        repository = repositories[source.identity]
        if getattr(repository, "root_path", None) != source.root_path:
            raise ValueError("contract source root must match the frozen repository snapshot")
    for mapping in authorization.contract_mappings:
        consumer = (mapping.consumer_repo_id, mapping.consumer_module_id, mapping.consumer_source_revision)
        if consumer not in frozen or mapping.contract_source_identity not in source_identities:
            raise ValueError("contract mappings must use current frozen authorized contract sources")
    if any(source.identity not in frozen for source in authorization.authorized_provider_sources):
        raise ValueError("authorized providers must be current frozen source identities")
    if any(source.identity in source_identities for source in authorization.authorized_provider_sources):
        raise ValueError("contract API or client sources cannot be authorized providers")

    index = JavaContractIndex().build(tuple(sorted(authorization.contract_sources, key=lambda item: item.identity)))
    views: dict[tuple[str, str, str], AuthorizedContractView] = {}
    for identity in sorted(frozen):
        allowed = {
            mapping.contract_source_identity
            for mapping in authorization.contract_mappings
            if (mapping.consumer_repo_id, mapping.consumer_module_id, mapping.consumer_source_revision) == identity
        }
        declarations: dict[str, AuthorizedContractDeclaration] = {}
        for fqcn, contract in sorted(index.contracts.items()):
            sources = tuple(source for source in contract.sources if _source_identity(source) in allowed)
            if not sources:
                continue
            methods = tuple(
                AuthorizedContractMethod(
                    method.name, method.parameter_types, method.return_type, _contract_source(method.source)
                )
                for method in contract.methods
                if _source_identity(method.source) in allowed
            )
            declarations[fqcn] = AuthorizedContractDeclaration(
                fqcn,
                methods,
                tuple(_contract_source(source) for source in sources),
            )
        views[identity] = AuthorizedContractView(declarations)
    return index, views


def _source_identity(source: JavaSourceRange) -> tuple[str, str, str]:
    return source.repo_id, source.module_id, source.source_revision


def _contract_source(source: JavaSourceRange) -> AuthorizedContractSource:
    return AuthorizedContractSource(
        source.repo_id,
        source.module_id,
        source.source_revision,
        source.file_path,
        source.start_line,
        source.end_line,
    )


@dataclass(frozen=True)
class WorkspaceJavaRpcResolutionInput:
    """All explicit, frozen inputs required for a pure Java/Dubbo resolution pass."""

    generation_id: str
    method_facts: tuple[MethodFacts, ...]
    contract_index: JavaContractIndexResult
    contract_mappings: tuple[ContractSourceMapping, ...]
    frozen_source_identities: frozenset[FrozenSourceIdentity]
    authorized_provider_sources: frozenset[AuthorizedProviderSource]
    matches_provider_identity: ProviderBindingIdentityPredicate

    def __post_init__(self) -> None:
        _require_nonblank(self.generation_id, "generation_id")
        if type(self.method_facts) is not tuple or any(type(item) is not MethodFacts for item in self.method_facts):
            raise ValueError("method_facts must be a tuple of MethodFacts")
        if type(self.contract_index) is not JavaContractIndexResult:
            raise ValueError("contract_index must be a JavaContractIndexResult")
        if type(self.contract_mappings) is not tuple or any(
            type(item) is not ContractSourceMapping for item in self.contract_mappings
        ):
            raise ValueError("contract_mappings must be a tuple of ContractSourceMapping values")
        if type(self.frozen_source_identities) is not frozenset or any(
            type(item) is not FrozenSourceIdentity for item in self.frozen_source_identities
        ):
            raise ValueError("frozen_source_identities must be a frozenset of FrozenSourceIdentity values")
        if type(self.authorized_provider_sources) is not frozenset or any(
            type(item) is not AuthorizedProviderSource for item in self.authorized_provider_sources
        ):
            raise ValueError("authorized_provider_sources must be a frozenset of AuthorizedProviderSource values")
        if not callable(self.matches_provider_identity):
            raise ValueError("matches_provider_identity must be callable")


@dataclass(frozen=True)
class WorkspaceJavaRpcCallResolution:
    """One retained caller's contract-stage and optional provider-stage outcomes."""

    retained_call: RetainedSourceCall
    contract: ContractMethodResolution
    binding: ProviderMethodBindingResult | None

    def __post_init__(self) -> None:
        if type(self.retained_call) is not RetainedSourceCall:
            raise ValueError("retained_call must be a RetainedSourceCall")
        if type(self.contract) is not ContractMethodResolution:
            raise ValueError("contract must be a ContractMethodResolution")
        if self.contract.retained_call_id != self.retained_call.id:
            raise ValueError("contract must retain the retained call ID")
        if self.binding is not None and type(self.binding) is not ProviderMethodBindingResult:
            raise ValueError("binding must be a ProviderMethodBindingResult or None")
        if self.binding is not None and self.binding.retained_call_id != self.retained_call.id:
            raise ValueError("binding must retain the retained call ID")

    @property
    def retained_call_id(self) -> str:
        return self.retained_call.id


@dataclass(frozen=True)
class WorkspaceJavaRpcResolutionResult:
    """Deterministic immutable output for every admitted Dubbo retained caller."""

    calls: tuple[WorkspaceJavaRpcCallResolution, ...]

    def __post_init__(self) -> None:
        if type(self.calls) is not tuple or any(
            type(item) is not WorkspaceJavaRpcCallResolution for item in self.calls
        ):
            raise ValueError("calls must be a tuple of WorkspaceJavaRpcCallResolution values")
        if len({item.retained_call_id for item in self.calls}) != len(self.calls):
            raise ValueError("calls must have unique retained call IDs")

    @property
    def determined(self) -> tuple[WorkspaceJavaRpcCallResolution, ...]:
        """Return the exact consumer-to-provider operation/implementation tuples."""
        return tuple(
            item
            for item in self.calls
            if item.binding is not None and item.binding.outcome is ProviderMethodBindingOutcome.DETERMINED
        )

    @property
    def unresolved(self) -> tuple[WorkspaceJavaRpcCallResolution, ...]:
        """Return calls with a contract or provider-stage unresolved outcome."""
        return tuple(item for item in self.calls if item not in self.determined)


class WorkspaceJavaRpcResolutionAssembler:
    """Resolve retained Dubbo callers with only explicit frozen workspace inputs."""

    def resolve(self, value: WorkspaceJavaRpcResolutionInput) -> WorkspaceJavaRpcResolutionResult:
        """Assemble deterministic contract and provider outcomes without persistence or inference."""
        if type(value) is not WorkspaceJavaRpcResolutionInput:
            raise ValueError("value must be a WorkspaceJavaRpcResolutionInput")
        frozen = {item.identity for item in value.frozen_source_identities}
        self._validate_frozen_inputs(value, frozen)
        calls = tuple(
            sorted(
                (
                    call
                    for fact in value.method_facts
                    if fact.detector_id == "dubbo-method"
                    for call in fact.retained_source_calls
                ),
                key=_retained_call_key,
            )
        )
        if len({call.id for call in calls}) != len(calls):
            raise ValueError("retained source calls must have unique IDs")
        operations = _sorted_facts(fact.operations for fact in value.method_facts)
        bindings = _sorted_facts(fact.bindings for fact in value.method_facts)
        implementations = _sorted_facts(fact.implementations for fact in value.method_facts)
        index = JavaContractIndex()
        resolver = ContractMethodResolver()
        binder = ProviderMethodBinder()
        results: list[WorkspaceJavaRpcCallResolution] = []
        for call in calls:
            visibility = self._visibility(index, value, call)
            contract = resolver.resolve(
                call,
                CallerIdentity.from_retained_call(call),
                visibility,
                ProtocolMetadata("dubbo", tuple(sorted(call.protocol_settings))),
                _compatible_with_visible_mapping,
            )
            binding = (
                binder.bind(
                    contract,
                    operations,
                    bindings,
                    implementations,
                    value.generation_id,
                    value.authorized_provider_sources,
                    value.matches_provider_identity,
                )
                if contract.contract_method is not None
                else None
            )
            results.append(WorkspaceJavaRpcCallResolution(call, contract, binding))
        return WorkspaceJavaRpcResolutionResult(tuple(results))

    @staticmethod
    def _visibility(
        index: JavaContractIndex, value: WorkspaceJavaRpcResolutionInput, call: RetainedSourceCall
    ) -> JavaContractVisibilityResult:
        settings = dict(call.protocol_settings)
        version = settings.get("version")
        if version is None:
            return JavaContractVisibilityResult(JavaContractVisibilityStatus.NO_EVIDENCE, (), ())
        return index.visible_contracts(
            value.contract_index,
            call.repo_id,
            call.module_id,
            call.source_revision,
            version,
            tuple(sorted(value.contract_mappings, key=_mapping_key)),
            call.receiver_type,
        )

    @staticmethod
    def _validate_frozen_inputs(value: WorkspaceJavaRpcResolutionInput, frozen: set[tuple[str, str, str]]) -> None:
        if not frozen:
            raise ValueError("frozen_source_identities must not be empty")
        for fact in value.method_facts:
            if fact.generation_id != value.generation_id:
                raise ValueError("method facts must belong to the current generation")
            if not any(identity[0] == fact.repo_id and identity[2] == fact.source_revision for identity in frozen):
                raise ValueError("method facts must belong to frozen source identities")
            for item in (
                *fact.operations,
                *fact.implementations,
                *fact.consumer_calls,
                *fact.bindings,
                *fact.evidences,
                *fact.unresolved,
                *fact.retained_source_calls,
            ):
                if (item.repo_id, item.module_id, item.source_revision) not in frozen:
                    raise ValueError("method facts must belong to frozen source identities")
        for identity, _ in value.contract_index.source_roles:
            if identity not in frozen:
                raise ValueError("contract index sources must belong to frozen source identities")
        for mapping in value.contract_mappings:
            consumer = (mapping.consumer_repo_id, mapping.consumer_module_id, mapping.consumer_source_revision)
            if consumer not in frozen or mapping.contract_source_identity not in frozen:
                raise ValueError("contract mappings must belong to frozen source identities")
        if any(source.identity not in frozen for source in value.authorized_provider_sources):
            raise ValueError("authorized provider sources must belong to frozen source identities")
        contract_source_identities = {identity for identity, _ in value.contract_index.source_roles}
        if any(source.identity in contract_source_identities for source in value.authorized_provider_sources):
            raise ValueError("contract API or client sources cannot be authorized providers")


def _compatible_with_visible_mapping(
    call: RetainedSourceCall, visibility: JavaContractVisibilityResult, metadata: ProtocolMetadata
) -> bool:
    return bool(visibility.mappings) and tuple(sorted(call.protocol_settings)) == metadata.settings


def _sorted_facts(
    values: Iterable[tuple[ServiceOperation, ...]]
    | Iterable[tuple[OperationBinding, ...]]
    | Iterable[tuple[ImplementationMethod, ...]],
) -> tuple[ServiceOperation, ...] | tuple[OperationBinding, ...] | tuple[ImplementationMethod, ...]:
    return tuple(sorted((item for group in values for item in group), key=lambda item: item.id))


def _retained_call_key(call: RetainedSourceCall) -> tuple[str, str, str, str, int, int, str]:
    return (
        call.repo_id,
        call.module_id,
        call.source_revision,
        call.file_path,
        call.start_line,
        call.start_column,
        call.id,
    )


def _mapping_key(mapping: ContractSourceMapping) -> tuple[str, str, str, str, str, int, int]:
    return (
        mapping.consumer_repo_id,
        mapping.consumer_module_id,
        mapping.consumer_source_revision,
        mapping.contract_repo_id,
        mapping.contract_module_id,
        mapping.evidence_start_line,
        mapping.evidence_end_line,
    )


def _require_nonblank(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank")
