"""Repository-scoped graph fact lookup for business-entry realizations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ontoagent.domain.business_entry import LookupReason, RawBusinessEntry
from ontoagent.domain.exceptions import BusinessEntryBackendUnavailable
from ontoagent.store.graph_store import GraphStore


@dataclass(frozen=True)
class RepositoryLookup:
    """Validated raw business entries and stable lookup reasons."""

    entries: tuple[RawBusinessEntry, ...]
    reasons: tuple[LookupReason, ...]

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        reasons = tuple(self.reasons)
        if any(not isinstance(entry, RawBusinessEntry) for entry in entries):
            raise ValueError("entries must contain only RawBusinessEntry values")
        if any(not isinstance(reason, LookupReason) for reason in reasons):
            raise ValueError("reasons must contain only LookupReason values")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "reasons", reasons)


class BusinessEntryRepository:
    """Read and validate Capability-to-CodeEntity realization facts from a graph store."""

    def __init__(self, graph_store: GraphStore) -> None:
        self._graph_store = graph_store

    def find_realizations(self, repo_id: str, capability_ids: Sequence[str]) -> RepositoryLookup:
        """Find verified code realizations for candidate capabilities in one repository."""
        candidates = _validate_lookup_input(repo_id, capability_ids)
        entries: list[RawBusinessEntry] = []
        reasons: list[LookupReason] = []

        for capability_id in candidates:
            capability = self._get_node(capability_id)
            if capability is None:
                _append_reason(reasons, LookupReason.NO_CAPABILITY_MATCH)
                continue
            if not isinstance(capability, Mapping):
                _append_reason(reasons, LookupReason.CORRUPT_GRAPH_DATA)
                continue

            capability_fields = _required_fields(
                capability, ("id", "name", "businessDomain", "repoId", "entryCodeEntityId")
            )
            if capability_fields is None or capability_fields["id"] != capability_id:
                _append_reason(reasons, LookupReason.CORRUPT_GRAPH_DATA)
                continue
            if capability_fields["repoId"] != repo_id:
                _append_reason(reasons, LookupReason.REPO_MISMATCH)
                continue

            target_ids, relation_corrupt = self._realization_target_ids(capability_id)
            if relation_corrupt:
                _append_reason(reasons, LookupReason.CORRUPT_GRAPH_DATA)
            if not target_ids:
                _append_reason(reasons, LookupReason.NO_REALIZATION)
                continue

            for target_id in target_ids:
                if capability_fields["entryCodeEntityId"] != target_id:
                    _append_reason(reasons, LookupReason.CORRUPT_GRAPH_DATA)
                    continue

                code = self._get_node(target_id)
                if code is None:
                    _append_reason(reasons, LookupReason.NO_REALIZATION)
                    continue
                if not isinstance(code, Mapping):
                    _append_reason(reasons, LookupReason.CORRUPT_GRAPH_DATA)
                    continue

                entry = _raw_entry_from_nodes(capability_fields, code, target_id, repo_id)
                if entry is None:
                    _append_reason(reasons, LookupReason.CORRUPT_GRAPH_DATA)
                elif entry is False:
                    _append_reason(reasons, LookupReason.REPO_MISMATCH)
                else:
                    entries.append(entry)

        return RepositoryLookup(entries, reasons)

    def _get_node(self, node_id: str) -> object:
        try:
            return self._graph_store.get_node(node_id)
        except Exception as exc:
            raise BusinessEntryBackendUnavailable("Business entry graph backend unavailable") from exc

    def _realization_target_ids(self, capability_id: str) -> tuple[list[str], bool]:
        try:
            relations = self._graph_store.get_relations(source_id=capability_id, rel_type="realized_by")
        except Exception as exc:
            raise BusinessEntryBackendUnavailable("Business entry graph backend unavailable") from exc

        if not isinstance(relations, Iterable):
            return [], True

        targets: list[str] = []
        corrupt = False
        for relation in relations:
            if not isinstance(relation, Mapping):
                corrupt = True
                continue
            source_id = relation.get("source_id")
            target_id = relation.get("target_id")
            rel_type = relation.get("rel_type")
            if source_id != capability_id or rel_type != "REALIZED_BY" or not _is_nonblank_string(target_id):
                corrupt = True
                continue
            if target_id not in targets:
                targets.append(target_id)
        return targets, corrupt


def _validate_lookup_input(repo_id: str, capability_ids: Sequence[str]) -> list[str]:
    if not _is_nonblank_string(repo_id):
        raise ValueError("repo_id must be a nonblank string")
    if isinstance(capability_ids, (str, bytes)) or not isinstance(capability_ids, Sequence):
        raise ValueError("capability_ids must be a non-string Sequence of nonblank strings")

    candidates: list[str] = []
    for capability_id in capability_ids:
        if not _is_nonblank_string(capability_id):
            raise ValueError("capability_ids must contain only nonblank strings")
        if capability_id not in candidates:
            candidates.append(capability_id)
    return candidates


def _required_fields(node: Mapping[object, object], fields: tuple[str, ...]) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for field in fields:
        value = node.get(field)
        if not _is_nonblank_string(value):
            return None
        values[field] = value
    return values


def _raw_entry_from_nodes(
    capability: Mapping[str, str], code: Mapping[object, object], target_id: str, repo_id: str
) -> RawBusinessEntry | bool | None:
    code_fields = _required_fields(code, ("id", "name", "repoId"))
    if code_fields is None or code_fields["id"] != target_id:
        return None
    if code_fields["repoId"] != repo_id:
        return False

    category_valid, entry_category = _optional_string(code, "entryCategory")
    path_valid, file_path = _optional_string(code, "filePath")
    metadata_valid, entry_metadata = _optional_string(code, "entryMetadata")
    start_valid, start_line = _optional_line(code, "startLine")
    end_valid, end_line = _optional_line(code, "endLine")
    if not all((category_valid, path_valid, metadata_valid, start_valid, end_valid)):
        return None
    if start_line is not None and end_line is not None and end_line < start_line:
        return None
    return RawBusinessEntry(
        capability_id=capability["id"],
        capability_name=capability["name"],
        capability_domain=capability["businessDomain"],
        capability_repo_id=capability["repoId"],
        code_entity_id=code_fields["id"],
        code_entity_repo_id=code_fields["repoId"],
        entry_name=code_fields["name"],
        entry_category=entry_category,
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        entry_metadata=entry_metadata,
    )


def _optional_string(node: Mapping[object, object], field: str) -> tuple[bool, str | None]:
    value = node.get(field)
    if value is not None and not isinstance(value, str):
        return False, None
    return True, value


def _optional_line(node: Mapping[object, object], field: str) -> tuple[bool, int | None]:
    value = node.get(field)
    if value is None:
        return True, None
    if isinstance(value, bool):
        return False, None
    if isinstance(value, int):
        return (True, value) if value > 0 else (False, None)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isascii() and normalized.isdecimal() and int(normalized) > 0:
            return True, int(normalized)
    return False, None


def _is_nonblank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _append_reason(reasons: list[LookupReason], reason: LookupReason) -> None:
    if reason not in reasons:
        reasons.append(reason)
