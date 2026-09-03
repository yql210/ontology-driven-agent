from __future__ import annotations

from typing import Protocol

from ontoagent.parsing.service_graph.models import DetectorFacts, RepositorySnapshot


class Detector(Protocol):
    id: str
    version: str
    supported_languages: frozenset[str]

    def detect(self, snapshot: RepositorySnapshot) -> DetectorFacts: ...
