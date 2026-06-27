from __future__ import annotations

from ontoagent.execution.connectors.base import Connector


class MockConnector(Connector):
    """Mock connector for testing and demonstration."""

    def __init__(self) -> None:
        self._data: list[dict] = []
        self._pushed: list[dict] = []

    def fetch(self, params: dict) -> list[dict]:
        return self._data

    def sync(self, graph_store: object, params: dict | None = None) -> int:
        return len(self._data)

    def push(self, data: dict) -> bool:
        self._pushed.append(data)
        return True

    def health_check(self) -> bool:
        return True

    def add_mock_data(self, data: list[dict]) -> None:
        self._data.extend(data)
