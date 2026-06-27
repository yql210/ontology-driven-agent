"""Tests for builder module clustering (Stage 4) and vector write (Stage 5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from layerkg.config import LayerKGConfig
from layerkg.domain.schema import CodeEntity, ModuleEntity
from layerkg.pipeline.builder import LayerKGBuilder
from layerkg.pipeline.module_clustering import ModuleCluster
from layerkg.store.schema_version import SchemaStatus


@pytest.fixture
def builder() -> LayerKGBuilder:
    """创建 mock 依赖的 builder。"""
    config = LayerKGConfig(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test",
    )
    return LayerKGBuilder(config)


def _make_cluster(name: str = "test_module", count: int = 3) -> ModuleCluster:
    """创建测试用 ModuleCluster。"""
    module = ModuleEntity(name=name)
    return ModuleCluster(
        module=module,
        entity_ids=[f"entity_{i}" for i in range(count)],
        cohesion=0.8,
        entity_count=count,
    )


class TestInitClustering:
    def test_init_clustering_lazy_init(self, builder: LayerKGBuilder) -> None:
        """验证 lazy init：首次创建，二次复用。"""
        with patch.object(builder, "_get_graph_store") as mock_gs:
            mock_gs.return_value = MagicMock()
            c1 = builder._init_clustering()
            c2 = builder._init_clustering()
            assert c1 is c2
            mock_gs.assert_called_once()


class TestDetectAndWriteModules:
    def test_detect_and_write_modules_success(self, builder: LayerKGBuilder) -> None:
        """3 clusters → (3, clusters)。"""
        clusters = [_make_cluster("m1"), _make_cluster("m2"), _make_cluster("m3")]
        mock_clustering = MagicMock()
        mock_clustering.detect_modules.return_value = clusters
        mock_clustering.save_modules.return_value = 3

        all_entities = [
            CodeEntity(name="test", entity_type="module", file_path="/test.py"),
            CodeEntity(name="func1", entity_type="function", file_path="/test.py"),
        ]

        with patch.object(builder, "_init_clustering", return_value=mock_clustering):
            count, result = builder._detect_and_write_modules(MagicMock(), all_entities)

        assert count == 3
        assert result == clusters
        mock_clustering.save_modules.assert_called_once_with(clusters, all_entities)

    def test_detect_and_write_modules_empty_graph(self, builder: LayerKGBuilder) -> None:
        """空图 → detect_modules 返回 [] → (0, [])。"""
        mock_clustering = MagicMock()
        mock_clustering.detect_modules.return_value = []

        with patch.object(builder, "_init_clustering", return_value=mock_clustering):
            count, result = builder._detect_and_write_modules(MagicMock(), [])

        assert count == 0
        assert result == []
        mock_clustering.save_modules.assert_not_called()

    def test_detect_and_write_modules_exception(self, builder: LayerKGBuilder) -> None:
        """异常 → 抛出 RuntimeError。"""
        mock_clustering = MagicMock()
        mock_clustering.detect_modules.side_effect = RuntimeError("graph error")

        with (
            patch.object(builder, "_init_clustering", return_value=mock_clustering),
            pytest.raises(RuntimeError, match="graph error"),
        ):
            builder._detect_and_write_modules(MagicMock(), [])


class TestWriteAllVectors:
    def test_write_all_vectors_code_entities_only(self, builder: LayerKGBuilder) -> None:
        """CodeEntity 向量写入。"""
        from layerkg.domain.schema import CodeEntity

        entity = CodeEntity(
            name="my_func",
            entity_type="function",
            file_path="/test.py",
            source="def my_func(): pass",
        )
        mock_chroma = MagicMock()

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([entity], [], [], [])

        mock_chroma.put_entities_batch.assert_called_once()
        items = mock_chroma.put_entities_batch.call_args[0][0]
        assert len(items) == 1
        assert items[0][0] == entity.id
        assert "my_func" in items[0][1]

    def test_write_all_vectors_concept_entities_only(self, builder: LayerKGBuilder) -> None:
        """ConceptEntity 向量写入。"""
        from layerkg.domain.schema import ConceptEntity

        concept = ConceptEntity(
            name="retry_pattern", entity_type="design_pattern", description="Retry failed operations"
        )
        mock_chroma = MagicMock()

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([], [], [concept], [])

        mock_chroma.put_entities_batch.assert_called_once()
        items = mock_chroma.put_entities_batch.call_args[0][0]
        assert len(items) == 1
        assert items[0][1] == "Retry failed operations"

    def test_write_all_vectors_module_clusters(self, builder: LayerKGBuilder) -> None:
        """ModuleCluster 向量写入。"""
        cluster = _make_cluster("auth_module")
        mock_chroma = MagicMock()

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([], [], [], [cluster])

        mock_chroma.put_entities_batch.assert_called_once()
        items = mock_chroma.put_entities_batch.call_args[0][0]
        assert len(items) == 1
        assert items[0][2]["entity_type"] == "module"

    def test_write_all_vectors_mixed_types(self, builder: LayerKGBuilder) -> None:
        """混合三种实体类型。"""
        from layerkg.domain.schema import CodeEntity, ConceptEntity

        entity = CodeEntity(name="fn", entity_type="function", file_path="/a.py", source="def fn(): pass")
        concept = ConceptEntity(name="c1", entity_type="business_concept", description="desc")
        cluster = _make_cluster("mod")

        mock_chroma = MagicMock()
        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([entity], [], [concept], [cluster])

        items = mock_chroma.put_entities_batch.call_args[0][0]
        assert len(items) == 3

    def test_write_all_vectors_empty_lists(self, builder: LayerKGBuilder) -> None:
        """空列表 → 不调用 ChromaDB。"""
        mock_chroma = MagicMock()
        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([], [], [], [])

        mock_chroma.put_entities_batch.assert_not_called()


class TestBuildIntegration:
    def test_build_full_pipeline_with_modules(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """完整流水线包含 Stage 4+5。"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass\n")

        clusters = [_make_cluster("m1"), _make_cluster("m2")]
        with (
            patch("layerkg.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store") as mock_gs,
            patch.object(builder, "_get_chroma_store") as mock_chroma,
            patch.object(builder, "_check_llm_available", return_value=False),
            patch.object(builder, "_detect_and_write_modules") as mock_dm,
        ):
            mock_graph = MagicMock()
            mock_gs.return_value = mock_graph
            mock_dm.return_value = (2, clusters)

            result = builder.build(tmp_path)

        assert result.modules_created == 2
        mock_dm.assert_called_once()
        # 验证 _write_all_vectors 被调用（通过 chroma_store 访问）
        mock_chroma.assert_called()
        # 验证 _detect_and_write_modules 的调用参数
        assert mock_dm.call_args.args[0] == mock_graph
        # 验证第二个参数是 all_entities (list[CodeEntity])
        assert isinstance(mock_dm.call_args.args[1], list)

    def test_build_chroma_failure_records_error(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """ChromaDB 写入失败 → error 记录但不中断。"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass\n")

        with (
            patch("layerkg.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store") as mock_gs,
            patch.object(builder, "_check_llm_available", return_value=False),
            patch.object(builder, "_detect_and_write_modules", return_value=(0, [])),
        ):
            mock_gs.return_value = MagicMock()
            # 让 _write_all_vectors 中的 _get_chroma_store 抛异常
            with patch.object(builder, "_get_chroma_store", side_effect=RuntimeError("chroma down")):
                result = builder.build(tmp_path)

        assert len(result.errors) >= 1
        assert any("chroma" in str(e).lower() or "vector" in str(e).lower() for e in result.errors)
