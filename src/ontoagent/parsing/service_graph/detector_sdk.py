from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from .methods import MethodFacts, _require_nonblank
from .models import RepositorySnapshot


@dataclass(frozen=True)
class DetectorCapability:
    capability_id: str
    version: str
    description: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.capability_id, "capability_id")
        _require_nonblank(self.version, "version")
        if self.description is not None:
            _require_nonblank(self.description, "description")

    def to_dict(self) -> dict[str, str | None]:
        return {"capability_id": self.capability_id, "version": self.version, "description": self.description}


@dataclass(frozen=True)
class DetectorMetadata:
    detector_id: str
    detector_version: str
    supported_languages: frozenset[str]
    capabilities: tuple[DetectorCapability, ...]

    def __post_init__(self) -> None:
        _require_nonblank(self.detector_id, "detector_id")
        _require_nonblank(self.detector_version, "detector_version")
        if type(self.supported_languages) is not frozenset:
            raise ValueError("supported_languages must be a frozenset")
        if any(not isinstance(language, str) or not language.strip() for language in self.supported_languages):
            raise ValueError("supported_languages must contain nonblank strings")
        languages = frozenset(language.strip().lower() for language in self.supported_languages)
        if not languages:
            raise ValueError("supported_languages must be non-empty")
        if type(self.capabilities) is not tuple or any(
            type(item) is not DetectorCapability for item in self.capabilities
        ):
            raise ValueError("capabilities must be a tuple of DetectorCapability")
        if len({item.capability_id for item in self.capabilities}) != len(self.capabilities):
            raise ValueError("capability_id values must be unique")
        object.__setattr__(self, "supported_languages", languages)

    def to_dict(self) -> dict[str, object]:
        return {
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "supported_languages": sorted(self.supported_languages),
            "capabilities": [item.to_dict() for item in self.capabilities],
        }


@dataclass(frozen=True)
class AuthorizedContractSource:
    """Immutable provenance for an explicitly authorized contract declaration."""

    repo_id: str
    module_id: str
    source_revision: str
    file_path: str
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        for name in ("repo_id", "module_id", "source_revision", "file_path"):
            _require_nonblank(getattr(self, name), name)
        if (
            not isinstance(self.start_line, int)
            or isinstance(self.start_line, bool)
            or self.start_line < 1
            or not isinstance(self.end_line, int)
            or isinstance(self.end_line, bool)
            or self.end_line < self.start_line
        ):
            raise ValueError("invalid source range")


@dataclass(frozen=True)
class AuthorizedContractMethod:
    name: str
    parameter_types: tuple[str, ...]
    return_type: str
    source: AuthorizedContractSource

    def __post_init__(self) -> None:
        for name in ("name", "return_type"):
            _require_nonblank(getattr(self, name), name)
        if type(self.parameter_types) is not tuple or any(
            not isinstance(value, str) or not value.strip() for value in self.parameter_types
        ):
            raise ValueError("parameter_types must be a tuple of nonblank strings")
        if type(self.source) is not AuthorizedContractSource:
            raise ValueError("source must be an AuthorizedContractSource")


@dataclass(frozen=True)
class AuthorizedContractDeclaration:
    fqcn: str
    methods: tuple[AuthorizedContractMethod, ...]
    sources: tuple[AuthorizedContractSource, ...]

    def __post_init__(self) -> None:
        _require_nonblank(self.fqcn, "fqcn")
        if type(self.methods) is not tuple or any(type(item) is not AuthorizedContractMethod for item in self.methods):
            raise ValueError("methods must be a tuple of AuthorizedContractMethod values")
        if (
            type(self.sources) is not tuple
            or not self.sources
            or any(type(item) is not AuthorizedContractSource for item in self.sources)
        ):
            raise ValueError("sources must be a non-empty tuple of AuthorizedContractSource values")


@runtime_checkable
class AuthorizedContractViewPort(Protocol):
    """Read-only contract declarations authorized for this detector invocation."""

    @property
    def contracts(self) -> Mapping[str, AuthorizedContractDeclaration]: ...


@dataclass(frozen=True)
class AuthorizedContractView:
    """Generic, immutable authorized contract view keyed by exact contract FQCN."""

    contracts: Mapping[str, AuthorizedContractDeclaration]

    def __post_init__(self) -> None:
        if not isinstance(self.contracts, Mapping):
            raise ValueError("contracts must be a mapping")
        copied = dict(self.contracts)
        if any(
            not isinstance(fqcn, str)
            or not fqcn.strip()
            or type(declaration) is not AuthorizedContractDeclaration
            or declaration.fqcn != fqcn
            for fqcn, declaration in copied.items()
        ):
            raise ValueError("contracts must be keyed by their exact contract FQCN")
        frozen = MappingProxyType(copied)
        object.__setattr__(self, "contracts", frozen)


@dataclass(frozen=True)
class MethodDetectionContext:
    """Immutable identity supplied by the caller for one method detection run."""

    repo_id: str
    module_id: str
    service_id: str
    source_revision: str
    generation_id: str
    contract_view: AuthorizedContractViewPort | None = None

    def __post_init__(self) -> None:
        for name in ("repo_id", "module_id", "service_id", "source_revision", "generation_id"):
            _require_nonblank(getattr(self, name), name)
        if self.contract_view is not None and not isinstance(self.contract_view, AuthorizedContractViewPort):
            raise ValueError("contract_view must implement AuthorizedContractViewPort")


@runtime_checkable
class DetectorMetadataPort(Protocol):
    detector_id: str
    detector_version: str
    supported_languages: frozenset[str]
    capabilities: tuple[DetectorCapability, ...]


@runtime_checkable
class MethodDetector(Protocol):
    metadata: DetectorMetadataPort

    def detect_methods(self, snapshot: RepositorySnapshot, context: MethodDetectionContext) -> MethodFacts: ...
