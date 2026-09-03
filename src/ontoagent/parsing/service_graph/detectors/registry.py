from __future__ import annotations

from ontoagent.parsing.service_graph.detectors.base import Detector
from ontoagent.parsing.service_graph.models import DetectorFacts, RepositorySnapshot


class DetectorRegistry:
    def __init__(self, detectors: list[Detector] | tuple[Detector, ...] = ()) -> None:
        self._detectors: dict[str, Detector] = {}
        for detector in detectors:
            if detector.id in self._detectors:
                raise ValueError(f"duplicate detector id: {detector.id}")
            self._detectors[detector.id] = detector

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._detectors))

    def detect(
        self,
        snapshot: RepositorySnapshot,
        detector_id: str | None = None,
        language: str | None = None,
    ) -> DetectorFacts:
        if detector_id is not None:
            try:
                candidates = [self._detectors[detector_id]]
            except KeyError as exc:
                raise LookupError(f"unknown detector: {detector_id}") from exc
        else:
            candidates = [self._detectors[item_id] for item_id in self.ids]

        requested_language = language.strip().lower() if language is not None else None
        eligible = [
            detector
            for detector in candidates
            if detector.supported_languages & snapshot.languages
            and (
                requested_language is None
                or (requested_language in detector.supported_languages and requested_language in snapshot.languages)
            )
        ]
        if not eligible:
            raise LookupError("no eligible detector")
        return eligible[0].detect(snapshot)
