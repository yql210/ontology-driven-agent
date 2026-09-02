"""Repository-scoped application service for business-entry lookup."""

from __future__ import annotations

import math
from typing import Protocol

from ontoagent.domain.business_entry import BusinessEntryLookupResult
from ontoagent.parsing.extractor.capability_finder import CapabilityFinder, CapabilityMatch
from ontoagent.pipeline.business_entry_assembler import (
    CapabilityCandidate,
    assemble_business_entry_lookup,
)
from ontoagent.pipeline.business_entry_repository import BusinessEntryRepository, RepositoryLookup

__all__ = ["BusinessEntryFinder"]


class _CapabilityFinder(Protocol):
    def find(
        self,
        sub_goal: str,
        top_k: int = 5,
        domain: str | None = None,
        repo_id: str | None = None,
        generation_id: str | None = None,
    ) -> list[CapabilityMatch]: ...


class _Repository(Protocol):
    def find_realizations(
        self, repo_id: str, capability_ids: list[str], *, generation_id: str | None = None
    ) -> RepositoryLookup: ...


class BusinessEntryFinder:
    """Orchestrate capability retrieval, graph lookup, and result assembly."""

    def __init__(
        self,
        capability_finder: CapabilityFinder,
        repository: BusinessEntryRepository,
    ) -> None:
        self._capability_finder: _CapabilityFinder = capability_finder
        self._repository: _Repository = repository

    def find(
        self, repo_id: str, query: str, *, top_k: int = 5, domain: str | None = None, generation_id: str | None = None
    ) -> BusinessEntryLookupResult:
        """Find business-entry evidence for a query within one repository."""
        normalized_repo_id = self._validate_inputs(repo_id, query, top_k, domain)
        normalized_generation = _validate_generation_id(generation_id)
        find_kwargs = {"top_k": top_k, "domain": domain, "repo_id": normalized_repo_id}
        if normalized_generation is not None:
            find_kwargs["generation_id"] = normalized_generation
        matches = self._capability_finder.find(query, **find_kwargs)
        validated = self._validate_matches(matches, normalized_repo_id, normalized_generation)
        candidates = tuple(CapabilityCandidate(match.id, match.distance) for match in validated)

        lookup_kwargs = {"generation_id": normalized_generation} if normalized_generation is not None else {}
        lookup = self._repository.find_realizations(
            normalized_repo_id, [candidate.capability_id for candidate in candidates], **lookup_kwargs
        )
        if type(lookup) is not RepositoryLookup:
            raise ValueError("lookup must be a RepositoryLookup")

        result = assemble_business_entry_lookup(normalized_repo_id, candidates, lookup)
        if type(result) is not BusinessEntryLookupResult:
            raise ValueError("result must be a BusinessEntryLookupResult")
        return result

    @staticmethod
    def _validate_inputs(repo_id: object, query: object, top_k: object, domain: object) -> str:
        if not isinstance(repo_id, str) or not repo_id.strip():
            raise ValueError("repo_id must be a nonblank string")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonblank string")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("top_k must be an integer in [1, 100]")
        if domain is not None and (not isinstance(domain, str) or not domain.strip()):
            raise ValueError("domain must be None or a nonblank string")
        return repo_id.strip()

    @staticmethod
    def _validate_matches(matches: object, repo_id: str, generation_id: str | None = None) -> list[CapabilityMatch]:
        if type(matches) is not list:
            raise ValueError("finder must return a list of CapabilityMatch values")
        for match in matches:
            if type(match) is not CapabilityMatch:
                raise ValueError("finder must return a list of CapabilityMatch values")
            if not isinstance(match.id, str) or not match.id.strip():
                raise ValueError("capability match id must be a nonblank string")
            if isinstance(match.distance, bool) or not isinstance(match.distance, (int, float)):
                raise ValueError("capability match distance must be a finite number in [0.0, 2.0]")
            distance = float(match.distance)
            if not math.isfinite(distance) or not 0.0 <= distance <= 2.0:
                raise ValueError("capability match distance must be a finite number in [0.0, 2.0]")
            if not isinstance(match.repo_id, str) or not match.repo_id.strip() or match.repo_id != repo_id:
                raise ValueError("capability match repo_id must match repo_id")
            if generation_id is not None and match.generation_id != generation_id:
                raise ValueError("capability match generation_id must match generation_id")
        return matches


def _validate_generation_id(generation_id: object) -> str | None:
    if generation_id is None:
        return None
    if not isinstance(generation_id, str) or not generation_id.strip():
        raise ValueError("generation_id must be a nonblank string")
    return generation_id.strip()
