"""Tests for builder module clustering (Stage 4) and vector write (Stage 5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.schema import CodeEntity, ModuleEntity
from ontoagent.pipeline.builder import OntoAgentBuilder
from ontoagent.pipeline.module_clustering import ModuleCluster
from ontoagent.store.schema_version import SchemaStatus


@pytest.fixture
def builder() -> OntoAgentBuilder:
    """创建 mock 依赖的 builder。"""
    config = OntoAgentConfig(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test",
    )
    return OntoAgentBuilder(config)


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
    def test_init_clustering_lazy_init(self, builder: OntoAgentBuilder) -> None:
        """验证 lazy init：首次创建，二次复用。"""
        with patch.object(builder, "_get_graph_store") as mock_gs:
            mock_gs.return_value = MagicMock()
            c1 = builder._init_clustering()
            c2 = builder._init_clustering()
            assert c1 is c2
            mock_gs.assert_called_once()


class TestDetectAndWriteModules:
    def test_detect_and_write_modules_success(self, builder: OntoAgentBuilder) -> None:
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

    def test_detect_and_write_modules_empty_graph(self, builder: OntoAgentBuilder) -> None:
        """空图 → detect_modules 返回 [] → (0, [])。"""
        mock_clustering = MagicMock()
        mock_clustering.detect_modules.return_value = []

        with patch.object(builder, "_init_clustering", return_value=mock_clustering):
            count, result = builder._detect_and_write_modules(MagicMock(), [])

        assert count == 0
        assert result == []
        mock_clustering.save_modules.assert_not_called()

    def test_detect_and_write_modules_exception(self, builder: OntoAgentBuilder) -> None:
        """异常 → 抛出 RuntimeError。"""
        mock_clustering = MagicMock()
        mock_clustering.detect_modules.side_effect = RuntimeError("graph error")

        with (
            patch.object(builder, "_init_clustering", return_value=mock_clustering),
            pytest.raises(RuntimeError, match="graph error"),
        ):
            builder._detect_and_write_modules(MagicMock(), [])


class TestWriteAllVectors:
    def test_write_all_vectors_capability_preserves_repository_identity(self, builder: OntoAgentBuilder) -> None:
        """Capability vectors retain the graph identity metadata used for repo isolation."""
        capability = {
            "id": "cap-repo-a-entry-1",
            "name": "process_payment",
            "business_domain": "payment",
            "description": "Process a payment.",
            "keywords": '["payment", "charge"]',
            "repo_id": "repo-a",
            "entry_code_entity_id": "entry-1",
        }
        mock_chroma = MagicMock()

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([], [], [], [], capability_dicts=[capability])

        item = mock_chroma.put_entities_batch_with_outcome.call_args.args[0][0]
        assert item[0] == "cap-repo-a-entry-1"
        assert "process_payment" in item[1]
        assert "Process a payment." in item[1]
        assert "charge" in item[1]
        assert item[2] == {
            "entity_type": "CapabilityEntity",
            "name": "process_payment",
            "business_domain": "payment",
            "repo_id": "repo-a",
            "entry_code_entity_id": "entry-1",
        }

    def test_write_all_vectors_capability_omits_blank_legacy_identity(self, builder: OntoAgentBuilder) -> None:
        """Legacy capability inputs remain writable without fabricated identity metadata."""
        capability = {
            "id": "legacy-capability",
            "name": "process_payment",
            "business_domain": "payment",
            "description": "Process a payment.",
            "repo_id": " ",
            "entry_code_entity_id": "",
        }
        mock_chroma = MagicMock()

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([], [], [], [], capability_dicts=[capability])

        metadata = mock_chroma.put_entities_batch_with_outcome.call_args.args[0][0][2]
        assert metadata == {
            "entity_type": "CapabilityEntity",
            "name": "process_payment",
            "business_domain": "payment",
        }

    def test_capability_identity_matches_graph_and_vectors_across_repositories(self, builder: OntoAgentBuilder) -> None:
        """Extractor-derived capability identity stays aligned at both builder handoffs."""
        graph_store = MagicMock()
        repo_a_entry = CodeEntity(
            id="entry-a",
            name="process_payment",
            entity_type="function",
            repo_id="repo-a",
            file_path="api/payment.py",
        )
        repo_a_entry.entry_category = "http_api"
        repo_b_entry = CodeEntity(
            id="entry-b",
            name="process_payment",
            entity_type="function",
            repo_id="repo-b",
            file_path="api/payment.py",
        )
        repo_b_entry.entry_category = "http_api"

        builder._extract_capabilities([repo_a_entry], graph_store, "2026-01-01T00:00:00+00:00")
        repo_a_capability = builder._capability_dicts[0]
        builder._extract_capabilities([repo_b_entry], graph_store, "2026-01-01T00:00:00+00:00")
        repo_b_capability = builder._capability_dicts[0]

        assert repo_a_capability["repo_id"] == "repo-a"
        assert repo_a_capability["entry_code_entity_id"] == "entry-a"
        assert repo_a_capability["id"] != repo_b_capability["id"]
        relation_batches = [call.args[0] for call in graph_store.merge_relations_batch.call_args_list]
        assert relation_batches[0][0]["source_id"] == repo_a_capability["id"]

        mock_chroma = MagicMock()
        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([], [], [], [], capability_dicts=[repo_a_capability, repo_b_capability])

        vector_items = mock_chroma.put_entities_batch_with_outcome.call_args.args[0]
        assert [item[0] for item in vector_items] == [repo_a_capability["id"], repo_b_capability["id"]]
        assert vector_items[0][2]["repo_id"] == repo_a_capability["repo_id"]
        assert vector_items[0][2]["entry_code_entity_id"] == repo_a_capability["entry_code_entity_id"]
        assert vector_items[1][2]["repo_id"] == repo_b_capability["repo_id"]
        assert vector_items[1][2]["entry_code_entity_id"] == repo_b_capability["entry_code_entity_id"]

    def test_write_all_vectors_code_entities_only(self, builder: OntoAgentBuilder) -> None:
        """CodeEntity 向量写入。"""
        from ontoagent.domain.schema import CodeEntity

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

    def test_write_all_vectors_concept_entities_only(self, builder: OntoAgentBuilder) -> None:
        """ConceptEntity 向量写入。"""
        from ontoagent.domain.schema import ConceptEntity

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

    def test_write_all_vectors_module_clusters(self, builder: OntoAgentBuilder) -> None:
        """ModuleCluster 向量写入。"""
        cluster = _make_cluster("auth_module")
        mock_chroma = MagicMock()

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([], [], [], [cluster])

        mock_chroma.put_entities_batch.assert_called_once()
        items = mock_chroma.put_entities_batch.call_args[0][0]
        assert len(items) == 1
        assert items[0][2]["entity_type"] == "module"

    def test_write_all_vectors_mixed_types(self, builder: OntoAgentBuilder) -> None:
        """混合三种实体类型。"""
        from ontoagent.domain.schema import CodeEntity, ConceptEntity

        entity = CodeEntity(name="fn", entity_type="function", file_path="/a.py", source="def fn(): pass")
        concept = ConceptEntity(name="c1", entity_type="business_concept", description="desc")
        cluster = _make_cluster("mod")

        mock_chroma = MagicMock()
        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([entity], [], [concept], [cluster])

        items = mock_chroma.put_entities_batch.call_args[0][0]
        assert len(items) == 3

    def test_write_all_vectors_empty_lists(self, builder: OntoAgentBuilder) -> None:
        """空列表 → 不调用 ChromaDB。"""
        mock_chroma = MagicMock()
        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            builder._write_all_vectors([], [], [], [])

        mock_chroma.put_entities_batch.assert_not_called()


class TestBuildIntegration:
    def test_build_resets_capabilities_before_failed_extraction(
        self, builder: OntoAgentBuilder, tmp_path: Path
    ) -> None:
        """A reused builder cannot write a prior build's capabilities after Stage 2.7 fails."""
        (tmp_path / "test.py").write_text("def hello(): pass\n")
        mock_graph = MagicMock()
        repo_a_entry = CodeEntity(
            id="entry-a",
            name="process_payment",
            entity_type="function",
            repo_id="repo-a",
            file_path="api/payment.py",
        )
        repo_a_entry.entry_category = "http_api"
        builder._extract_capabilities([repo_a_entry], mock_graph, "2026-01-01T00:00:00+00:00")
        assert builder._capability_dicts[0]["repo_id"] == "repo-a"

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_check_llm_available", return_value=False),
            patch.object(builder, "_detect_and_write_modules", return_value=(0, [])),
            patch.object(builder, "_extract_capabilities", side_effect=RuntimeError("extract failed")),
            patch.object(builder, "_write_all_vectors") as write_vectors,
        ):
            builder.build(tmp_path, skip_semantic=True, skip_clustering=True, repo_id="repo-b")

        assert write_vectors.call_args.kwargs["capability_dicts"] == []

    def test_build_full_pipeline_with_modules(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
        """完整流水线包含 Stage 4+5。"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass\n")

        clusters = [_make_cluster("m1"), _make_cluster("m2")]
        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
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

    def test_build_chroma_failure_records_error(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
        """ChromaDB 写入失败 → error 记录但不中断。"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass\n")

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
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
