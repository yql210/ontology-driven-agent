from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from layerkg.aligner import NO_MATCH, AlignResult, ConceptAligner
from layerkg.builder import LayerKGBuilder
from layerkg.config import LayerKGConfig
from layerkg.extractor.semantic import SemanticExtractor, SemanticRelation


@pytest.fixture
def config() -> LayerKGConfig:
    """默认配置测试 fixture。"""
    return LayerKGConfig()


@pytest.fixture
def builder(config: LayerKGConfig) -> LayerKGBuilder:
    """Builder 测试 fixture（mock 掉 Neo4j/ChromaDB）。"""
    with patch("layerkg.builder.Neo4jGraphStore"), patch("layerkg.builder.ChromaStore"):
        return LayerKGBuilder(config)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """仓库根目录 fixture。"""
    return tmp_path


@pytest.fixture
def mock_entity_index() -> dict[tuple[str, str, str], list[str]]:
    """模拟实体索引。"""
    return {
        ("function", "", "parse_file"): ["code-1"],
        ("function", "", "extract"): ["code-3"],
        ("class", "", "Parser"): ["code-2"],
        ("function", "src/parser.py", "extract"): ["code-3"],
    }


class TestCheckOllama:
    """_check_ollama 健康检查测试。"""

    def test_check_ollama_available(self, builder: LayerKGBuilder) -> None:
        """Ollama 返回 200 → True。"""
        with patch("httpx.get", return_value=MagicMock(status_code=200)):
            assert builder._check_ollama() is True

    def test_check_ollama_unavailable(self, builder: LayerKGBuilder) -> None:
        """Ollama 连接失败 → False。"""
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            assert builder._check_ollama() is False

    def test_check_ollama_timeout(self, builder: LayerKGBuilder) -> None:
        """Ollama 超时 → False。"""
        with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
            assert builder._check_ollama() is False


class TestInitSemanticExtractor:
    """_init_semantic_extractor lazy init 测试。"""

    def test_init_semantic_extractor_lazy(self, builder: LayerKGBuilder) -> None:
        """首次调用创建实例，再次调用返回同一实例。"""
        ext1 = builder._init_semantic_extractor()
        ext2 = builder._init_semantic_extractor()
        assert ext1 is ext2
        assert isinstance(ext1, SemanticExtractor)

    def test_init_semantic_extractor_uses_config(self, builder: LayerKGBuilder) -> None:
        """使用 config 中的 ollama_base_url 和 llm_model。"""
        ext = builder._init_semantic_extractor()
        assert ext._ollama_url == builder._config.ollama_base_url
        assert ext._model == builder._config.llm_model


class TestInitConceptAligner:
    """_init_concept_aligner lazy init 测试。"""

    def test_init_concept_aligner_lazy(self, builder: LayerKGBuilder) -> None:
        """首次调用创建实例，再次调用返回同一实例。"""
        aligner1 = builder._init_concept_aligner()
        aligner2 = builder._init_concept_aligner()
        assert aligner1 is aligner2
        assert isinstance(aligner1, ConceptAligner)

    def test_init_concept_aligner_uses_chroma(self, builder: LayerKGBuilder) -> None:
        """ConceptAligner 使用 builder 的 ChromaStore。"""
        aligner = builder._init_concept_aligner()
        assert aligner._chroma_store is builder._get_chroma_store()


class TestProcessSemanticRelations:
    """_process_semantic_relations 核心方法测试。"""

    def test_process_semantic_new_concept(
        self, builder: LayerKGBuilder, repo_root: Path, mock_entity_index: dict
    ) -> None:
        """路径 A：NO_MATCH → 创建新 ConceptEntity。"""
        relations = [
            SemanticRelation(
                source_name="parse_file",
                source_type="function",
                target_name="Parser",
                target_type="business_concept",
                relation_type="derived_from",
            )
        ]

        with (
            patch.object(builder._init_concept_aligner(), "align_batch", return_value=[NO_MATCH]),
            patch.object(builder._get_chroma_store(), "put_entities_batch"),
        ):
            new_concepts, _resolved, skipped = builder._process_semantic_relations(
                relations, mock_entity_index, repo_root
            )

        assert len(new_concepts) == 1
        assert new_concepts[0].name == "Parser"
        assert new_concepts[0].entity_type == "business_concept"
        assert skipped == 0

    def test_process_semantic_existing_concept(
        self, builder: LayerKGBuilder, repo_root: Path, mock_entity_index: dict
    ) -> None:
        """路径 A：exact match → 复用已有 concept_id，不创建新概念。"""
        existing_id = "existing-concept-123"
        relations = [
            SemanticRelation(
                source_name="parse_file",
                source_type="function",
                target_name="Parser",
                target_type="business_concept",
                relation_type="derived_from",
            )
        ]

        align_result = AlignResult(
            concept_id=existing_id,
            concept_name="Parser",
            match_type="exact",
            confidence=1.0,
            aliases=[],
        )

        with patch.object(builder._init_concept_aligner(), "align_batch", return_value=[align_result]):
            new_concepts, resolved, skipped = builder._process_semantic_relations(
                relations, mock_entity_index, repo_root
            )

        assert len(new_concepts) == 0
        assert len(resolved) == 1
        assert resolved[0].target_id == existing_id
        assert skipped == 0

    def test_process_semantic_concept_dedup(
        self, builder: LayerKGBuilder, repo_root: Path, mock_entity_index: dict
    ) -> None:
        """路径 A：多个 SemanticRelation 指向同一 target_name → 只创建一个 ConceptEntity。"""
        relations = [
            SemanticRelation(
                source_name="parse_file",
                source_type="function",
                target_name="Parser",
                target_type="business_concept",
                relation_type="derived_from",
            ),
            SemanticRelation(
                source_name="extract",
                source_type="function",
                target_name="Parser",
                target_type="business_concept",
                relation_type="derived_from",
            ),
            SemanticRelation(
                source_name="parse_file",
                source_type="function",
                target_name="Parser",
                target_type="business_concept",
                relation_type="semantic_impact",
            ),
        ]

        with (
            patch.object(builder._init_concept_aligner(), "align_batch", return_value=[NO_MATCH]),
            patch.object(builder._get_chroma_store(), "put_entities_batch"),
        ):
            new_concepts, resolved, _skipped = builder._process_semantic_relations(
                relations, mock_entity_index, repo_root
            )

        assert len(new_concepts) == 1
        assert len(resolved) == 3
        # 验证所有关系的 target_id 相同
        target_ids = {rel.target_id for rel in resolved}
        assert len(target_ids) == 1

    def test_process_semantic_no_direct_chroma_write(
        self, builder: LayerKGBuilder, repo_root: Path, mock_entity_index: dict
    ) -> None:
        """_process_semantic_relations 不再直接写入 ChromaDB（由 Stage 5 _write_all_vectors 统一处理）。"""
        relations = [
            SemanticRelation(
                source_name="parse_file",
                source_type="function",
                target_name="Parser",
                target_type="business_concept",
                relation_type="derived_from",
            )
        ]

        mock_chroma = builder._get_chroma_store()
        with (
            patch.object(builder._init_concept_aligner(), "align_batch", return_value=[NO_MATCH]),
            patch.object(mock_chroma, "put_entities_batch") as mock_put,
        ):
            new_concepts, _, _ = builder._process_semantic_relations(relations, mock_entity_index, repo_root)

        assert len(new_concepts) == 1
        # 验证不再直接调用 ChromaDB 写入
        mock_put.assert_not_called()

    def test_process_semantic_code_target(
        self, builder: LayerKGBuilder, repo_root: Path, mock_entity_index: dict
    ) -> None:
        """路径 B：CodeEntity → CodeEntity 的 semantic_impact。"""
        relations = [
            SemanticRelation(
                source_name="parse_file",
                source_type="function",
                target_name="Parser",
                target_type="class",
                relation_type="semantic_impact",
            )
        ]

        new_concepts, resolved, skipped = builder._process_semantic_relations(relations, mock_entity_index, repo_root)

        assert len(new_concepts) == 0
        assert len(resolved) == 1
        assert resolved[0].source_id == "code-1"
        assert resolved[0].target_id == "code-2"
        assert resolved[0].relation_type == "semantic_impact"
        assert skipped == 0

    def test_process_semantic_code_target_missing(
        self, builder: LayerKGBuilder, repo_root: Path, mock_entity_index: dict
    ) -> None:
        """路径 B：target_id 解析失败 → 跳过。"""
        relations = [
            SemanticRelation(
                source_name="parse_file",
                source_type="function",
                target_name="NonExistent",
                target_type="class",
                relation_type="semantic_impact",
            )
        ]

        new_concepts, resolved, skipped = builder._process_semantic_relations(relations, mock_entity_index, repo_root)

        assert len(new_concepts) == 0
        assert len(resolved) == 0
        assert skipped == 1

    def test_process_semantic_resource_target_skipped(
        self, builder: LayerKGBuilder, repo_root: Path, mock_entity_index: dict
    ) -> None:
        """target_type 为非代码/概念类型 → 跳过（如 wiki）。"""
        relations = [
            SemanticRelation(
                source_name="parse_file",
                source_type="function",
                target_name="ArchitectureDoc",
                target_type="wiki",
                relation_type="describes",
            )
        ]

        new_concepts, resolved, skipped = builder._process_semantic_relations(relations, mock_entity_index, repo_root)

        # wiki 不是 _CODE_ENTITY_TYPES 也不是 _CONCEPT_ENTITY_TYPES，所以跳过
        assert len(new_concepts) == 0
        assert len(resolved) == 0
        assert skipped == 1

    def test_process_semantic_mixed_paths(
        self, builder: LayerKGBuilder, repo_root: Path, mock_entity_index: dict
    ) -> None:
        """混合路径：同时有概念目标和代码目标。"""
        relations = [
            # 路径 A：概念目标
            SemanticRelation(
                source_name="parse_file",
                source_type="function",
                target_name="Parser",
                target_type="business_concept",
                relation_type="derived_from",
            ),
            SemanticRelation(
                source_name="extract",
                source_type="function",
                target_name="Extractor",
                target_type="design_pattern",
                relation_type="derived_from",
            ),
            # 路径 B：代码目标
            SemanticRelation(
                source_name="extract",
                source_type="function",
                target_name="parse_file",
                target_type="function",
                relation_type="semantic_impact",
            ),
        ]

        mock_align_result = AlignResult(
            concept_id="existing-parser",
            concept_name="Parser",
            match_type="exact",
            confidence=1.0,
            aliases=[],
        )

        with (
            patch.object(builder._init_concept_aligner(), "align_batch", return_value=[mock_align_result, NO_MATCH]),
            patch.object(builder._get_chroma_store(), "put_entities_batch"),
        ):
            new_concepts, resolved, skipped = builder._process_semantic_relations(
                relations, mock_entity_index, repo_root
            )

        # 验证新概念（Extractor 没匹配到）
        assert len(new_concepts) == 1
        assert new_concepts[0].name == "Extractor"

        # 验证 resolved 包含 3 条关系
        assert len(resolved) == 3

        # 验证跳过数量
        assert skipped == 0


class TestClose:
    """close() 资源清理测试。"""

    def test_close_closes_semantic_extractor(self, builder: LayerKGBuilder) -> None:
        """close() 应关闭 SemanticExtractor 及其 httpx.Client。"""
        # 初始化 semantic_extractor
        extractor = builder._init_semantic_extractor()
        mock_close = MagicMock()
        extractor.close = mock_close  # type: ignore[method-assign]

        # 调用 close()
        builder.close()

        # 验证 extractor.close() 被调用
        mock_close.assert_called_once()

    def test_close_without_semantic_extractor(self, builder: LayerKGBuilder) -> None:
        """close() 在未初始化 semantic_extractor 时不报错。"""
        # 不初始化 semantic_extractor
        assert builder._semantic_extractor is None

        # 不应抛出异常
        builder.close()


class TestBuildSemanticPipeline:
    """build() 方法中 Stage 3a 集成测试。"""

    def test_build_semantic_pipeline(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """Ollama 可用 → 完整语义流水线。"""
        # 创建测试 .py 文件
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        # mock extractor 和 aligner
        # 注意：source_file_path 需要匹配 entity_index 中的键
        mock_extraction = MagicMock()
        mock_extraction.relations = [
            SemanticRelation(
                source_name="foo",
                source_type="function",
                target_name="BusinessConcept",
                target_type="business_concept",
                relation_type="derived_from",
                source_file_path="test.py",  # 匹配 _normalize_path 后的路径
            )
        ]

        # mock Neo4j 和 ChromaDB
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        # Call tracker for merge_relation
        relation_call_count = [0]

        def mock_merge_relation(*args, **kwargs):
            relation_call_count[0] += 1
            # 第一次调用是结构关系，正常返回
            # 第二次调用是语义关系，也正常返回
            return None

        mock_graph.merge_relation.side_effect = mock_merge_relation

        with (
            patch.object(builder, "_check_ollama", return_value=True),
            patch.object(builder, "_init_semantic_extractor") as mock_init_ext,
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            mock_ext = MagicMock()
            mock_ext.extract.return_value = mock_extraction
            mock_init_ext.return_value = mock_ext

            with patch.object(builder._init_concept_aligner(), "align_batch", return_value=[NO_MATCH]):
                result = builder.build(tmp_path)

        # 验证语义处理结果
        assert result.concepts_created == 1
        # merge_relation 被调用了 2 次：1 次结构关系 + 1 次语义关系
        assert relation_call_count[0] >= 2
        assert result.skipped_semantic is False
        assert result.entities_created > 0

    def test_build_semantic_skipped(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """Ollama 不可用 → skipped_semantic=True。"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_check_ollama", return_value=False),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            result = builder.build(tmp_path)

        assert result.skipped_semantic is True
        assert result.concepts_created == 0
        assert result.semantic_relations_created == 0

    def test_build_semantic_error(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """SemanticExtractor 异常 → error 记录 + 不中断。"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_check_ollama", return_value=True),
            patch.object(builder, "_init_semantic_extractor") as mock_init_ext,
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            mock_ext = MagicMock()
            mock_ext.extract.side_effect = Exception("LLM error")
            mock_init_ext.return_value = mock_ext

            result = builder.build(tmp_path)

        # 验证错误记录
        assert len(result.errors) == 1
        assert "LLM error" in result.errors[0] or "Semantic extraction error" in result.errors[0]
        # 验证结构流水线仍完成
        assert result.entities_created > 0

    def test_build_semantic_neo4j_write_failure(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """Neo4j 写入语义关系失败 → error 记录。"""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass")

        mock_extraction = MagicMock()
        mock_extraction.relations = [
            SemanticRelation(
                source_name="foo",
                source_type="function",
                target_name="BusinessConcept",
                target_type="business_concept",
                relation_type="derived_from",
                source_file_path="test.py",  # 匹配 _normalize_path 后的路径
            )
        ]

        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        # 让 merge_relation 在写入语义关系时失败
        call_count = [0]

        def mock_merge_relation(*args, **kwargs):
            call_count[0] += 1
            # 第一次调用是结构关系（CONTAINS），正常返回
            if call_count[0] == 1:
                return None
            # 后续调用是语义关系，抛异常
            raise Exception("Neo4j write failed")

        mock_graph.merge_relation.side_effect = mock_merge_relation

        with (
            patch.object(builder, "_check_ollama", return_value=True),
            patch.object(builder, "_init_semantic_extractor") as mock_init_ext,
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            mock_ext = MagicMock()
            mock_ext.extract.return_value = mock_extraction
            mock_init_ext.return_value = mock_ext

            with patch.object(builder._init_concept_aligner(), "align_batch", return_value=[NO_MATCH]):
                result = builder.build(tmp_path)

        # 验证错误记录
        assert len(result.errors) > 0
        assert any("Neo4j" in err for err in result.errors)
