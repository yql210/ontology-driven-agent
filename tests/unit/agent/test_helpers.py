"""测试 _helpers.py 的 lazy init 和单例模式"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ontoagent.agent import _helpers
from ontoagent.config import OntoAgentConfig


@pytest.fixture(autouse=True)
def reset_helpers() -> None:
    """每个测试前后重置全局单例变量"""
    _helpers._config = None
    _helpers._neo4j = None
    _helpers._chroma = None
    _helpers._aligner = None
    _helpers._clustering = None
    _helpers._impact_propagator = None
    yield
    _helpers._config = None
    _helpers._neo4j = None
    _helpers._chroma = None
    _helpers._aligner = None
    _helpers._clustering = None
    _helpers._impact_propagator = None


def test_get_config_returns_config() -> None:
    """get_config() 返回 OntoAgentConfig 实例，neo4j_uri 不为空"""
    with patch.object(OntoAgentConfig, "from_env") as mock_from_env:
        mock_config = MagicMock(spec=OntoAgentConfig)
        mock_config.neo4j_uri = "bolt://localhost:7687"
        mock_from_env.return_value = mock_config

        config = _helpers.get_config()

        assert config is not None
        assert isinstance(config, MagicMock)
        assert config.neo4j_uri == "bolt://localhost:7687"


def test_get_config_singleton() -> None:
    """多次调用返回同一实例（单例模式）"""
    with patch.object(OntoAgentConfig, "from_env") as mock_from_env:
        mock_config = MagicMock(spec=OntoAgentConfig)
        mock_from_env.return_value = mock_config

        config1 = _helpers.get_config()
        config2 = _helpers.get_config()

        # from_env 只被调用一次
        mock_from_env.assert_called_once()
        # 返回同一实例
        assert config1 is config2


def test_get_neo4j_returns_store() -> None:
    """get_neo4j() 返回 Neo4jGraphStore 实例"""
    with patch("ontoagent.store.neo4j_store.Neo4jGraphStore") as mock_neo4j_class:
        mock_store = MagicMock()
        mock_neo4j_class.return_value = mock_store

        with patch.object(_helpers, "get_config") as mock_get_config:
            mock_config = MagicMock(spec=OntoAgentConfig)
            mock_config.neo4j_uri = "bolt://localhost:7687"
            mock_config.neo4j_user = "neo4j"
            mock_config.neo4j_password = "password"
            mock_get_config.return_value = mock_config

            store = _helpers.get_neo4j()

            assert store is not None
            mock_neo4j_class.assert_called_once_with("bolt://localhost:7687", "neo4j", "password")


def test_get_neo4j_singleton() -> None:
    """get_neo4j() 多次调用返回同一实例"""
    with patch("ontoagent.store.neo4j_store.Neo4jGraphStore") as mock_neo4j_class:
        mock_store = MagicMock()
        mock_neo4j_class.return_value = mock_store

        with patch.object(_helpers, "get_config") as mock_get_config:
            mock_config = MagicMock(spec=OntoAgentConfig)
            mock_config.neo4j_uri = "bolt://localhost:7687"
            mock_config.neo4j_user = "neo4j"
            mock_config.neo4j_password = "password"
            mock_get_config.return_value = mock_config

            store1 = _helpers.get_neo4j()
            store2 = _helpers.get_neo4j()

            # Neo4jGraphStore 只被构造一次
            mock_neo4j_class.assert_called_once()
            # 返回同一实例
            assert store1 is store2


def test_get_chroma_returns_store() -> None:
    """get_chroma() 返回 ChromaStore 实例"""
    with patch("ontoagent.store.chroma_store.ChromaStore") as mock_chroma_class:
        mock_store = MagicMock()
        mock_chroma_class.return_value = mock_store

        with patch.object(_helpers, "get_config") as mock_get_config:
            mock_config = MagicMock(spec=OntoAgentConfig)
            mock_config.chroma_persist_dir = "/tmp/chroma"
            mock_config.ollama_base_url = "http://localhost:11434"
            mock_config.embedding_model = "qwen2.5-coder:0.5b"
            mock_get_config.return_value = mock_config

            store = _helpers.get_chroma()

            assert store is not None
            mock_chroma_class.assert_called_once_with("/tmp/chroma", "http://localhost:11434", "qwen2.5-coder:0.5b")


def test_get_chroma_singleton() -> None:
    """get_chroma() 多次调用返回同一实例"""
    with patch("ontoagent.store.chroma_store.ChromaStore") as mock_chroma_class:
        mock_store = MagicMock()
        mock_chroma_class.return_value = mock_store

        with patch.object(_helpers, "get_config") as mock_get_config:
            mock_config = MagicMock(spec=OntoAgentConfig)
            mock_config.chroma_persist_dir = "/tmp/chroma"
            mock_config.ollama_base_url = "http://localhost:11434"
            mock_config.embedding_model = "qwen2.5-coder:0.5b"
            mock_get_config.return_value = mock_config

            store1 = _helpers.get_chroma()
            store2 = _helpers.get_chroma()

            # ChromaStore 只被构造一次
            mock_chroma_class.assert_called_once()
            # 返回同一实例
            assert store1 is store2


def test_get_aligner_returns_aligner() -> None:
    """get_aligner 返回 ConceptAligner（带 concepts 加载）"""
    with (
        patch("ontoagent.agent._helpers.get_neo4j") as mock_neo4j_fn,
        patch("ontoagent.agent._helpers.get_chroma") as mock_chroma_fn,
        patch("ontoagent.agent._helpers.get_config") as mock_config_fn,
    ):
        mock_neo4j = MagicMock()
        mock_neo4j.query.return_value = []
        mock_neo4j_fn.return_value = mock_neo4j
        mock_chroma_fn.return_value = MagicMock()
        mock_config_fn.return_value = MagicMock()

        from ontoagent.agent._helpers import get_aligner

        aligner = get_aligner()
        assert aligner is not None


def test_get_clustering_returns_clustering() -> None:
    """get_clustering 返回 ModuleClustering"""
    with patch("ontoagent.agent._helpers.get_neo4j") as mock_neo4j_fn:
        mock_neo4j_fn.return_value = MagicMock()

        from ontoagent.agent._helpers import get_clustering

        clustering = get_clustering()
        assert clustering is not None


def test_get_impact_propagator_returns_propagator() -> None:
    """get_impact_propagator 返回 ImpactPropagator"""
    with patch("ontoagent.agent._helpers.get_neo4j") as mock_neo4j_fn:
        mock_neo4j_fn.return_value = MagicMock()

        from ontoagent.agent._helpers import get_impact_propagator

        propagator = get_impact_propagator()
        assert propagator is not None
