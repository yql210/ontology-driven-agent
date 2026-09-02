"""Immutable build identity and namespace contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ontoagent.domain.index_health import BusinessEntryIndexHealth


def _text(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


@dataclass(frozen=True)
class GraphNamespace:
    backend: str
    endpoint_identity: str
    database_or_space: str

    def __post_init__(self) -> None:
        backend = _text("backend", self.backend)
        if backend not in {"neo4j", "nebula"}:
            raise ValueError("backend must be neo4j or nebula")
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "endpoint_identity", _text("endpoint_identity", self.endpoint_identity))
        object.__setattr__(self, "database_or_space", _text("database_or_space", self.database_or_space))

    def to_dict(self) -> dict[str, str]:
        return {
            "backend": self.backend,
            "endpoint_identity": self.endpoint_identity,
            "database_or_space": self.database_or_space,
        }


@dataclass(frozen=True)
class VectorNamespace:
    backend: str
    server_or_persist_identity: str
    collection_name: str
    embedding_model: str
    schema_version: str

    def __post_init__(self) -> None:
        backend = _text("backend", self.backend)
        if backend != "chroma":
            raise ValueError("backend must be chroma")
        object.__setattr__(self, "backend", backend)
        for name in ("server_or_persist_identity", "collection_name", "embedding_model", "schema_version"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))

    def to_dict(self) -> dict[str, str]:
        return {
            "backend": self.backend,
            "server_or_persist_identity": self.server_or_persist_identity,
            "collection_name": self.collection_name,
            "embedding_model": self.embedding_model,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class BuildBinding:
    binding_version: str
    build_id: str
    repo_id: str
    generation_id: str
    source_revision: str
    schema_version: str
    created_at: str
    graph_namespace: GraphNamespace
    vector_namespace: VectorNamespace
    business_entry_index: BusinessEntryIndexHealth

    def __post_init__(self) -> None:
        if type(self.binding_version) is not str or self.binding_version.strip() != "1":
            raise ValueError("binding_version must be 1")
        object.__setattr__(self, "binding_version", self.binding_version.strip())
        for name in ("build_id", "repo_id", "generation_id", "source_revision", "schema_version"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        if type(self.created_at) is not str:
            raise ValueError("created_at must be a UTC ISO-8601 string")
        timestamp = self.created_at.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise ValueError("created_at must be a UTC ISO-8601 string") from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError("created_at must use UTC")
        object.__setattr__(self, "created_at", parsed.isoformat())
        if type(self.graph_namespace) is not GraphNamespace:
            raise ValueError("graph_namespace must be a GraphNamespace")
        if type(self.vector_namespace) is not VectorNamespace:
            raise ValueError("vector_namespace must be a VectorNamespace")
        if type(self.business_entry_index) is not BusinessEntryIndexHealth:
            raise ValueError("business_entry_index must be a BusinessEntryIndexHealth")
        if self.schema_version != self.vector_namespace.schema_version:
            raise ValueError("schema_version must match vector_namespace.schema_version")

    def to_dict(self) -> dict[str, object]:
        return {
            "binding_version": self.binding_version,
            "build_id": self.build_id,
            "repo_id": self.repo_id,
            "generation_id": self.generation_id,
            "source_revision": self.source_revision,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "graph_namespace": self.graph_namespace.to_dict(),
            "vector_namespace": self.vector_namespace.to_dict(),
            "business_entry_index": self.business_entry_index.to_dict(),
        }
