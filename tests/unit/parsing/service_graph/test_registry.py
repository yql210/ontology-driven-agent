from pathlib import Path

import pytest

from ontoagent.parsing.service_graph.detectors.registry import DetectorRegistry
from ontoagent.parsing.service_graph.models import DetectorFacts, RepositorySnapshot


class D:
    id = "d"
    version = "1"
    supported_languages = frozenset({"java"})

    def detect(self, snapshot):
        return DetectorFacts(self.id, self.version, snapshot.repo_id, snapshot.source_revision, (), (), (), ())


class E(D):
    id = "e"


def test_registry_explicit_registration_sorting_and_language_filter():
    registry = DetectorRegistry([E(), D()])
    assert registry.ids == ("d", "e")
    snapshot = RepositorySnapshot("r", "v", Path("."), frozenset({"JAVA"}))
    assert registry.detect(snapshot).detector_id == "d"
    assert registry.detect(snapshot, language="java").detector_id == "d"


def test_registry_rejects_duplicates_and_unknown_or_ineligible():
    with pytest.raises(ValueError):
        DetectorRegistry([D(), D()])
    registry = DetectorRegistry([D()])
    snapshot = RepositorySnapshot("r", "v", Path("."), frozenset({"python"}))
    with pytest.raises(LookupError):
        registry.detect(snapshot)
    with pytest.raises(LookupError):
        registry.detect(snapshot, detector_id="nope")
