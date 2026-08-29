"""Repository-scoped application service for business-entry lookup."""

from __future__ import annotations

import math
from collections.abc import Callable
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
        self, sub_goal: str, top_k: int = 5, domain: str | None = None, repo_id: str | None = None
    ) -> list[CapabilityMatch]: ...


class _Repository(Protocol):
    def find_realizations(self, repo_id: str, capability_ids: list[str]) -> RepositoryLookup: ...


class BusinessEntryFinder:
    """Orchestrate capability retrieval, graph lookup, and result assembly."""

    def __init__(
        self,
        capability_finder: CapabilityFinder,
        repository: BusinessEntryRepository,
        assembler: Callable[[str, tuple[CapabilityCandidate, ...], RepositoryLookup], object] | None = None,
    ) -> None:
        self._capability_finder: _CapabilityFinder = capability_finder
        self._repository: _Repository = repository
        self._assembler = assemble_business_entry_lookup if assembler is None else assembler

    def find(self, repo_id: str, query: str, *, top_k: int = 5, domain: str | None = None) -> BusinessEntryLookupResult:
        """Find business-entry evidence for a query within one repository."""
        normalized_repo_id = self._validate_inputs(repo_id, query, top_k, domain)
        matches = self._capability_finder.find(query, top_k=top_k, domain=domain, repo_id=normalized_repo_id)
        validated = self._validate_matches(matches, normalized_repo_id)
        candidates = tuple(CapabilityCandidate(match.id, match.distance) for match in validated)

        lookup = self._repository.find_realizations(
            normalized_repo_id, [candidate.capability_id for candidate in candidates]
        )
        if type(lookup) is not RepositoryLookup:
            raise ValueError("lookup must be a RepositoryLookup")

        result = self._assembler(normalized_repo_id, candidates, lookup)
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
    def _validate_matches(matches: object, repo_id: str) -> list[CapabilityMatch]:
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
        return matches
