"""测试 _helpers.py 的 lazy init 和单例模式"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from layerkg.agent import _helpers
from layerkg.config import LayerKGConfig


@pytest.fixture(autouse=True)
def reset_helpers() -> None:
    """每个测试前后重置全局单例变量"""
    _helpers._config = None
    _helpers._neo4j = None
    _helpers._chroma = None
    yield
    _helpers._config = None
    _helpers._neo4j = None
    _helpers._chroma = None


def test_get_config_returns_config() -> None:
    """get_config() 返回 LayerKGConfig 实例，neo4j_uri 不为空"""
    with patch.object(LayerKGConfig, "from_env") as mock_from_env:
        mock_config = MagicMock(spec=LayerKGConfig)
        mock_config.neo4j_uri = "bolt://localhost:7687"
        mock_from_env.return_value = mock_config

        config = _helpers.get_config()

        assert config is not None
        assert isinstance(config, MagicMock)
        assert config.neo4j_uri == "bolt://localhost:7687"


def test_get_config_singleton() -> None:
    """多次调用返回同一实例（单例模式）"""
    with patch.object(LayerKGConfig, "from_env") as mock_from_env:
        mock_config = MagicMock(spec=LayerKGConfig)
        mock_from_env.return_value = mock_config

        config1 = _helpers.get_config()
        config2 = _helpers.get_config()

        # from_env 只被调用一次
        mock_from_env.assert_called_once()
        # 返回同一实例
        assert config1 is config2


def test_get_neo4j_returns_store() -> None:
    """get_neo4j() 返回 Neo4jGraphStore 实例"""
    with patch("layerkg.neo4j_store.Neo4jGraphStore") as mock_neo4j_class:
        mock_store = MagicMock()
        mock_neo4j_class.return_value = mock_store

        with patch.object(_helpers, "get_config") as mock_get_config:
            mock_config = MagicMock(spec=LayerKGConfig)
            mock_config.neo4j_uri = "bolt://localhost:7687"
            mock_config.neo4j_user = "neo4j"
            mock_config.neo4j_password = "password"
            mock_get_config.return_value = mock_config

            store = _helpers.get_neo4j()

            assert store is not None
            mock_neo4j_class.assert_called_once_with(
                "bolt://localhost:7687", "neo4j", "password"
            )


def test_get_neo4j_singleton() -> None:
    """get_neo4j() 多次调用返回同一实例"""
    with patch("layerkg.neo4j_store.Neo4jGraphStore") as mock_neo4j_class:
        mock_store = MagicMock()
        mock_neo4j_class.return_value = mock_store

        with patch.object(_helpers, "get_config") as mock_get_config:
            mock_config = MagicMock(spec=LayerKGConfig)
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
    with patch("layerkg.chroma_store.ChromaStore") as mock_chroma_class:
        mock_store = MagicMock()
        mock_chroma_class.return_value = mock_store

        with patch.object(_helpers, "get_config") as mock_get_config:
            mock_config = MagicMock(spec=LayerKGConfig)
            mock_config.chroma_persist_dir = "/tmp/chroma"
            mock_config.ollama_base_url = "http://localhost:11434"
            mock_config.embedding_model = "qwen2.5-coder:0.5b"
            mock_get_config.return_value = mock_config

            store = _helpers.get_chroma()

            assert store is not None
            mock_chroma_class.assert_called_once_with(
                "/tmp/chroma", "http://localhost:11434", "qwen2.5-coder:0.5b"
            )


def test_get_chroma_singleton() -> None:
    """get_chroma() 多次调用返回同一实例"""
    with patch("layerkg.chroma_store.ChromaStore") as mock_chroma_class:
        mock_store = MagicMock()
        mock_chroma_class.return_value = mock_store

        with patch.object(_helpers, "get_config") as mock_get_config:
            mock_config = MagicMock(spec=LayerKGConfig)
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
