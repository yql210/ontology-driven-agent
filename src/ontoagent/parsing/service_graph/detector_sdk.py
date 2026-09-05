from __future__ import annotations

from dataclasses import dataclass
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


@runtime_checkable
class DetectorMetadataPort(Protocol):
    detector_id: str
    detector_version: str
    supported_languages: frozenset[str]
    capabilities: tuple[DetectorCapability, ...]


@runtime_checkable
class MethodDetector(Protocol):
    metadata: DetectorMetadataPort

    def detect_methods(self, snapshot: RepositorySnapshot) -> MethodFacts: ...
