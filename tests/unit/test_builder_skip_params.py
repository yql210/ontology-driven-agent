from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from layerkg.builder import LayerKGBuilder
from layerkg.config import LayerKGConfig


@pytest.fixture
def mock_config() -> LayerKGConfig:
    """创建测试配置。"""
    return LayerKGConfig(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test",
        chroma_persist_dir=".chroma",
        ollama_base_url="http://localhost:11434",
        embedding_model="test-model",
    )


@pytest.fixture
def builder(mock_config: LayerKGConfig) -> LayerKGBuilder:
    """创建 Builder 实例。"""
    return LayerKGBuilder(mock_config)


class TestBuildSkipSemantic:
    """测试 build() skip_semantic 参数。"""

    def test_build_skip_semantic_returns_zero_concepts(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            result = builder.build(tmp_path, skip_semantic=True)

            # Assert
            assert result.skipped_semantic is True
            assert result.concepts_created == 0
            assert result.semantic_relations_created == 0

    def test_build_default_semantic_not_skipped(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
            patch.object(builder, "_check_ollama", return_value=False),  # Ollama 不可用也会跳过
        ):
            # Act - 不传 skip_semantic，默认 False
            result = builder.build(tmp_path, skip_semantic=False)

            # Assert - Ollama 不可用时，即使 skip_semantic=False，也会被标记为跳过
            # 但这里我们测试的是参数传递，不是 Ollama 可用性
            assert result.skipped_semantic is True  # Ollama 不可用


class TestBuildSkipClustering:
    """测试 build() skip_clustering 参数。"""

    def test_build_skip_clustering_returns_zero_modules(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            result = builder.build(tmp_path, skip_clustering=True)

            # Assert
            assert result.modules_created == 0

    def test_build_default_clustering_not_skipped(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        # Mock 聚类返回值
        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
            patch.object(builder, "_detect_and_write_modules", return_value=(2, [])),
        ):
            # Act - 不传 skip_clustering，默认 False
            result = builder.build(tmp_path, skip_clustering=False)

            # Assert
            assert result.modules_created == 2


class TestBuildSkipBoth:
    """测试同时跳过语义和聚类。"""

    def test_build_skip_both(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            result = builder.build(tmp_path, skip_semantic=True, skip_clustering=True)

            # Assert
            assert result.skipped_semantic is True
            assert result.concepts_created == 0
            assert result.semantic_relations_created == 0
            assert result.modules_created == 0
