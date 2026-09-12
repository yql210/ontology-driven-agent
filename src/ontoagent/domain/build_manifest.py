"""Signed build-manifest resolution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ontoagent.domain.build_binding import BuildBinding


class BuildManifestResolutionStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class BuildManifestBlockReason(StrEnum):
    MISSING_MANIFEST = "missing_manifest"
    MALFORMED_MANIFEST = "malformed_manifest"
    SIGNATURE_INVALID = "signature_invalid"
    REPO_MISMATCH = "repo_mismatch"
    BUILD_MISMATCH = "build_mismatch"


@dataclass(frozen=True)
class BuildManifestResolution:
    """The verified manifest binding, or a typed reason it cannot be used."""

    status: BuildManifestResolutionStatus
    binding: BuildBinding | None
    reasons: tuple[BuildManifestBlockReason, ...]

    def __post_init__(self) -> None:
        if type(self.status) is not BuildManifestResolutionStatus:
            raise ValueError("status must be a BuildManifestResolutionStatus")
        if type(self.reasons) is not tuple or any(
            type(reason) is not BuildManifestBlockReason for reason in self.reasons
        ):
            raise ValueError("reasons must be a tuple of BuildManifestBlockReason")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("reasons must not contain duplicates")
        if self.reasons != tuple(reason for reason in BuildManifestBlockReason if reason in self.reasons):
            raise ValueError("reasons must use BuildManifestBlockReason declaration order")
        if self.status is BuildManifestResolutionStatus.READY:
            if type(self.binding) is not BuildBinding or self.reasons:
                raise ValueError("READY resolution requires a binding and no reasons")
        elif self.binding is not None or not self.reasons:
            raise ValueError("BLOCKED resolution requires reasons and no binding")
