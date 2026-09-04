"""Generation-bound readiness gate for business-entry queries."""

from __future__ import annotations

from ontoagent.domain.build_binding import BuildBinding
from ontoagent.domain.index_health import BusinessEntryIndexStatus
from ontoagent.domain.query_envelope import QueryBlockReason, QueryEnvelope, QueryEnvelopeStatus
from ontoagent.pipeline.business_entry_finder import BusinessEntryFinder

__all__ = ["QueryBindingGate"]


class QueryBindingGate:
    """Authorize a repository query against one explicit build binding."""

    def __init__(self, binding: BuildBinding, finder: BusinessEntryFinder) -> None:
        self._binding: object = binding
        self._finder = finder

    def find(
        self,
        repo_id: object,
        query: str,
        *,
        top_k: int = 5,
        domain: str | None = None,
        generation_id: object = None,
        source_revision: object = None,
    ) -> QueryEnvelope:
        """Return a bound result only when the explicit binding is query-ready."""
        binding = self._binding
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
