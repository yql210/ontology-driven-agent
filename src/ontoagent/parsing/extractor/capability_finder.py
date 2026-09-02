"""CapabilityFinder — semantic search for business capabilities via ChromaDB."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ontoagent.store.chroma_store import ChromaStore

__all__ = ["CapabilityFinder", "CapabilityMatch"]


@dataclass
class CapabilityMatch:
    """A capability match from semantic search.

    Attributes:
        id: CapabilityEntity ID.
        name: Capability name.
        domain: Business domain.
        description: Text description.
        distance: Cosine distance (lower = more similar).
    """

    id: str
    name: str
    domain: str
    description: str
    distance: float
    metadata: dict = field(default_factory=dict)
    repo_id: str | None = None
    entry_code_entity_id: str | None = None
    generation_id: str | None = None


class CapabilityFinder:
    """Semantic search for business capabilities using ChromaDB.

    Usage:
        store = ChromaStore(persist_dir=".chroma")
        finder = CapabilityFinder(store)
        results = finder.find("处理支付", top_k=5)
    """

    def __init__(self, chroma_store: ChromaStore) -> None:
        """Initialize with a ChromaStore instance.

        Args:
            chroma_store: Existing (possibly pre-populated) ChromaStore.
        """
        self._store = chroma_store

    def find(
        self,
        sub_goal: str,
        top_k: int = 5,
        domain: str | None = None,
        repo_id: str | None = None,
        generation_id: str | None = None,
    ) -> list[CapabilityMatch]:
        """Search for capabilities matching a sub-goal.

        Args:
            sub_goal: Natural language description of the desired capability.
            top_k: Maximum number of results.
            domain: Optional business domain filter.
            repo_id: Optional repository filter. None retains cross-repository retrieval.

        Returns:
            Ordered list of CapabilityMatch (best match first).
        """
        normalized_generation = _validate_generation_id(generation_id)
        if repo_id is not None and not repo_id.strip():
            raise ValueError("repo_id cannot be empty or whitespace-only")

        # Some store implementations expose a lightweight metadata listing;
        # validate it when available, without requiring it from ChromaStore.
        if normalized_generation is not None:
            list_entities = getattr(self._store, "list", None)
            if callable(list_entities):
                listed_entities = list_entities()
                if not isinstance(listed_entities, Iterable):
                    raise ValueError("capability metadata listing must be iterable")
                for listed in listed_entities:
                    metadata = listed.get("metadata") if isinstance(listed, dict) else None
                    if (
                        isinstance(metadata, dict)
                        and metadata.get("entity_type") == "CapabilityEntity"
                        and metadata.get("repo_id") == repo_id
                        and metadata.get("generation_id") != normalized_generation
                    ):
                        raise ValueError("capability metadata generation_id must match generation_id")

        predicates = [{"entity_type": "CapabilityEntity"}]
        if domain:
            predicates.append({"business_domain": domain})
        if repo_id is not None:
            predicates.append({"repo_id": repo_id})
        if normalized_generation is not None:
            predicates.append({"generation_id": normalized_generation})
        where = predicates[0] if len(predicates) == 1 else {"$and": predicates}

        raw = self._store.search(sub_goal, n_results=top_k, where=where)

        results: list[CapabilityMatch] = []
        for item in raw:
            meta = item.get("metadata") or {}
            if normalized_generation is not None and meta.get("generation_id") != normalized_generation:
                raise ValueError("capability match generation_id must match generation_id")
            results.append(
                CapabilityMatch(
                    id=item["id"],
                    name=meta.get("name", ""),
                    domain=meta.get("business_domain", ""),
                    description=item.get("text", ""),
                    distance=item.get("distance", 0.0),
                    metadata=meta,
                    repo_id=meta.get("repo_id"),
                    entry_code_entity_id=meta.get("entry_code_entity_id"),
                    generation_id=meta.get("generation_id"),
                )
            )
        return results


def _validate_generation_id(generation_id: object) -> str | None:
    if generation_id is None:
        return None
    if not isinstance(generation_id, str) or not generation_id.strip():
        raise ValueError("generation_id must be a nonblank string")
    return generation_id.strip()
