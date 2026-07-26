from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from ontoagent.config import OntoAgentConfig
from ontoagent.store.factory import create_graph_store, create_graph_store_from_env


class TestCreateGraphStore:
    """create_graph_store 路由逻辑测试。"""

    def test_factory_returns_neo4j_store_when_backend_neo4j(self) -> None:
        config = OntoAgentConfig(
            graph_backend="neo4j",
            neo4j_uri="bolt://localhost:7687",
            neo4j_user="neo4j",
            neo4j_password="secret",
        )

        fake_store = MagicMock(name="Neo4jGraphStore")
        with patch(
            "ontoagent.store.factory.Neo4jGraphStore",
            return_value=fake_store,
        ) as mock_ctor:
            result = create_graph_store(config)

        assert result is fake_store
        mock_ctor.assert_called_once_with(
            uri="bolt://localhost:7687",
            user="neo4j",
            password="secret",
        )

    def test_factory_returns_nebula_store_when_backend_nebula(self) -> None:
        config = OntoAgentConfig(
            graph_backend="nebula",
            nebula_host="10.0.0.1",
            nebula_port=10000,
            nebula_user="admin",
            nebula_password="pass",
            nebula_space="my_space",
        )

        fake_store = MagicMock(name="NebulaGraphStore")
        with patch(
            "ontoagent.store.factory.NebulaGraphStore",
            return_value=fake_store,
        ) as mock_ctor:
            result = create_graph_store(config)

        assert result is fake_store
        mock_ctor.assert_called_once_with(
            host="10.0.0.1",
            port=10000,
            user="admin",
            password="pass",
            space="my_space",
        )

    def test_factory_raises_on_invalid_backend(self) -> None:
        config = OntoAgentConfig(graph_backend="redis")

        with pytest.raises(ValueError, match="Unsupported graph_backend"):
            create_graph_store(config)


class TestCreateGraphStoreFromEnv:
    """create_graph_store_from_env 环境变量集成测试。"""

    _env_keys = ("ONTOAGENT_GRAPH_BACKEND",)

    def _stash_env(self) -> dict[str, str | None]:
        return {key: os.getenv(key) for key in self._env_keys}

    def _restore_env(self, stash: dict[str, str | None]) -> None:
        for key, value in stash.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_factory_from_env_uses_env_var(self) -> None:
        stash = self._stash_env()
        try:
            os.environ["ONTOAGENT_GRAPH_BACKEND"] = "nebula"

            fake_store = MagicMock(name="NebulaGraphStore")
            with patch(
                "ontoagent.store.factory.NebulaGraphStore",
                return_value=fake_store,
            ) as mock_ctor:
                result = create_graph_store_from_env()

            assert result is fake_store
            assert mock_ctor.call_count == 1
            _, kwargs = mock_ctor.call_args
            assert kwargs["space"] == OntoAgentConfig().nebula_space
        finally:
            self._restore_env(stash)
