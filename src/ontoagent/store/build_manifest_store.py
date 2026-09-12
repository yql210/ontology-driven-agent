"""File-backed, HMAC-protected build-binding manifests."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from ontoagent.domain.build_binding import BuildBinding, GraphNamespace, VectorNamespace
from ontoagent.domain.build_manifest import (
    BuildManifestBlockReason,
    BuildManifestResolution,
    BuildManifestResolutionStatus,
)
from ontoagent.domain.index_health import BusinessEntryIndexHealth, BusinessEntryIndexStatus, IndexHealthReason


class FileBuildManifestStore:
    """Persist and resolve one signed build binding at a configured path."""

    def __init__(self, path: Path, secret: str) -> None:
        if not isinstance(path, Path):
            raise ValueError("path must be a Path")
        if type(secret) is not str or not secret.strip():
            raise ValueError("secret must be a nonblank string")
        self._path = path
        self._secret = secret.encode("utf-8")

    def canonical_payload(self, binding: BuildBinding) -> bytes:
        """Return the stable signed representation of a build binding."""
        if type(binding) is not BuildBinding:
            raise ValueError("binding must be a BuildBinding")
        return json.dumps(binding.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    def persist(self, binding: BuildBinding) -> None:
        """Atomically replace the manifest with a freshly signed binding."""
        payload = self.canonical_payload(binding)
        manifest = {**binding.to_dict(), "signature": hmac.new(self._secret, payload, hashlib.sha256).hexdigest()}
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._path.parent, prefix=f".{self._path.name}.", delete=False
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(manifest, temporary_file, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self._path)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    def resolve(self, repo_id: str, build_id: str) -> BuildManifestResolution:
        """Resolve an exact, verified manifest binding or a typed blocked result."""
        if not self._path.is_file():
            return _blocked(BuildManifestBlockReason.MISSING_MANIFEST)
        try:
            raw_manifest = json.loads(self._path.read_text(encoding="utf-8"))
            raw_payload, signature = _raw_binding_payload_and_signature(raw_manifest)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return _blocked(BuildManifestBlockReason.MALFORMED_MANIFEST)
        payload = json.dumps(raw_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        expected_signature = hmac.new(self._secret, payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            return _blocked(BuildManifestBlockReason.SIGNATURE_INVALID)
        try:
            binding = _binding_from_payload(raw_payload)
        except (TypeError, ValueError):
            return _blocked(BuildManifestBlockReason.MALFORMED_MANIFEST)
        if binding.repo_id != repo_id:
            return _blocked(BuildManifestBlockReason.REPO_MISMATCH)
        if binding.build_id != build_id:
            return _blocked(BuildManifestBlockReason.BUILD_MISMATCH)
        return BuildManifestResolution(BuildManifestResolutionStatus.READY, binding, ())


_BINDING_FIELDS = frozenset(
    {
        "binding_version",
        "build_id",
        "repo_id",
        "generation_id",
        "source_revision",
        "schema_version",
        "created_at",
        "graph_namespace",
        "vector_namespace",
        "business_entry_index",
    }
)


def _raw_binding_payload_and_signature(raw_manifest: object) -> tuple[dict[str, object], str]:
    if type(raw_manifest) is not dict or set(raw_manifest) != _BINDING_FIELDS | {"signature"}:
        raise ValueError("manifest has invalid fields")
    signature = raw_manifest["signature"]
    if type(signature) is not str:
        raise ValueError("signature must be a string")
    payload = {field: raw_manifest[field] for field in _BINDING_FIELDS}
    _mapping(payload["graph_namespace"], _GRAPH_FIELDS)
    _mapping(payload["vector_namespace"], _VECTOR_FIELDS)
    _mapping(payload["business_entry_index"], _HEALTH_FIELDS)
    return payload, signature


def _binding_from_payload(raw_payload: Mapping[str, object]) -> BuildBinding:
    graph = _mapping(raw_payload["graph_namespace"], _GRAPH_FIELDS)
    vector = _mapping(raw_payload["vector_namespace"], _VECTOR_FIELDS)
    health = _mapping(raw_payload["business_entry_index"], _HEALTH_FIELDS)
    reasons = health["reasons"]
    if type(reasons) is not list:
        raise ValueError("reasons must be a list")
    return BuildBinding(
        binding_version=raw_payload["binding_version"],
        build_id=raw_payload["build_id"],
        repo_id=raw_payload["repo_id"],
        generation_id=raw_payload["generation_id"],
        source_revision=raw_payload["source_revision"],
        schema_version=raw_payload["schema_version"],
        created_at=raw_payload["created_at"],
        graph_namespace=GraphNamespace(**cast(dict[str, str], graph)),
        vector_namespace=VectorNamespace(**cast(dict[str, str], vector)),
        business_entry_index=BusinessEntryIndexHealth(
            eligible_entries_seen=cast(int, health["eligible_entries_seen"]),
            capabilities_merged=cast(int, health["capabilities_merged"]),
            realized_by_submitted=cast(int, health["realized_by_submitted"]),
            capability_vectors_submitted=cast(int, health["capability_vectors_submitted"]),
            capability_vectors_confirmed=cast(int, health["capability_vectors_confirmed"]),
            capability_vectors_failed=cast(int, health["capability_vectors_failed"]),
            status=BusinessEntryIndexStatus(cast(str, health["status"])),
            reasons=tuple(IndexHealthReason(reason) for reason in reasons),
        ),
    )


_GRAPH_FIELDS = frozenset({"backend", "endpoint_identity", "database_or_space"})
_VECTOR_FIELDS = frozenset(
    {"backend", "server_or_persist_identity", "collection_name", "embedding_model", "schema_version"}
)
_HEALTH_FIELDS = frozenset(
    {
        "eligible_entries_seen",
        "capabilities_merged",
        "realized_by_submitted",
        "capability_vectors_submitted",
        "capability_vectors_confirmed",
        "capability_vectors_failed",
        "status",
        "reasons",
    }
)


def _mapping(value: object, expected_fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise ValueError("manifest has invalid nested fields")
    return dict(value)


def _blocked(reason: BuildManifestBlockReason) -> BuildManifestResolution:
    return BuildManifestResolution(BuildManifestResolutionStatus.BLOCKED, None, (reason,))
