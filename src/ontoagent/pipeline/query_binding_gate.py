"""Generation-bound readiness gate for business-entry queries."""

from __future__ import annotations

from typing import Protocol, Self

from ontoagent.domain.build_binding import BuildBinding
from ontoagent.domain.build_manifest import (
    BuildManifestBlockReason,
    BuildManifestResolution,
    BuildManifestResolutionStatus,
)
from ontoagent.domain.index_health import BusinessEntryIndexStatus
from ontoagent.domain.query_envelope import QueryBlockReason, QueryEnvelope, QueryEnvelopeStatus
from ontoagent.pipeline.business_entry_finder import BusinessEntryFinder

__all__ = ["BuildManifestResolver", "QueryBindingGate"]


class BuildManifestResolver(Protocol):
    """Resolve one verified build manifest for a repository build."""

    def resolve(self, repo_id: str, build_id: str) -> BuildManifestResolution:
        """Return the manifest resolution for the requested repository build."""


class QueryBindingGate:
    """Authorize a repository query against one explicit build binding."""

    def __init__(self, binding: BuildBinding, finder: BusinessEntryFinder) -> None:
        self._binding: object = binding
        self._durable = False
        self._resolver: BuildManifestResolver | None = None
        self._finder = finder

    @classmethod
    def from_manifest_store(cls, resolver: BuildManifestResolver, finder: BusinessEntryFinder) -> Self:
        """Create a gate that resolves its binding from durable manifest storage."""
        gate = cls.__new__(cls)
        gate._binding = None
        gate._durable = True
        gate._resolver = resolver if callable(getattr(resolver, "resolve", None)) else None
        gate._finder = finder
        return gate

    def find(
        self,
        repo_id: object,
        query: str,
        *,
        build_id: object = None,
        top_k: int = 5,
        domain: str | None = None,
        generation_id: object = None,
        source_revision: object = None,
    ) -> QueryEnvelope:
        """Return a bound result only when the explicit binding is query-ready."""
        if self._durable:
            return self._find_durable(
                repo_id,
                query,
                build_id=build_id,
                top_k=top_k,
                domain=domain,
                generation_id=generation_id,
                source_revision=source_revision,
            )
        return self._find_with_binding(
            repo_id,
            query,
            self._binding,
            top_k=top_k,
            domain=domain,
            generation_id=generation_id,
            source_revision=source_revision,
        )

    def _find_durable(
        self,
        repo_id: object,
        query: str,
        *,
        build_id: object,
        top_k: int,
        domain: str | None,
        generation_id: object,
        source_revision: object,
    ) -> QueryEnvelope:
        if not _valid_finder_inputs(query, top_k, domain):
            return self._blocked(repo_id, None, QueryBlockReason.BINDING_INVALID)

        requested_repo_id = _optional_text(repo_id)
        requested_build_id = _optional_text(build_id)
        requested_generation_id = _optional_text(generation_id)
        requested_source_revision = _optional_text(source_revision)
        if (
            requested_repo_id is None
            or requested_build_id is None
            or (generation_id is not None and requested_generation_id is None)
            or (source_revision is not None and requested_source_revision is None)
        ):
            return self._blocked(repo_id, None, QueryBlockReason.BINDING_INVALID)

        if self._resolver is None:
            return self._blocked(requested_repo_id, None, QueryBlockReason.MANIFEST_UNAVAILABLE)

        try:
            resolution = self._resolver.resolve(requested_repo_id, requested_build_id)
        except OSError:
            return self._blocked(requested_repo_id, None, QueryBlockReason.MANIFEST_UNAVAILABLE)

        if type(resolution) is not BuildManifestResolution:
            return self._blocked(requested_repo_id, None, QueryBlockReason.MANIFEST_UNAVAILABLE)
        if resolution.status is BuildManifestResolutionStatus.BLOCKED:
            return self._blocked(
                requested_repo_id,
                None,
                _manifest_block_reason(resolution.reasons),
            )

        binding = resolution.binding
        if binding is None or binding.repo_id != requested_repo_id or binding.build_id != requested_build_id:
            return self._blocked(requested_repo_id, None, QueryBlockReason.MANIFEST_UNAVAILABLE)
        return self._find_with_binding(
            requested_repo_id,
            query,
            binding,
            top_k=top_k,
            domain=domain,
            generation_id=generation_id,
            source_revision=source_revision,
        )

    def _find_with_binding(
        self,
        repo_id: object,
        query: str,
        binding: object,
        *,
        top_k: int,
        domain: str | None,
        generation_id: object,
        source_revision: object,
    ) -> QueryEnvelope:
        if type(binding) is not BuildBinding:
            return self._blocked(repo_id, None, QueryBlockReason.BINDING_INVALID)

        requested_repo_id = _optional_text(repo_id)
        requested_generation_id = _optional_text(generation_id)
        requested_source_revision = _optional_text(source_revision)
        if (
            requested_repo_id is None
            or (generation_id is not None and requested_generation_id is None)
            or (source_revision is not None and requested_source_revision is None)
        ):
            return self._blocked(repo_id, binding, QueryBlockReason.BINDING_INVALID)
        if requested_repo_id != binding.repo_id:
            return self._blocked(requested_repo_id, binding, QueryBlockReason.REPO_MISMATCH)
        if requested_generation_id is not None and requested_generation_id != binding.generation_id:
            return self._blocked(requested_repo_id, binding, QueryBlockReason.GENERATION_MISMATCH)
        if requested_source_revision is not None and requested_source_revision != binding.source_revision:
            return self._blocked(requested_repo_id, binding, QueryBlockReason.SOURCE_REVISION_MISMATCH)
        if binding.graph_namespace.backend != "neo4j":
            return self._blocked(requested_repo_id, binding, QueryBlockReason.GRAPH_NAMESPACE_MISMATCH)

        health_status = binding.business_entry_index.status
        if health_status is BusinessEntryIndexStatus.UNAVAILABLE:
            return self._blocked(requested_repo_id, binding, QueryBlockReason.INDEX_UNAVAILABLE)
        if health_status is BusinessEntryIndexStatus.DEGRADED:
            return self._blocked(requested_repo_id, binding, QueryBlockReason.INDEX_DEGRADED)

        result = self._finder.find(
            requested_repo_id,
            query,
            top_k=top_k,
            domain=domain,
            generation_id=binding.generation_id,
        )
        return QueryEnvelope(
            QueryEnvelopeStatus.READY,
            binding.repo_id,
            binding.build_id,
            binding.generation_id,
            binding.source_revision,
            (),
            result,
        )

    @staticmethod
    def _blocked(repo_id: object, binding: BuildBinding | None, reason: QueryBlockReason) -> QueryEnvelope:
        response_repo_id = _optional_text(repo_id) or (binding.repo_id if binding is not None else "unknown")
        return QueryEnvelope(
            QueryEnvelopeStatus.BLOCKED,
            response_repo_id,
            binding.build_id if binding is not None else None,
            binding.generation_id if binding is not None else None,
            binding.source_revision if binding is not None else None,
            (reason,),
            None,
        )


def _optional_text(value: object) -> str | None:
    """Normalize an optional identity without allowing malformed text through."""
    if type(value) is not str or not value.strip():
        return None
    return value.strip()


def _valid_finder_inputs(query: object, top_k: object, domain: object) -> bool:
    """Return whether durable finder inputs meet the query boundary contract."""
    return (
        _optional_text(query) is not None
        and type(top_k) is int
        and 1 <= top_k <= 100
        and (domain is None or _optional_text(domain) is not None)
    )


def _manifest_block_reason(reasons: tuple[BuildManifestBlockReason, ...]) -> QueryBlockReason:
    """Translate a verified manifest-store rejection into a stable query reason."""
    if any(
        reason in {BuildManifestBlockReason.MALFORMED_MANIFEST, BuildManifestBlockReason.SIGNATURE_INVALID}
        for reason in reasons
    ):
        return QueryBlockReason.MANIFEST_INTEGRITY_FAILURE
    return QueryBlockReason.MANIFEST_UNAVAILABLE
