from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from layerkg.config import LayerKGConfig
from layerkg.domain.schema import CodeEntity, Relation
from layerkg.pipeline.builder import LayerKGBuilder


@pytest.fixture
def mock_config() -> LayerKGConfig:
    """创建测试配置。"""
    return LayerKGConfig(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test",
        chroma_persist_dir=None,
        ollama_base_url="http://localhost:11434",
        embedding_model="test-model",
    )


@pytest.fixture
def builder(mock_config: LayerKGConfig) -> LayerKGBuilder:
    """创建 Builder 实例。"""
    return LayerKGBuilder(mock_config)


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """创建临时测试仓库。"""
    (tmp_path / "module1.py").write_text("def foo():\n    pass\n\nclass Bar:\n    pass\n")
    (tmp_path / "module2.py").write_text("def baz():\n    pass\n")
    return tmp_path


@pytest.fixture
def sample_entities() -> list[CodeEntity]:
    """创建示例实体列表。"""
    return [
        CodeEntity(name="foo", entity_type="function"),
        CodeEntity(name="Bar", entity_type="class"),
    ]


class TestStageParse:
    """测试 _stage_parse 方法。"""

    def test_stage_parse_returns_entities_and_relations(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        """验证 _stage_parse 正常工作，返回五元组 (entities, doc_entities, relations, files_scanned, unresolved_imports)。"""
        # Arrange & Act
        all_entities, doc_entities, relations, files_scanned, unresolved_imports = builder._stage_parse(temp_repo)

        # Assert - 返回五元组
        assert isinstance(all_entities, list)
        assert isinstance(doc_entities, list)
        assert isinstance(relations, list)
        assert isinstance(files_scanned, int)
        assert isinstance(unresolved_imports, list)

        # Assert - 扫描了 2 个文件
        assert files_scanned == 2

        # Assert - 至少有 module 实体
        entity_types = {e.entity_type for e in all_entities}
        assert "module" in entity_types

        # Assert - 至少有 contains 关系
        relation_types = {r.relation_type for r in relations}
        assert "contains" in relation_types

        # Assert - _repo_root 被设置
        assert builder._repo_root == temp_repo


class TestStageWriteStructural:
    """测试 _stage_write_structural 方法。"""

    def test_stage_write_structural_raises_on_neo4j_failure(
        self, builder: LayerKGBuilder, sample_entities: list[CodeEntity]
    ) -> None:
        """验证 Stage 2 Neo4j merge_node 失败时抛 RuntimeError。"""
        # Arrange
        relations = [Relation(source_id="1", target_id="2", relation_type="contains")]
        doc_entities: list = []
        unresolved_imports: list = []
        mock_graph = MagicMock()
        mock_graph.ensure_constraints.side_effect = RuntimeError("Neo4j connection failed")

        # Act & Assert
        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            pytest.raises(RuntimeError, match="Stage 2 structural write failed"),
        ):
            builder._stage_write_structural(sample_entities, doc_entities, relations, unresolved_imports)

    def test_stage_write_structural_writes_entities_and_relations(
        self, builder: LayerKGBuilder, sample_entities: list[CodeEntity]
    ) -> None:
        """验证 Stage 2 正常写入实体和关系。"""
        # Arrange
        relations = [Relation(source_id="1", target_id="2", relation_type="contains")]
        doc_entities: list = []
        unresolved_imports: list = []
        mock_graph = MagicMock()

        with patch.object(builder, "_get_graph_store", return_value=mock_graph):
            # Act
            result, ext_entity_count, ext_rel_count = builder._stage_write_structural(
                sample_entities, doc_entities, relations, unresolved_imports
            )

            # Assert
            assert result is mock_graph
            assert ext_entity_count == 0
            assert ext_rel_count == 0
            mock_graph.ensure_constraints.assert_called_once()
            # CodeEntity + DocEntity batch merge calls
            assert mock_graph.merge_nodes_batch.call_count >= 1
            assert mock_graph.merge_relations_batch.call_count >= 1


class TestStageSemantic:
    """测试 _stage_semantic 方法。"""

    def test_stage_semantic_degraded_when_ollama_down(
        self,
        builder: LayerKGBuilder,
        sample_entities: list[CodeEntity],
        temp_repo: Path,
    ) -> None:
        """验证 Stage 3 Ollama 不可用时优雅降级，返回 (0, 0, True, [], [])。"""
        # Arrange
        mock_graph = MagicMock()

        with patch.object(builder, "_check_llm_available", return_value=False):
            # Act
            concepts_created, semantic_rels_created, skipped_semantic, errors, new_concepts = builder._stage_semantic(
                sample_entities, mock_graph, temp_repo
            )

            # Assert
            assert concepts_created == 0
            assert semantic_rels_created == 0
            assert skipped_semantic is True
            assert errors == []
            assert new_concepts == []

    def test_stage_semantic_extracts_when_ollama_available(
        self,
        builder: LayerKGBuilder,
        sample_entities: list[CodeEntity],
        temp_repo: Path,
    ) -> None:
        """验证 Stage 3 Ollama 可用时执行语义提取。"""
        # Arrange
        mock_graph = MagicMock()
        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = MagicMock(relations=[])

        with (
            patch.object(builder, "_check_llm_available", return_value=True),
            patch.object(builder, "_init_semantic_extractor", return_value=mock_extractor),
        ):
            # Act
            _concepts_created, _semantic_rels_created, skipped_semantic, _errors, _new_concepts = (
                builder._stage_semantic(sample_entities, mock_graph, temp_repo)
            )

            # Assert
            mock_extractor.extract.assert_called_once_with(sample_entities, doc_entities=None)
            assert skipped_semantic is False


class TestBuildAborted:
    """测试 build() 的中止行为。"""

    def test_build_aborted_on_stage2_failure(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        """验证 Stage 2 失败 → aborted=True，提前返回。"""
        # Arrange
        mock_graph = MagicMock()
        mock_graph.ensure_constraints.side_effect = RuntimeError("Neo4j connection failed")

        with patch.object(builder, "_get_graph_store", return_value=mock_graph):
            # Act
            result = builder.build(temp_repo)

            # Assert
            assert result.aborted is True
            assert result.entities_created == 0
            assert result.relations_created == 0
            assert len(result.errors) > 0
            assert "Stage 2 structural write failed" in result.errors[0]


class TestBuildElapsed:
    """测试 build() 的计时功能。"""

    def test_build_elapsed_ms_positive(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        """验证 elapsed_ms > 0。"""
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            result = builder.build(temp_repo)

            # Assert
            assert result.elapsed_ms > 0


class TestBuildFullPipeline:
    """测试 build() 完整流水线。"""

    def test_build_full_pipeline_succeeds(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        """端到端正常路径，验证所有阶段成功执行。"""
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
            patch.object(builder, "_check_llm_available", return_value=False),
        ):
            # Act
            result = builder.build(temp_repo)

            # Assert - 所有阶段都执行了
            assert result.aborted is False
            assert result.files_scanned == 2
            assert result.entities_created > 0
            assert result.relations_created > 0
            assert result.skipped_semantic is True
            assert result.elapsed_ms > 0

            # Assert - Neo4j batch 操作被调用
            mock_graph.ensure_constraints.assert_called_once()
            assert mock_graph.merge_nodes_batch.call_count > 0

            # Assert - ChromaDB 操作被调用
            mock_chroma.put_entities_batch.assert_called()


class TestBuildErrorAccumulation:
    """测试 build() 的错误累积行为。"""

    def test_build_continues_after_stage4_failure(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        """验证 Stage 4 失败后继续 Stage 5，结果包含错误但不中止。"""
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
            patch.object(builder, "_check_llm_available", return_value=False),
            patch.object(
                builder,
                "_detect_and_write_modules",
                side_effect=RuntimeError("Clustering failed"),
            ),
        ):
            # Act
            result = builder.build(temp_repo)

            # Assert - 不中止，继续执行 Stage 5
            assert result.aborted is False
            assert result.modules_created == 0

            # Assert - Stage 5 仍然执行
            mock_chroma.put_entities_batch.assert_called()

            # Assert - 错误被记录
            assert len(result.errors) > 0
            assert any("Module clustering error" in e for e in result.errors)

    def test_build_accumulates_errors_from_stages_3_4_5(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        """验证多阶段降级时错误累积到 errors 列表。"""
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        # Stage 3: 语义提取失败
        mock_extractor = MagicMock()
        mock_extractor.extract.side_effect = RuntimeError("Semantic extraction failed")

        # Stage 4: 模块聚类失败
        mock_clustering = MagicMock()
        mock_clustering.detect_modules.side_effect = RuntimeError("Clustering failed")

        # Stage 5: 向量写入失败
        mock_chroma.put_entities_batch.side_effect = RuntimeError("Vector write failed")

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
            patch.object(builder, "_check_llm_available", return_value=True),
            patch.object(builder, "_init_semantic_extractor", return_value=mock_extractor),
            patch.object(builder, "_init_clustering", return_value=mock_clustering),
        ):
            # Act
            result = builder.build(temp_repo)

            # Assert - 不中止，构建完成
            assert result.aborted is False
            assert result.entities_created > 0

            # Assert - 所有错误都被累积
            assert len(result.errors) >= 2
            error_messages = " ".join(result.errors)
            assert "Semantic extraction error" in error_messages
            assert "Module clustering error" in error_messages
            assert "Vector write error" in error_messages
