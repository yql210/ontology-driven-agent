from __future__ import annotations

import pytest

from ontoagent.domain.business_entry import LookupReason, RawBusinessEntry
from ontoagent.domain.exceptions import BusinessEntryBackendUnavailable, StoreError
from ontoagent.pipeline.business_entry_repository import BusinessEntryRepository, RepositoryLookup


class FakeGraphStore:
    """Structural graph-store fake with deterministic responses and call recording."""

    def __init__(self, nodes: dict[str, object] | None = None, relations: dict[str, object] | None = None) -> None:
        self.nodes = nodes or {}
        self.relations = relations or {}
        self.calls: list[tuple[object, ...]] = []

    def get_node(self, node_id: str) -> object:
        self.calls.append(("get_node", node_id))
        response = self.nodes.get(node_id)
        if isinstance(response, BaseException):
            raise response
        return response

    def get_relations(self, source_id: str, rel_type: str) -> object:
        self.calls.append(("get_relations", source_id, rel_type))
        response = self.relations.get(source_id, [])
        if isinstance(response, BaseException):
            raise response
        return response


def _capability(capability_id: str = "cap-1", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": capability_id,
        "name": "Orders",
        "businessDomain": "commerce",
        "repoId": "repo-a",
        "entryCodeEntityId": "code-1",
    }
    values.update(overrides)
    return values


def _code(code_id: str = "code-1", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": code_id,
        "name": "create_order",
        "repoId": "repo-a",
        "entryCategory": "http_api",
        "filePath": "src/orders.py",
        "startLine": 10,
        "endLine": 20,
        "entryMetadata": '{"route": "/orders", "method": "POST"}',
    }
    values.update(overrides)
    return values


def _relation(capability_id: str = "cap-1", code_id: str = "code-1", **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {"source_id": capability_id, "target_id": code_id, "rel_type": "REALIZED_BY"}
    values.update(overrides)
    return values


_DEFAULT = object()


def test_generation_scoped_repository_requires_matching_nodes_and_relation() -> None:
    repository, _ = _repository(
        capability=_capability(generationId="gen-a"),
        code=_code(generationId="gen-a"),
        relations=[_relation(properties={"generationId": "gen-a"})],
    )
    result = repository.find_realizations("repo-a", ["cap-1"], generation_id=" gen-a ")
    assert len(result.entries) == 1
    assert result.entries[0].generation_id == "gen-a"


@pytest.mark.parametrize("part", ["capability", "code", "relation"])
def test_generation_scoped_repository_rejects_missing_generation(part: str) -> None:
    capability = _capability(generationId="gen-a")
    code = _code(generationId="gen-a")
    relation = _relation(properties={"generationId": "gen-a"})
    if part == "capability":
        capability.pop("generationId")
    elif part == "code":
        code.pop("generationId")
    else:
        relation["properties"] = {}
    repository, _ = _repository(capability=capability, code=code, relations=[relation])
    result = repository.find_realizations("repo-a", ["cap-1"], generation_id="gen-a")
    assert result.entries == ()
    assert result.reasons == (LookupReason.CORRUPT_GRAPH_DATA,)


def _repository(
    capability: object = _DEFAULT, relations: object = _DEFAULT, code: object = _DEFAULT
) -> tuple[BusinessEntryRepository, FakeGraphStore]:
    store = FakeGraphStore(
        nodes={
            "cap-1": _capability() if capability is _DEFAULT else capability,
            "code-1": _code() if code is _DEFAULT else code,
        },
        relations={"cap-1": [_relation()] if relations is _DEFAULT else relations},
    )
    return BusinessEntryRepository(store), store  # type: ignore[arg-type]


def test_find_realizations_reads_canonical_graph_facts_in_order() -> None:
    repository, store = _repository()

    result = repository.find_realizations("repo-a", ["cap-1"])

    assert result.entries == (
        RawBusinessEntry(
            capability_id="cap-1",
            capability_name="Orders",
            capability_domain="commerce",
            capability_repo_id="repo-a",
            code_entity_id="code-1",
            code_entity_repo_id="repo-a",
            entry_name="create_order",
            entry_category="http_api",
            file_path="src/orders.py",
            start_line=10,
            end_line=20,
            entry_metadata='{"route": "/orders", "method": "POST"}',
        ),
    )
    assert result.reasons == ()
    assert store.calls == [
        ("get_node", "cap-1"),
        ("get_relations", "cap-1", "realized_by"),
        ("get_node", "code-1"),
    ]


def test_find_realizations_isolates_requested_repository_and_deduplicates_candidates() -> None:
    repository, store = _repository(capability=_capability(repoId="repo-b"))

    result = repository.find_realizations("repo-a", ["cap-1", "cap-1"])

    assert result.entries == ()
    assert result.reasons == (LookupReason.REPO_MISMATCH,)
    assert store.calls == [("get_node", "cap-1")]


@pytest.mark.parametrize(
    "repo_id, capability_ids", [(" ", ["cap-1"]), ("repo-a", [" "]), ("repo-a", "cap-1"), ("repo-a", b"cap-1")]
)
def test_find_realizations_rejects_invalid_input_before_store_calls(repo_id: object, capability_ids: object) -> None:
    repository, store = _repository()

    with pytest.raises(ValueError):
        repository.find_realizations(repo_id, capability_ids)  # type: ignore[arg-type]

    assert store.calls == []


def test_find_realizations_accepts_empty_candidates_without_reads() -> None:
    repository, store = _repository()

    assert repository.find_realizations("repo-a", []) == RepositoryLookup((), ())
    assert store.calls == []


@pytest.mark.parametrize(
    ("capability", "expected_reason"),
    [
        (None, LookupReason.NO_CAPABILITY_MATCH),
        (_capability(entryCodeEntityId=" "), LookupReason.CORRUPT_GRAPH_DATA),
        ([], LookupReason.CORRUPT_GRAPH_DATA),
    ],
)
def test_find_realizations_handles_missing_or_invalid_capability(
    capability: object, expected_reason: LookupReason
) -> None:
    repository, store = _repository(capability=capability)

    result = repository.find_realizations("repo-a", ["cap-1"])

    assert result.entries == ()
    assert result.reasons == (expected_reason,)
    assert store.calls == [("get_node", "cap-1")]


@pytest.mark.parametrize(
    "relations",
    [[], [_relation(source_id="other")], [_relation(rel_type="realized_by")], [_relation(target_id=" ")], [object()]],
)
def test_find_realizations_rejects_invalid_or_missing_realizations(relations: object) -> None:
    repository, _ = _repository(relations=relations)

    result = repository.find_realizations("repo-a", ["cap-1"])

    assert result.entries == ()
    expected = (
        (LookupReason.NO_REALIZATION,)
        if relations == []
        else (LookupReason.CORRUPT_GRAPH_DATA, LookupReason.NO_REALIZATION)
    )
    assert result.reasons == expected


def test_find_realizations_deduplicates_valid_relation_targets() -> None:
    repository, store = _repository(relations=[_relation(), _relation()])

    result = repository.find_realizations("repo-a", ["cap-1"])

    assert len(result.entries) == 1
    assert store.calls.count(("get_node", "code-1")) == 1


@pytest.mark.parametrize(
    "capability, code",
    [
        (_capability(id="other"), _code()),
        (_capability(entryCodeEntityId="different"), _code()),
        (_capability(), _code(id="other")),
        (_capability(), _code(name=" ")),
        (_capability(), []),
    ],
)
def test_find_realizations_rejects_inconsistent_or_invalid_graph_nodes(capability: object, code: object) -> None:
    repository, _ = _repository(capability=capability, code=code)

    result = repository.find_realizations("repo-a", ["cap-1"])

    assert result.entries == ()
    assert result.reasons == (LookupReason.CORRUPT_GRAPH_DATA,)


def test_find_realizations_marks_missing_code_as_no_realization() -> None:
    repository, _ = _repository(code=None)

    result = repository.find_realizations("repo-a", ["cap-1"])

    assert result.entries == ()
    assert result.reasons == (LookupReason.NO_REALIZATION,)


def test_find_realizations_rejects_code_from_other_repository() -> None:
    repository, _ = _repository(code=_code(repoId="repo-b"))

    result = repository.find_realizations("repo-a", ["cap-1"])

    assert result.entries == ()
    assert result.reasons == (LookupReason.REPO_MISMATCH,)


@pytest.mark.parametrize(
    ("overrides", "expected_lines"),
    [
        ({"startLine": " 10 ", "endLine": "20"}, (10, 20)),
        ({"startLine": True}, None),
        ({"startLine": 1.5}, None),
        ({"startLine": " "}, None),
        ({"startLine": "10x"}, None),
        ({"startLine": 0}, None),
        ({"startLine": 20, "endLine": 10}, None),
        ({"entryCategory": 1}, None),
        ({"filePath": 1}, None),
        ({"entryMetadata": 1}, None),
    ],
)
def test_find_realizations_validates_optional_entry_fields(
    overrides: dict[str, object], expected_lines: tuple[int, int] | None
) -> None:
    repository, _ = _repository(code=_code(**overrides))

    result = repository.find_realizations("repo-a", ["cap-1"])

    if expected_lines is None:
        assert result.entries == ()
        assert result.reasons == (LookupReason.CORRUPT_GRAPH_DATA,)
    else:
        assert (result.entries[0].start_line, result.entries[0].end_line) == expected_lines


@pytest.mark.parametrize("file_path", ["", "   "])
def test_find_realizations_classifies_blank_stored_file_paths_as_corrupt(file_path: str) -> None:
    repository, _ = _repository(code=_code(filePath=file_path))

    result = repository.find_realizations("repo-a", ["cap-1"])

    assert result.entries == ()
    assert result.reasons == (LookupReason.CORRUPT_GRAPH_DATA,)


def test_find_realizations_preserves_missing_stored_file_path_for_assembler() -> None:
    repository, _ = _repository(code=_code(filePath=None))

    result = repository.find_realizations("repo-a", ["cap-1"])

    assert result.reasons == ()
    assert len(result.entries) == 1
    assert result.entries[0].file_path is None


def test_find_realizations_preserves_valid_entries_and_stably_deduplicates_reasons() -> None:
    store = FakeGraphStore(
        nodes={"cap-1": _capability(), "code-1": _code(), "cap-2": _capability("cap-2", id="cap-2")},
        relations={"cap-1": [_relation()], "cap-2": [_relation("cap-2", "bad")]},
    )
    repository = BusinessEntryRepository(store)  # type: ignore[arg-type]

    result = repository.find_realizations("repo-a", ["cap-1", "missing", "cap-2", "missing"])

    assert [entry.code_entity_id for entry in result.entries] == ["code-1"]
    assert result.reasons == (LookupReason.NO_CAPABILITY_MATCH, LookupReason.CORRUPT_GRAPH_DATA)


@pytest.mark.parametrize(
    "store",
    [
        FakeGraphStore(nodes={"cap-1": StoreError("no")}),
        FakeGraphStore(nodes={"cap-1": _capability()}, relations={"cap-1": RuntimeError("no")}),
        FakeGraphStore(nodes={"cap-1": _capability(), "code-1": StoreError("no")}, relations={"cap-1": [_relation()]}),
    ],
)
def test_find_realizations_wraps_graph_backend_errors(store: FakeGraphStore) -> None:
    repository = BusinessEntryRepository(store)  # type: ignore[arg-type]

    with pytest.raises(BusinessEntryBackendUnavailable, match=r"^Business entry graph backend unavailable$") as error:
        repository.find_realizations("repo-a", ["cap-1"])

    assert isinstance(error.value.__cause__, (StoreError, RuntimeError))


def test_repository_lookup_normalizes_iterables_and_rejects_invalid_members() -> None:
    entry = RawBusinessEntry("cap", "name", "domain", "repo", "code", "repo", "entry", None, None, None, None, None)

    lookup = RepositoryLookup((item for item in [entry]), (item for item in [LookupReason.NO_REALIZATION]))

    assert lookup.entries == (entry,)
    assert lookup.reasons == (LookupReason.NO_REALIZATION,)
    with pytest.raises(ValueError, match="entries"):
        RepositoryLookup([object()], ())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="reasons"):
        RepositoryLookup((), ["no_realization"])  # type: ignore[list-item]
