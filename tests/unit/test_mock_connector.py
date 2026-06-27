from __future__ import annotations

from layerkg.execution.connectors.mock_connector import MockConnector


class TestMockConnector:
    def test_fetch_returns_data(self) -> None:
        conn = MockConnector()
        conn.add_mock_data([{"id": 1}, {"id": 2}])

        result = conn.fetch({})
        assert result == [{"id": 1}, {"id": 2}]

    def test_push_appends(self) -> None:
        conn = MockConnector()
        conn.push({"key": "a"})
        conn.push({"key": "b"})

        assert conn._pushed == [{"key": "a"}, {"key": "b"}]

    def test_health_check(self) -> None:
        conn = MockConnector()
        assert conn.health_check() is True

    def test_sync_returns_count(self) -> None:
        conn = MockConnector()
        conn.add_mock_data([{"id": 1}, {"id": 2}, {"id": 3}])

        count = conn.sync(None)
        assert count == 3
