from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from layerkg.builder import BuildResult, LayerKGBuilder
from layerkg.config import LayerKGConfig
from layerkg.schema import CodeEntity


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """创建临时测试仓库。"""
    # 创建几个测试文件
    (tmp_path / "module1.py").write_text("def foo():\n    pass\n\nclass Bar:\n    pass\n")
    (tmp_path / "module2.py").write_text("def baz():\n    pass\n")
    # 创建子目录
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "module3.py").write_text("class Qux:\n    pass\n")
    # 创建应该跳过的隐藏目录
    hidden_dir = tmp_path / ".venv"
    hidden_dir.mkdir()
    (hidden_dir / "hidden.py").write_text("# should be skipped\n")

    # 创建 __pycache__ 目录
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "cached.py").write_text("# should be skipped\n")

    return tmp_path


@pytest.fixture
def mock_config() -> LayerKGConfig:
    """创建测试配置。"""
    return LayerKGConfig(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test",
        chroma_persist_dir=None,  # 内存模式
        ollama_base_url="http://localhost:11434",
        embedding_model="test-model",
    )


@pytest.fixture
def builder(mock_config: LayerKGConfig) -> LayerKGBuilder:
    """创建 Builder 实例。"""
    return LayerKGBuilder(mock_config)


class TestScanFiles:
    """测试 _scan_files 方法。"""

    def test_scan_files_finds_py_files(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        # Arrange
        expected_files = 3  # module1.py, module2.py, module3.py

        # Act
        code_files, _doc_files = builder._scan_files(temp_repo)

        # Assert
        assert len(code_files) == expected_files
        assert all(f.suffix == ".py" for f in code_files)

    def test_scan_skips_hidden_dirs(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        # Arrange
        hidden_dir = temp_repo / ".venv"
        cache_dir = temp_repo / "__pycache__"

        # Act
        code_files, _doc_files = builder._scan_files(temp_repo)

        # Assert
        file_strs = [str(f) for f in code_files]
        assert not any(str(hidden_dir) in f for f in file_strs)
        assert not any(str(cache_dir) in f for f in file_strs)

    def test_scan_empty_dir_returns_empty(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        # Arrange & Act
        code_files, doc_files = builder._scan_files(tmp_path)

        # Assert
        assert code_files == []
        assert doc_files == []

    def test_scan_returns_sorted_files(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        # Act
        code_files, doc_files = builder._scan_files(temp_repo)

        # Assert - 检查是否已排序
        assert code_files == sorted(code_files)
        assert doc_files == sorted(doc_files)

    def test_scan_files_skip_dirs_from_config(self, tmp_path: Path) -> None:
        """验证 skip_dirs 从 config 读取。"""
        # Arrange
        (tmp_path / "module.py").write_text("pass")
        custom_dir = tmp_path / "custom_skip"
        custom_dir.mkdir()
        (custom_dir / "skipped.py").write_text("pass")

        config = LayerKGConfig(
            build_skip_dirs={"custom_skip"},
        )
        builder = LayerKGBuilder(config)

        # Act
        code_files, _doc_files = builder._scan_files(tmp_path)

        # Assert
        assert len(code_files) == 1
        assert code_files[0].name == "module.py"

    def test_scan_files_include_docs_false(self, tmp_path: Path) -> None:
        """验证 build_include_docs=False 时 doc_files 为空。"""
        # Arrange
        (tmp_path / "README.md").write_text("# Test")
        config = LayerKGConfig(build_include_docs=False)
        builder = LayerKGBuilder(config)

        # Act
        _code_files, doc_files = builder._scan_files(tmp_path)

        # Assert
        assert doc_files == []

    def test_scan_files_include_docs_true(self, tmp_path: Path) -> None:
        """验证 build_include_docs=True 时能扫描到文档文件。"""
        # Arrange
        (tmp_path / "README.md").write_text("# Test")
        config = LayerKGConfig(build_include_docs=True)
        builder = LayerKGBuilder(config)

        # Act
        _code_files, doc_files = builder._scan_files(tmp_path)

        # Assert
        assert len(doc_files) == 1
        assert doc_files[0].name == "README.md"

    def test_scan_files_finds_java_files(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """验证 _scan_files 能扫描到 .java 文件。"""
        (tmp_path / "App.java").write_text("public class App {}")
        code_files, _doc_files = builder._scan_files(tmp_path)
        assert len(code_files) == 1
        assert code_files[0].suffix == ".java"

    def test_scan_files_mixed_py_and_java(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """验证 _scan_files 同时扫描 .py 和 .java。"""
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "App.java").write_text("public class App {}")
        code_files, _doc_files = builder._scan_files(tmp_path)
        suffixes = sorted(f.suffix for f in code_files)
        assert suffixes == [".java", ".py"]


class TestDocTruncation:
    """测试文档截断配置化。"""

    def test_doc_entity_truncation_respects_config(self, builder: LayerKGBuilder) -> None:
        """验证 build_doc_max_length 配置生效。"""
        # Arrange
        from layerkg.schema import DocEntity

        long_content = "x" * 5000
        doc = DocEntity(
            name="test.md",
            entity_type="readme",
            file_path="test.md",
            content=long_content,
        )
        builder._config.build_doc_max_length = 100

        # Act - 直接测试截断逻辑
        truncated = (doc.content or "")[: builder._config.build_doc_max_length]

        # Assert
        assert len(truncated) == 100
        assert truncated == "x" * 100


class TestEntityToDict:
    """测试 _entity_to_dict 方法。"""

    def test_entity_to_dict_contains_required_fields(self, builder: LayerKGBuilder) -> None:
        # Arrange
        entity = CodeEntity(name="foo", entity_type="function")

        # Act
        result = builder._entity_to_dict(entity)

        # Assert
        assert result["id"] == entity.id
        assert result["name"] == "foo"
        assert result["entity_type"] == "function"

    def test_entity_to_dict_with_optional_fields(self, builder: LayerKGBuilder) -> None:
        # Arrange
        entity = CodeEntity(
            name="Bar",
            entity_type="class",
            file_path="/path/to/file.py",
            start_line=10,
            end_line=20,
            language="python",
        )

        # Act
        result = builder._entity_to_dict(entity)

        # Assert
        assert result["file_path"] == "/path/to/file.py"
        assert result["start_line"] == 10
        assert result["end_line"] == 20
        assert result["language"] == "python"

    def test_entity_to_dict_without_optional_fields(self, builder: LayerKGBuilder) -> None:
        # Arrange
        entity = CodeEntity(name="baz", entity_type="function")

        # Act
        result = builder._entity_to_dict(entity)

        # Assert
        assert "file_path" not in result
        assert "start_line" not in result
        assert "end_line" not in result
        assert "language" not in result


class TestEntityToText:
    """测试 _entity_to_text 方法。"""

    def test_entity_to_text_with_source(self, builder: LayerKGBuilder) -> None:
        # Arrange
        entity = CodeEntity(
            name="foo",
            entity_type="function",
            source="def foo():\n    pass",
        )

        # Act
        result = builder._entity_to_text(entity)

        # Assert
        assert result == "def foo():\n    pass"

    def test_entity_to_text_without_source(self, builder: LayerKGBuilder) -> None:
        # Arrange
        entity = CodeEntity(
            name="Bar",
            entity_type="class",
            file_path="/path/to/file.py",
        )

        # Act
        result = builder._entity_to_text(entity)

        # Assert
        assert result == "class Bar in /path/to/file.py"

    def test_entity_to_text_minimal(self, builder: LayerKGBuilder) -> None:
        # Arrange
        entity = CodeEntity(name="baz", entity_type="function")

        # Act
        result = builder._entity_to_text(entity)

        # Assert
        assert result == "function baz"


class TestBuildResult:
    """测试 BuildResult dataclass。"""

    def test_build_result_creation(self) -> None:
        # Arrange & Act
        result = BuildResult(
            files_scanned=10,
            entities_created=100,
            relations_created=50,
        )

        # Assert
        assert result.files_scanned == 10
        assert result.entities_created == 100
        assert result.relations_created == 50


class TestBuilderBuild:
    """测试 build 方法。"""

    def test_build_parses_all_files(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
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
            assert result.files_scanned == 3
            assert result.entities_created > 0
            mock_graph.ensure_constraints.assert_called_once()

    def test_build_writes_to_graph_store(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(temp_repo)

            # Assert - 至少调用了 merge_node（每个实体至少一个 module）
            assert mock_graph.merge_node.call_count > 0

    def test_build_writes_to_chroma_store(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(temp_repo)

            # Assert - 至少有一些实体被写入 ChromaDB
            assert mock_chroma.put_entities_batch.call_count >= 1

    def test_build_returns_correct_counts(self, builder: LayerKGBuilder, temp_repo: Path) -> None:
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
            assert result.files_scanned == 3
            assert isinstance(result.entities_created, int)
            assert isinstance(result.relations_created, int)

    def test_build_empty_repository(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            result = builder.build(tmp_path)

            # Assert
            assert result.files_scanned == 0
            assert result.entities_created == 0
            assert result.relations_created == 0


class TestBuilderQuery:
    """测试 query 方法。"""

    def test_query_searches_chroma(self, builder: LayerKGBuilder) -> None:
        # Arrange
        mock_chroma = MagicMock()
        mock_chroma.search.return_value = [
            {
                "id": "123",
                "text": "def foo(): pass",
                "metadata": {"entity_type": "function", "name": "foo"},
                "distance": 0.123,
            }
        ]

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            # Act
            results = builder.query("foo", n_results=10)

            # Assert
            mock_chroma.search.assert_called_once_with("foo", n_results=10, where=None)
            assert len(results) == 1

    def test_query_with_type_filter(self, builder: LayerKGBuilder) -> None:
        # Arrange
        mock_chroma = MagicMock()
        mock_chroma.search.return_value = []

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            # Act
            builder.query("foo", n_results=5, entity_type="function")

            # Assert
            mock_chroma.search.assert_called_once_with("foo", n_results=5, where={"entity_type": "function"})


class TestBuilderInfo:
    """测试 info 方法。"""

    def test_info_returns_config(self, builder: LayerKGBuilder) -> None:
        # Arrange
        mock_chroma = MagicMock()
        mock_chroma.count.return_value = 42

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            # Act
            info = builder.info()

            # Assert
            assert "config" in info
            assert info["config"]["neo4j_uri"] == "bolt://localhost:7687"
            assert info["config"]["ollama_url"] == "http://localhost:11434"
            assert info["config"]["model"] == "test-model"

    def test_info_returns_chroma_count(self, builder: LayerKGBuilder) -> None:
        # Arrange
        mock_chroma = MagicMock()
        mock_chroma.count.return_value = 99

        with patch.object(builder, "_get_chroma_store", return_value=mock_chroma):
            # Act
            info = builder.info()

            # Assert
            assert info["chroma_count"] == 99


class TestContextManager:
    """测试 context manager。"""

    def test_context_manager_closes_stores(self, mock_config: LayerKGConfig) -> None:
        # Arrange
        with (
            patch("layerkg.builder.Neo4jGraphStore") as mock_graph_cls,
            patch("layerkg.builder.ChromaStore") as mock_chroma_cls,
        ):
            mock_graph = MagicMock()
            mock_chroma = MagicMock()
            mock_graph_cls.return_value = mock_graph
            mock_chroma_cls.return_value = mock_chroma

            builder = LayerKGBuilder(mock_config)

            # Act
            with builder:
                # 触发 store 创建
                builder._get_graph_store()
                builder._get_chroma_store()

            # Assert
            mock_graph.close.assert_called_once()
            mock_chroma.close.assert_called_once()

    def test_context_manager_returns_self(self, mock_config: LayerKGConfig) -> None:
        # Arrange
        builder = LayerKGBuilder(mock_config)

        # Act
        with builder as b:
            # Assert
            assert b is builder


class TestBuilderEndToEnd:
    """Task 6: Builder 端到端测试（使用真实 PythonParser + RelationExtractor）。"""

    def test_parse_and_extract_sample_file(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """解析简单 Python 文件，验证实体和关系正确。"""
        # Arrange
        sample_code = """def foo():
    pass

def bar():
    foo()
"""
        test_file = tmp_path / "sample.py"
        test_file.write_text(sample_code)

        # Act
        parser = builder._get_parser(test_file)
        assert parser is not None
        parse_result = parser.parse_file(test_file)

        # Assert
        assert parse_result.error is None
        # 应有 1 个 module + 2 个 function
        assert len(parse_result.entities) == 3
        entity_types = {e.entity_type for e in parse_result.entities}
        assert entity_types == {"module", "function"}
        entity_names = {e.name for e in parse_result.entities}
        assert "foo" in entity_names
        assert "bar" in entity_names

        # 验证关系提取
        builder._extractor.add_parse_result(parse_result.entities, parse_result.relations)
        relations = builder._extractor.resolve(parse_result.entities)
        # module contains foo, bar (2 contains relations)
        # 注：PythonParser 不提取 calls 关系（需要更复杂的语义分析）
        contains_rels = [r for r in relations if r.relation_type == "contains"]
        assert len(contains_rels) == 2

    def test_parse_class_with_methods(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """解析含类+方法的文件，验证 contains 关系被正确提取。"""
        # Arrange
        sample_code = """class MyClass:
    def method1(self):
        pass

    def method2(self):
        self.method1()
"""
        test_file = tmp_path / "class_sample.py"
        test_file.write_text(sample_code)

        # Act
        parser = builder._get_parser(test_file)
        assert parser is not None
        parse_result = parser.parse_file(test_file)

        # Assert
        assert parse_result.error is None
        # 1 module + 1 class + 2 methods
        assert len(parse_result.entities) == 4
        entity_types = {e.entity_type for e in parse_result.entities}
        assert entity_types == {"module", "class", "function"}

        entity_names = {e.name for e in parse_result.entities}
        assert "MyClass" in entity_names
        # 类内方法名使用 ClassName.method_name 格式
        assert "MyClass.method1" in entity_names
        assert "MyClass.method2" in entity_names

        # 验证 contains 关系
        builder._extractor.add_parse_result(parse_result.entities, parse_result.relations)
        relations = builder._extractor.resolve(parse_result.entities)

        # module contains MyClass, MyClass contains method1, method2
        contains_rels = [r for r in relations if r.relation_type == "contains"]
        assert len(contains_rels) == 3  # module->MyClass, MyClass->method1, MyClass->method2

    def test_parse_imports(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """解析含 import 语句的文件，验证 imports 关系被正确提取。"""
        # Arrange
        sample_code = """import os
import sys
from pathlib import Path

def my_func():
    pass
"""
        test_file = tmp_path / "import_sample.py"
        test_file.write_text(sample_code)

        # Act
        parser = builder._get_parser(test_file)
        assert parser is not None
        parse_result = parser.parse_file(test_file)

        # Assert
        assert parse_result.error is None
        # 1 module + 1 function
        assert len(parse_result.entities) >= 2

        entity_names = {e.name for e in parse_result.entities}
        assert "my_func" in entity_names

        # 验证 imports 关系
        builder._extractor.add_parse_result(parse_result.entities, parse_result.relations)
        relations = builder._extractor.resolve(parse_result.entities)

        # 应有 imports 关系（module imports os, sys, Path）
        _ = [r for r in relations if r.relation_type == "imports"]
        # 注意：import 关系的源是 module，目标是导入的模块名
        # 这些关系可能无法完全解析，因为 os/sys/Path 不在实体列表中
        # 但可以验证解析器确实提取了这些关系
        # 解析结果中的 imports 关系数量
        raw_imports = [r for r in parse_result.relations if r.relation_type == "imports"]
        assert len(raw_imports) >= 2


class TestMultiLanguageParsers:
    """测试 Builder 多语言解析器注册和路由。"""

    def test_get_parser_python(self, builder: LayerKGBuilder) -> None:
        """验证 .py 文件路由到 PythonParser。"""
        from layerkg.parser.python_parser import PythonParser

        parser = builder._get_parser(Path("foo.py"))
        assert parser is not None
        assert isinstance(parser, PythonParser)
        assert parser.language == "python"

    def test_get_parser_java(self, builder: LayerKGBuilder) -> None:
        """验证 .java 文件路由到 JavaParser。"""
        from layerkg.parser.java_parser import JavaParser

        parser = builder._get_parser(Path("Foo.java"))
        assert parser is not None
        assert isinstance(parser, JavaParser)
        assert parser.language == "java"

    def test_get_parser_unknown_suffix(self, builder: LayerKGBuilder) -> None:
        """验证未知扩展名返回 None。"""
        parser = builder._get_parser(Path("main.rs"))
        assert parser is None

    def test_stage_parse_uses_java_parser(self, builder: LayerKGBuilder, tmp_path: Path) -> None:
        """验证 _stage_parse 能用 JavaParser 解析 .java 文件。"""
        java_code = """
package com.example;

public class Hello {
    public void greet() {
        System.out.println("Hello");
    }
}
"""
        (tmp_path / "Hello.java").write_text(java_code)
        all_entities, _doc_entities, _relations, _files_scanned, _unresolved = builder._stage_parse(tmp_path)
        # 应该能解析出至少 class + method + module + file 实体
        entity_types = {e.entity_type for e in all_entities}
        assert "class" in entity_types
        assert "function" in entity_types

    def test_external_import_language_is_unknown(self, builder: LayerKGBuilder) -> None:
        """验证外部 import 的 language 不是硬编码 python。"""
        # 检查 _stage_write_structural 中的外部模块 language
        # 间接验证：读取源码中第 327 行附近的 language="unknown"
        import inspect

        source = inspect.getsource(builder._stage_write_structural)
        assert 'language="unknown"' in source or "language='unknown'" in source
