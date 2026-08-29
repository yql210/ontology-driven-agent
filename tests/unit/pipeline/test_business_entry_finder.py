from __future__ import annotations

import math
from unittest.mock import Mock

import pytest

import ontoagent.pipeline.business_entry_finder as business_entry_finder_module
from ontoagent.domain.business_entry import BusinessEntryLookupResult, LookupReason, LookupStatus
from ontoagent.parsing.extractor.capability_finder import CapabilityMatch
from ontoagent.pipeline.business_entry_assembler import CapabilityCandidate
from ontoagent.pipeline.business_entry_finder import BusinessEntryFinder
from ontoagent.pipeline.business_entry_repository import RepositoryLookup


def _match(capability_id: str = "cap-1", *, repo_id: str | None = "repo-a", distance: float = 0.25) -> CapabilityMatch:
    return CapabilityMatch(capability_id, "Orders", "commerce", "desc", distance, repo_id=repo_id)


def _result(status: LookupStatus = LookupStatus.NOT_FOUND) -> BusinessEntryLookupResult:
    return BusinessEntryLookupResult(status, (), (LookupReason.NO_CAPABILITY_MATCH,))


def _service(
    matches: object = None, lookup: object = None, result: object = None
) -> tuple[BusinessEntryFinder, Mock, Mock, object]:
    finder, repository = Mock(), Mock()
    finder.find.return_value = [] if matches is None else matches
    repository.find_realizations.return_value = RepositoryLookup((), ()) if lookup is None else lookup
    return BusinessEntryFinder(finder, repository), finder, repository, _result() if result is None else result


def test_find_orchestrates_in_order_and_normalizes_repo_id() -> None:
    service, finder, repository, assembler = _service([_match()], RepositoryLookup((), ()), _result())
    assembler_spy = Mock(return_value=assembler)
    events: list[str] = []
    finder.find.side_effect = lambda *a, **k: (events.append("finder"), [_match()])[1]
    repository.find_realizations.side_effect = lambda *a, **k: (events.append("repository"), RepositoryLookup((), ()))[
        1
    ]
    assembler_spy.side_effect = lambda *a, **k: (events.append("assembler"), assembler)[1]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(business_entry_finder_module, "assemble_business_entry_lookup", assembler_spy)
        result = service.find(" repo-a ", "orders", top_k=3, domain="commerce")

    assert result.status is LookupStatus.NOT_FOUND
    assert events == ["finder", "repository", "assembler"]
    finder.find.assert_called_once_with("orders", top_k=3, domain="commerce", repo_id="repo-a")
    repository.find_realizations.assert_called_once_with("repo-a", ["cap-1"])
    assembler_spy.assert_called_once_with("repo-a", (CapabilityCandidate("cap-1", 0.25),), RepositoryLookup((), ()))


def test_find_calls_repository_and_assembler_for_empty_matches() -> None:
    service, finder, repository, assembler = _service([], RepositoryLookup((), ()), _result())
    assembler_spy = Mock(return_value=assembler)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(business_entry_finder_module, "assemble_business_entry_lookup", assembler_spy)
        service.find("repo-a", "orders")

    finder.find.assert_called_once()
    repository.find_realizations.assert_called_once_with("repo-a", [])
    assembler_spy.assert_called_once()


@pytest.mark.parametrize("matches", [(_match(),), (x for x in [_match()]), "abc", b"abc"])
def test_find_requires_finder_to_return_a_list(matches: object) -> None:
    service, _, repository, _ = _service(matches)

    with pytest.raises(ValueError):
        service.find("repo-a", "orders")
    repository.find_realizations.assert_not_called()


@pytest.mark.parametrize("match", [object(), _match(repo_id="repo-b"), _match(capability_id=" "), _match(repo_id=None)])
@pytest.mark.parametrize("distance", [True, math.nan, math.inf, -1.0, 2.1])
def test_find_rejects_malformed_matches(match: object, distance: object) -> None:
    if isinstance(match, CapabilityMatch):
        match = CapabilityMatch(match.id, match.name, match.domain, match.description, distance, repo_id=match.repo_id)
    service, _, repository, _ = _service([match])
    with pytest.raises(ValueError):
        service.find("repo-a", "orders")
    repository.find_realizations.assert_not_called()


@pytest.mark.parametrize(
    ("repo_id", "query", "top_k", "domain"),
    [
        (" ", "q", 5, None),
        ("repo", "", 5, None),
        ("repo", "q", True, None),
        ("repo", "q", 0, None),
        ("repo", "q", 101, None),
        ("repo", "q", 5, " "),
        (1, "q", 5, None),
    ],
)
def test_find_validates_inputs_before_collaborators(
    repo_id: object, query: object, top_k: object, domain: object
) -> None:
    service, finder, repository, _ = _service()
    with pytest.raises(ValueError):
        service.find(repo_id, query, top_k=top_k, domain=domain)  # type: ignore[arg-type]
    finder.find.assert_not_called()
    repository.find_realizations.assert_not_called()


def test_find_requires_exact_lookup_and_result_types() -> None:
    service, _, _repository, _ = _service([_match()], object(), _result())
    with pytest.raises(ValueError):
        service.find("repo-a", "orders")

    service, _, _repository, invalid_result = _service([_match()], RepositoryLookup((), ()), object())
    assembler_spy = Mock(return_value=invalid_result)
    with pytest.raises(ValueError), pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(business_entry_finder_module, "assemble_business_entry_lookup", assembler_spy)
        service.find("repo-a", "orders")


def test_find_propagates_collaborator_exceptions_and_preserves_order() -> None:
    service, finder, repository, _ = _service([_match()])
    finder.find.side_effect = RuntimeError("finder")
    with pytest.raises(RuntimeError, match="finder"):
        service.find("repo-a", "q")
    repository.assert_not_called()

    finder.find.side_effect = None
    repository.find_realizations.side_effect = RuntimeError("repository")
    with pytest.raises(RuntimeError, match="repository"):
        service.find("repo-a", "q")


def test_constructor_rejects_assembler_injection() -> None:
    with pytest.raises(TypeError):
        BusinessEntryFinder(Mock(), Mock(), assembler=Mock())  # type: ignore[call-arg]


def test_find_propagates_assembler_exception() -> None:
    service, _, _, _ = _service([_match()])
    assembler_spy = Mock(side_effect=RuntimeError("assembler"))
    with pytest.raises(RuntimeError, match="assembler"), pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(business_entry_finder_module, "assemble_business_entry_lookup", assembler_spy)
        service.find("repo-a", "orders")


def test_find_rejects_malformed_assembler_output() -> None:
    service, _, _, _ = _service([_match()])
    malformed_assembler = Mock(return_value=object())
    with pytest.raises(ValueError, match="BusinessEntryLookupResult"), pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(business_entry_finder_module, "assemble_business_entry_lookup", malformed_assembler)
        service.find("repo-a", "orders")


def test_find_uses_real_b3_assembler() -> None:
    service, _, _, _ = _service([])
    result = service.find("repo-a", "orders")
    assert result == BusinessEntryLookupResult(LookupStatus.NOT_FOUND, (), (LookupReason.NO_CAPABILITY_MATCH,))
