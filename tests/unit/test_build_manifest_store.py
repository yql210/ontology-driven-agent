from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

import pytest

from ontoagent.domain.build_binding import BuildBinding, GraphNamespace, VectorNamespace
from ontoagent.domain.build_manifest import BuildManifestBlockReason, BuildManifestResolutionStatus
from ontoagent.domain.index_health import BusinessEntryIndexHealth, BusinessEntryIndexStatus
from ontoagent.store.build_manifest_store import FileBuildManifestStore


def _binding() -> BuildBinding:
    return BuildBinding(
        binding_version="1",
        build_id="build-1",
        repo_id="repo-1",
        generation_id="generation-1",
        source_revision="abc123",
        schema_version="1",
        created_at="2026-09-02T10:00:00Z",
        graph_namespace=GraphNamespace("neo4j", "graph.example", "ontology"),
        vector_namespace=VectorNamespace("chroma", "chroma.example", "business-entry", "embed-v1", "1"),
        business_entry_index=BusinessEntryIndexHealth(
            eligible_entries_seen=1,
            capabilities_merged=1,
            realized_by_submitted=1,
            capability_vectors_submitted=1,
            capability_vectors_confirmed=1,
            capability_vectors_failed=0,
            status=BusinessEntryIndexStatus.HEALTHY,
            reasons=(),
        ),
    )


def test_persist_and_resolve_returns_exact_ready_binding(tmp_path: Path) -> None:
    binding = _binding()
    store = FileBuildManifestStore(tmp_path / "build-manifest.json", "test-secret")

    store.persist(binding)

    resolution = store.resolve("repo-1", "build-1")
    assert resolution.status is BuildManifestResolutionStatus.READY
    assert resolution.binding == binding
    assert resolution.reasons == ()


def test_canonical_payload_is_stable_ascii_compact_json_bytes(tmp_path: Path) -> None:
    store = FileBuildManifestStore(tmp_path / "build-manifest.json", "test-secret")

    payload = store.canonical_payload(_binding())

    assert payload == json.dumps(_binding().to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )
    assert b" " not in payload


def test_resolve_blocks_tampered_payload(tmp_path: Path) -> None:
    path = tmp_path / "build-manifest.json"
    store = FileBuildManifestStore(path, "test-secret")
    store.persist(_binding())
    contents = json.loads(path.read_text(encoding="utf-8"))
    contents["source_revision"] = "tampered"
    path.write_text(json.dumps(contents), encoding="utf-8")

    resolution = store.resolve("repo-1", "build-1")
    assert resolution.status is BuildManifestResolutionStatus.BLOCKED
    assert resolution.binding is None
    assert resolution.reasons == (BuildManifestBlockReason.SIGNATURE_INVALID,)


def test_resolve_blocks_invalidly_typed_tampered_payload_before_binding_reconstruction(tmp_path: Path) -> None:
    path = tmp_path / "build-manifest.json"
    store = FileBuildManifestStore(path, "test-secret")
    store.persist(_binding())
    contents = json.loads(path.read_text(encoding="utf-8"))
    contents["source_revision"] = {"unexpected": "mapping"}
    path.write_text(json.dumps(contents), encoding="utf-8")

    resolution = store.resolve("repo-1", "build-1")
    assert resolution.status is BuildManifestResolutionStatus.BLOCKED
    assert resolution.binding is None
    assert resolution.reasons == (BuildManifestBlockReason.SIGNATURE_INVALID,)


def test_resolve_blocks_manifest_signed_with_wrong_secret(tmp_path: Path) -> None:
    path = tmp_path / "build-manifest.json"
    FileBuildManifestStore(path, "correct-secret").persist(_binding())

    resolution = FileBuildManifestStore(path, "wrong-secret").resolve("repo-1", "build-1")
    assert resolution.status is BuildManifestResolutionStatus.BLOCKED
    assert resolution.binding is None
    assert resolution.reasons == (BuildManifestBlockReason.SIGNATURE_INVALID,)


def test_resolve_blocks_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "build-manifest.json"
    path.write_text("{", encoding="utf-8")

    resolution = FileBuildManifestStore(path, "test-secret").resolve("repo-1", "build-1")
    assert resolution.status is BuildManifestResolutionStatus.BLOCKED
    assert resolution.binding is None
    assert resolution.reasons == (BuildManifestBlockReason.MALFORMED_MANIFEST,)


def test_resolve_blocks_missing_file(tmp_path: Path) -> None:
    resolution = FileBuildManifestStore(tmp_path / "missing.json", "test-secret").resolve("repo-1", "build-1")
    assert resolution.status is BuildManifestResolutionStatus.BLOCKED
    assert resolution.binding is None
    assert resolution.reasons == (BuildManifestBlockReason.MISSING_MANIFEST,)


@pytest.mark.parametrize(
    ("repo_id", "build_id", "reason"),
    [
        ("other-repo", "build-1", BuildManifestBlockReason.REPO_MISMATCH),
        ("repo-1", "other-build", BuildManifestBlockReason.BUILD_MISMATCH),
    ],
)
def test_resolve_blocks_identity_mismatch(
    tmp_path: Path, repo_id: str, build_id: str, reason: BuildManifestBlockReason
) -> None:
    store = FileBuildManifestStore(tmp_path / "build-manifest.json", "test-secret")
    store.persist(_binding())

    resolution = store.resolve(repo_id, build_id)
    assert resolution.status is BuildManifestResolutionStatus.BLOCKED
    assert resolution.binding is None
    assert resolution.reasons == (reason,)


def test_persisted_manifest_does_not_contain_secret(tmp_path: Path) -> None:
    path = tmp_path / "build-manifest.json"
    store = FileBuildManifestStore(path, "sensitive-secret")

    store.persist(_binding())

    assert "sensitive-secret" not in path.read_text(encoding="utf-8")


def test_persist_fsyncs_containing_directory_after_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "build-manifest.json"
    store = FileBuildManifestStore(path, "test-secret")
    events: list[str] = []
    directory_fd = 9876
    real_replace = os.replace
    real_open = os.open
    real_fsync = os.fsync
    real_close = os.close

    def record_replace(source: str | Path, destination: str | Path) -> None:
        events.append("replace")
        real_replace(source, destination)

    def open_directory(directory: str | bytes | Path, flags: int, mode: int = 0o777) -> int:
        if directory == path.parent:
            assert flags == os.O_RDONLY
            events.append("open")
            return directory_fd
        return real_open(directory, flags, mode)

    def record_fsync(fd: int) -> None:
        if fd == directory_fd:
            events.append("fsync")
            return
        real_fsync(fd)

    def close_directory(fd: int) -> None:
        if fd == directory_fd:
            events.append("close")
            return
        real_close(fd)

    monkeypatch.setattr("ontoagent.store.build_manifest_store.os.replace", record_replace)
    monkeypatch.setattr("ontoagent.store.build_manifest_store.os.open", open_directory)
    monkeypatch.setattr("ontoagent.store.build_manifest_store.os.fsync", record_fsync)
    monkeypatch.setattr("ontoagent.store.build_manifest_store.os.close", close_directory)

    store.persist(_binding())

    assert events == ["replace", "open", "fsync", "close"]


def test_resolve_blocks_correctly_signed_manifest_with_extra_top_level_field(tmp_path: Path) -> None:
    path = tmp_path / "build-manifest.json"
    store = FileBuildManifestStore(path, "test-secret")
    store.persist(_binding())
    contents = json.loads(path.read_text(encoding="utf-8"))
    contents["unexpected"] = "field"
    payload = {field: value for field, value in contents.items() if field != "signature"}
    contents["signature"] = hmac.new(
        b"test-secret",
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    path.write_text(json.dumps(contents), encoding="utf-8")

    resolution = store.resolve("repo-1", "build-1")

    assert resolution.status is BuildManifestResolutionStatus.BLOCKED
    assert resolution.binding is None
    assert resolution.reasons == (BuildManifestBlockReason.MALFORMED_MANIFEST,)


def test_persist_cleans_up_temp_file_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "build-manifest.json"
    store = FileBuildManifestStore(path, "test-secret")

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("ontoagent.store.build_manifest_store.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.persist(_binding())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "secret,path", [("", Path("manifest.json")), ("   ", Path("manifest.json")), ("key", "manifest.json")]
)
def test_constructor_rejects_blank_secret_and_non_path(secret: str, path: object) -> None:
    with pytest.raises(ValueError):
        FileBuildManifestStore(path, secret)  # type: ignore[arg-type]
