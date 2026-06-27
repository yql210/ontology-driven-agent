from __future__ import annotations

import pytest

from ontoagent.execution.connectors.base import Connector, ConnectorRegistry


class _StubConnector(Connector):
    """Minimal concrete connector for registry tests."""

    def fetch(self, params: dict) -> list[dict]:
        return []

    def sync(self, graph_store: object, params: dict | None = None) -> int:
        return 0

    def push(self, data: dict) -> bool:
        return True

    def health_check(self) -> bool:
        return True


class TestConnectorRegistry:
    def test_register_and_get(self) -> None:
        registry = ConnectorRegistry()
        conn = _StubConnector()
        registry.register("stub", conn)

        result = registry.get("stub")
        assert result is conn

    def test_register_duplicate_raises(self) -> None:
        registry = ConnectorRegistry()
        registry.register("stub", _StubConnector())

        with pytest.raises(ValueError, match="already registered"):
            registry.register("stub", _StubConnector())

    def test_get_not_found(self) -> None:
        registry = ConnectorRegistry()

        assert registry.get("nonexistent") is None

    def test_list_connectors(self) -> None:
        registry = ConnectorRegistry()
        registry.register("beta", _StubConnector())
        registry.register("alpha", _StubConnector())

        names = registry.list_connectors()
        assert names == ["alpha", "beta"]

    def test_clear(self) -> None:
        registry = ConnectorRegistry()
        registry.register("stub", _StubConnector())
        registry.clear()

        assert registry.list_connectors() == []
        assert registry.get("stub") is None
