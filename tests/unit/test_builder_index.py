from __future__ import annotations

import logging
from pathlib import Path

import pytest

from layerkg.builder import LayerKGBuilder
from layerkg.config import LayerKGConfig
from layerkg.extractor.semantic import SemanticRelation
from layerkg.schema import CodeEntity, ConceptEntity, DocEntity


class TestNormalizePath:
    """测试 _normalize_path 方法。"""

    def test_relative_path_unchanged(self, tmp_path: Path) -> None:
        """相对路径保持原样。"""
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)
        result = builder._normalize_path("src/foo.py", tmp_path)
        assert result == "src/foo.py"

    def test_absolute_path_to_relative(self, tmp_path: Path) -> None:
        """绝对路径转为相对路径。"""
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)
        file_path = str(tmp_path / "src" / "foo.py")
        result = builder._normalize_path(file_path, tmp_path)
        assert result == "src/foo.py"

    def test_none_returns_empty(self, tmp_path: Path) -> None:
        """None 返回空字符串。"""
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)
        result = builder._normalize_path(None, tmp_path)
        assert result == ""


class TestBuildEntityIndex:
    """测试 _build_entity_index 方法。"""

    def test_basic_three_entities(self, tmp_path: Path) -> None:
        """3 个不同实体 → 3 个索引键。"""
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)

        entities = [
            CodeEntity(name="foo", entity_type="function"),
            CodeEntity(name="bar", entity_type="function"),
            CodeEntity(name="baz", entity_type="class"),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        assert len(index) == 3
        assert all(len(ids) == 1 for ids in index.values())

    def test_same_name_different_file(self, tmp_path: Path) -> None:
        """同名不同文件 → 2 个不同的三元组键（因为 file_path 不同）。"""
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)

        entities = [
            CodeEntity(name="foo", entity_type="function", file_path="src/a.py"),
            CodeEntity(name="foo", entity_type="function", file_path="src/b.py"),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        assert len(index) == 2
        assert all(len(ids) == 1 for ids in index.values())

    def test_same_name_same_file(self, tmp_path: Path) -> None:
        """同文件同名函数 → 同一个三元组键，值列表长度 2。"""
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)

        entities = [
            CodeEntity(name="foo", entity_type="function", file_path="src/a.py"),
            CodeEntity(name="foo", entity_type="function", file_path="src/a.py"),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        assert len(index) == 1
        _key, ids = next(iter(index.items()))
        assert len(ids) == 2


class TestResolveSemanticNames:
    """测试 _resolve_semantic_names 方法。"""

    def test_success(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """name 匹配 → 正确创建 Relation。"""
        caplog.set_level(logging.WARNING)
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)
        builder._repo_root = tmp_path

        entities = [
            CodeEntity(name="foo", entity_type="function", file_path="src/a.py"),
            ConceptEntity(name="Singleton", entity_type="design_pattern"),
        ]

        relations = [
            SemanticRelation(
                source_name="foo",
                source_type="function",
                target_name="Singleton",
                target_type="design_pattern",
                relation_type="derived_from",
                source_file_path="src/a.py",
            )
        ]

        index = builder._build_entity_index(entities, tmp_path)
        resolved, skipped = builder._resolve_semantic_names(relations, index)

        assert len(resolved) == 1
        assert skipped == 0
        assert resolved[0].source_id == entities[0].id
        assert resolved[0].target_id == entities[1].id
        assert resolved[0].relation_type == "derived_from"

    def test_missing_target_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """target name 找不到 → 跳过，skipped=1。"""
        caplog.set_level(logging.WARNING)
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)
        builder._repo_root = tmp_path

        entities = [
            CodeEntity(name="foo", entity_type="function", file_path="src/a.py"),
        ]

        relations = [
            SemanticRelation(
                source_name="foo",
                source_type="function",
                target_name="nonexistent",
                target_type="class",
                relation_type="semantic_impact",
                source_file_path="src/a.py",
            )
        ]

        index = builder._build_entity_index(entities, tmp_path)
        resolved, skipped = builder._resolve_semantic_names(relations, index)

        assert len(resolved) == 0
        assert skipped == 1
        assert "Cannot resolve semantic relation" in caplog.text

    def test_concept_entity_no_file_path(self, tmp_path: Path) -> None:
        """ConceptEntity 无 file_path → 索引键 file_path 部分为空。"""
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)

        entities = [
            ConceptEntity(name="Singleton", entity_type="design_pattern"),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        # (entity_type, "", name)
        expected_key = ("design_pattern", "", "Singleton")
        assert expected_key in index

    def test_doc_entity_with_file_path(self, tmp_path: Path) -> None:
        """DocEntity 有 file_path → 正确构建索引。"""
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)

        entities = [
            DocEntity(name="README", entity_type="readme", file_path="README.md"),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        expected_key = ("readme", "README.md", "README")
        assert expected_key in index

    def test_missing_source_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """source name 找不到 → 跳过，skipped=1。"""
        caplog.set_level(logging.WARNING)
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)
        builder._repo_root = tmp_path

        entities = [
            CodeEntity(name="foo", entity_type="function", file_path="src/a.py"),
        ]
        relations = [
            SemanticRelation(
                source_name="nonexistent",
                source_type="function",
                target_name="foo",
                target_type="function",
                relation_type="semantic_impact",
                source_file_path="src/a.py",
            ),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        resolved, skipped = builder._resolve_semantic_names(relations, index)

        assert len(resolved) == 0
        assert skipped == 1

    def test_source_path_mismatch_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """source name 存在但 file_path 不匹配 → 跳过。"""
        caplog.set_level(logging.WARNING)
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)
        builder._repo_root = tmp_path

        entities = [
            CodeEntity(name="foo", entity_type="function", file_path="src/a.py"),
        ]
        relations = [
            SemanticRelation(
                source_name="foo",
                source_type="function",
                target_name="bar",
                target_type="function",
                relation_type="semantic_impact",
                source_file_path="src/WRONG.py",  # 路径不匹配
            ),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        resolved, skipped = builder._resolve_semantic_names(relations, index)

        assert len(resolved) == 0
        assert skipped == 1

    def test_empty_relations_returns_empty(self, tmp_path: Path) -> None:
        """空 relations 列表 → ([], 0)。"""
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)
        builder._repo_root = tmp_path

        index = builder._build_entity_index([], tmp_path)
        resolved, skipped = builder._resolve_semantic_names([], index)

        assert resolved == []
        assert skipped == 0

    def test_empty_index_all_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """空 index → 所有关系都 skipped。"""
        caplog.set_level(logging.WARNING)
        config = LayerKGConfig()
        builder = LayerKGBuilder(config)
        builder._repo_root = tmp_path

        relations = [
            SemanticRelation(
                source_name="foo",
                source_type="function",
                target_name="bar",
                target_type="function",
                relation_type="semantic_impact",
                source_file_path="src/a.py",
            ),
        ]

        index = builder._build_entity_index([], tmp_path)
        resolved, skipped = builder._resolve_semantic_names(relations, index)

        assert len(resolved) == 0
        assert skipped == 1


class TestBuildResult:
    """测试 BuildResult 扩展。"""

    def test_to_dict(self) -> None:
        """to_dict 返回所有字段。"""
        from layerkg.builder import BuildResult

        result = BuildResult(
            files_scanned=10,
            entities_created=100,
            relations_created=50,
            concepts_created=5,
            semantic_relations_created=20,
            modules_created=3,
            doc_entities_created=15,
            skipped_semantic=True,
            elapsed_ms=1234.5,
            errors=["error1", "error2"],
        )

        d = result.to_dict()
        assert d["files_scanned"] == 10
        assert d["entities_created"] == 100
        assert d["relations_created"] == 50
        assert d["concepts_created"] == 5
        assert d["semantic_relations_created"] == 20
        assert d["modules_created"] == 3
        assert d["doc_entities_created"] == 15
        assert d["skipped_semantic"] is True
        assert d["elapsed_ms"] == 1234.5
        assert d["errors"] == ["error1", "error2"]

    def test_defaults(self) -> None:
        """测试新字段的默认值。"""
        from layerkg.builder import BuildResult

        result = BuildResult(
            files_scanned=1,
            entities_created=2,
            relations_created=3,
        )

        assert result.concepts_created == 0
        assert result.semantic_relations_created == 0
        assert result.modules_created == 0
        assert result.doc_entities_created == 0
        assert result.skipped_semantic is False
        assert result.elapsed_ms == 0.0
        assert result.errors == []
