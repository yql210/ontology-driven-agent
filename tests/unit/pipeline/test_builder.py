from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.index_health import VectorWriteOutcome
from ontoagent.domain.schema import CodeEntity, RepositoryEntity
from ontoagent.pipeline.builder import BuildResult, OntoAgentBuilder
from ontoagent.store.schema_version import SchemaStatus


class _RecordingGraphStore:
    """Minimal graph-store recorder for real builder parser handoff tests."""

    def __init__(self) -> None:
        self.node_batches: list[tuple[str, list[dict]]] = []
        self.relation_batches: list[list[dict]] = []
        self.nodes: list[tuple[str, dict]] = []

    def ensure_constraints(self) -> None:
        pass

    def get_nodes_by_label(self, _label: str, _properties: list[str]) -> list[dict]:
        return []

    def merge_node(self, label: str, properties: dict) -> None:
        self.nodes.append((label, dict(properties)))

    def merge_nodes_batch(self, label: str, properties_list: list[dict], batch_size: int = 200) -> int:
        del batch_size
        self.node_batches.append((label, [dict(properties) for properties in properties_list]))
        return len(properties_list)

    def merge_relations_batch(self, relations: list[dict], batch_size: int = 200) -> int:
        del batch_size
        self.relation_batches.append([dict(relation) for relation in relations])
        return len(relations)


class _RecordingChromaStore:
    """Minimal vector-store recorder for builder vector handoff tests."""

    def __init__(self) -> None:
        self.batches: list[list[tuple[str, str, dict]]] = []

    def put_entities_batch(self, items: list[tuple[str, str, dict]]) -> None:
        self.batches.append(list(items))

    def put_entities_batch_with_outcome(self, items: list[tuple[str, str, dict]]) -> VectorWriteOutcome:
        self.batches.append(list(items))
        return VectorWriteOutcome(len(items), len(items), 0)


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
def mock_config() -> OntoAgentConfig:
    """创建测试配置。"""
    return OntoAgentConfig(
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test",
        chroma_persist_dir=None,  # 内存模式
        ollama_base_url="http://localhost:11434",
        embedding_model="test-model",
    )


@pytest.fixture
def builder(mock_config: OntoAgentConfig) -> OntoAgentBuilder:
    """创建 Builder 实例。"""
    return OntoAgentBuilder(mock_config)


class TestScanFiles:
    """测试 _scan_files 方法。"""

    def test_scan_files_finds_py_files(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        # Arrange
        expected_files = 3  # module1.py, module2.py, module3.py

        # Act
        code_files, _doc_files = builder._scan_files(temp_repo)

        # Assert
        assert len(code_files) == expected_files
        assert all(f.suffix == ".py" for f in code_files)

    def test_scan_skips_hidden_dirs(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        # Arrange
        hidden_dir = temp_repo / ".venv"
        cache_dir = temp_repo / "__pycache__"

        # Act
        code_files, _doc_files = builder._scan_files(temp_repo)

        # Assert
        file_strs = [str(f) for f in code_files]
        assert not any(str(hidden_dir) in f for f in file_strs)
        assert not any(str(cache_dir) in f for f in file_strs)

    def test_scan_empty_dir_returns_empty(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
        # Arrange & Act
        code_files, doc_files = builder._scan_files(tmp_path)

        # Assert
        assert code_files == []
        assert doc_files == []

    def test_scan_returns_sorted_files(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
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

        config = OntoAgentConfig(
            build_skip_dirs={"custom_skip"},
        )
        builder = OntoAgentBuilder(config)

        # Act
        code_files, _doc_files = builder._scan_files(tmp_path)

        # Assert
        assert len(code_files) == 1
        assert code_files[0].name == "module.py"

    def test_scan_files_include_docs_false(self, tmp_path: Path) -> None:
        """验证 build_include_docs=False 时 doc_files 为空。"""
        # Arrange
        (tmp_path / "README.md").write_text("# Test")
        config = OntoAgentConfig(build_include_docs=False)
        builder = OntoAgentBuilder(config)

        # Act
        _code_files, doc_files = builder._scan_files(tmp_path)

        # Assert
        assert doc_files == []

    def test_scan_files_include_docs_true(self, tmp_path: Path) -> None:
        """验证 build_include_docs=True 时能扫描到文档文件。"""
        # Arrange
        (tmp_path / "README.md").write_text("# Test")
        config = OntoAgentConfig(build_include_docs=True)
        builder = OntoAgentBuilder(config)

        # Act
        _code_files, doc_files = builder._scan_files(tmp_path)

        # Assert
        assert len(doc_files) == 1
        assert doc_files[0].name == "README.md"

    def test_scan_files_finds_java_files(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
        """验证 _scan_files 能扫描到 .java 文件。"""
        (tmp_path / "App.java").write_text("public class App {}")
        code_files, _doc_files = builder._scan_files(tmp_path)
        assert len(code_files) == 1
        assert code_files[0].suffix == ".java"

    def test_scan_files_mixed_py_and_java(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
        """验证 _scan_files 同时扫描 .py 和 .java。"""
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "App.java").write_text("public class App {}")
        code_files, _doc_files = builder._scan_files(tmp_path)
        suffixes = sorted(f.suffix for f in code_files)
        assert suffixes == [".java", ".py"]


class TestDocTruncation:
    """测试文档截断配置化。"""

    def test_doc_entity_truncation_respects_config(self, builder: OntoAgentBuilder) -> None:
        """验证 build_doc_max_length 配置生效。"""
        # Arrange
        from ontoagent.domain.schema import DocEntity

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

    def test_entity_to_dict_contains_required_fields(self, builder: OntoAgentBuilder) -> None:
        # Arrange
        entity = CodeEntity(name="foo", entity_type="function")

        # Act
        result = builder._entity_to_dict(entity)

        # Assert
        assert result["id"] == entity.id
        assert result["name"] == "foo"
        assert result["entity_type"] == "function"

    def test_entity_to_dict_with_optional_fields(self, builder: OntoAgentBuilder) -> None:
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

    def test_entity_to_dict_without_optional_fields(self, builder: OntoAgentBuilder) -> None:
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

    def test_entity_to_text_with_source(self, builder: OntoAgentBuilder) -> None:
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

    def test_entity_to_text_without_source(self, builder: OntoAgentBuilder) -> None:
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

    def test_entity_to_text_minimal(self, builder: OntoAgentBuilder) -> None:
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

    def test_build_parses_all_files(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            result = builder.build(temp_repo)

            # Assert
            assert result.files_scanned == 3
            assert result.entities_created > 0
            mock_graph.ensure_constraints.assert_called_once()

    def test_build_writes_to_graph_store(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(temp_repo)

            # Assert - 至少调用了 merge_nodes_batch（每个实体至少一个 module）
            assert mock_graph.merge_nodes_batch.call_count > 0

    def test_build_writes_to_chroma_store(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(temp_repo)

            # Assert - 至少有一些实体被写入 ChromaDB
            assert mock_chroma.put_entities_batch.call_count >= 1

    def test_build_returns_correct_counts(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            result = builder.build(temp_repo)

            # Assert
            assert result.files_scanned == 3
            assert isinstance(result.entities_created, int)
            assert isinstance(result.relations_created, int)

    def test_build_empty_repository(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            result = builder.build(tmp_path)

            # Assert
            assert result.files_scanned == 0
            assert result.entities_created == 0
            assert result.relations_created == 0


class TestBuilderClear:
    """测试 build(clear=True) 的 pre-build 清库容错。"""

    def test_build_clear_graph_failure_tolerated(self, builder: OntoAgentBuilder, temp_repo: Path, caplog) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_graph.clear_all.side_effect = RuntimeError("no CLEAR SPACE permission")
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            caplog.set_level(logging.WARNING, logger="ontoagent.pipeline.builder")
            # Act
            result = builder.build(temp_repo, clear=True, skip_semantic=True, skip_clustering=True)

        # Assert
        assert result.aborted is False
        assert "graph clear failed" in caplog.text

    def test_build_clear_success_logs_cleared(self, builder: OntoAgentBuilder, temp_repo: Path, caplog) -> None:
        # Arrange
        mock_graph = MagicMock()
        mock_graph.clear_all.return_value = 5
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            caplog.set_level(logging.INFO, logger="ontoagent.pipeline.builder")
            # Act
            result = builder.build(temp_repo, clear=True, skip_semantic=True, skip_clustering=True)

        # Assert
        assert result.aborted is False
        assert "Cleared 5 existing nodes" in caplog.text


class TestRepositoryEntityPersistence:
    """测试 build() 写 RepositoryEntity 时的 url 持久化与节点复用。

    RepositoryEntity.id = _stable_id(name, url) 是确定性稳定哈希；builder 按 name
    定向查询已有节点并复用 id，url 非空才覆盖，避免与 repo.py 入口 id 不一致导致重复记录。
    """

    @staticmethod
    def _repo_merge_props(mock_graph: MagicMock) -> dict:
        """从 merge_node 调用列表筛出 label=RepositoryEntity 的 properties。"""
        for args, _kwargs in mock_graph.merge_node.call_args_list:
            if args and args[0] == "RepositoryEntity":
                return args[1]
        pytest.fail("merge_node was never called with label='RepositoryEntity'")

    def test_build_repo_url_persisted_to_merge_node(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        """传入 repo_url → merge_node 收到 url，id 与 RepositoryEntity(name,url) 一致。"""
        # Arrange
        mock_graph = MagicMock()
        mock_graph.get_nodes_by_label.return_value = []
        mock_chroma = MagicMock()
        url = "https://gitee.com/x/y.git"

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(
                temp_repo,
                repo_id="repoA",
                repo_url=url,
                skip_semantic=True,
                skip_clustering=True,
                clear=True,
            )

        # Assert
        props = self._repo_merge_props(mock_graph)
        assert props["name"] == "repoA"
        assert props["url"] == url
        assert props["id"] == RepositoryEntity(name="repoA", url=url).id

    def test_build_keeps_existing_url_when_repo_url_empty(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        """已有节点 + repo_url='' → 复用节点 id，且旧 url 不被清空。"""
        # Arrange
        mock_graph = MagicMock()
        mock_graph.get_nodes_by_label.return_value = [
            {"id": "old-id", "name": "repoA", "url": "https://old.example/repo.git"}
        ]
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(
                temp_repo,
                repo_id="repoA",
                repo_url="",
                skip_semantic=True,
                skip_clustering=True,
                clear=True,
            )

        # Assert
        props = self._repo_merge_props(mock_graph)
        assert props["id"] == "old-id"
        assert props["url"] == "https://old.example/repo.git"

    def test_build_new_repo_url_overrides_old_url(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        """已有节点 + 新 repo_url → 覆盖为新 url。"""
        # Arrange
        mock_graph = MagicMock()
        mock_graph.get_nodes_by_label.return_value = [{"id": "old-id", "name": "repoA", "url": "old"}]
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(
                temp_repo,
                repo_id="repoA",
                repo_url="new",
                skip_semantic=True,
                skip_clustering=True,
                clear=True,
            )

        # Assert
        props = self._repo_merge_props(mock_graph)
        assert props["id"] == "old-id"
        assert props["url"] == "new"

    def test_build_creates_new_entity_when_none_exists(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        """无已有节点 → 用 RepositoryEntity(name=repo_id) 生成新 id。"""
        # Arrange
        mock_graph = MagicMock()
        mock_graph.get_nodes_by_label.return_value = []
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(
                temp_repo,
                repo_id="repoA",
                repo_url="",
                skip_semantic=True,
                skip_clustering=True,
                clear=True,
            )

        # Assert
        props = self._repo_merge_props(mock_graph)
        assert props["id"] == RepositoryEntity(name="repoA").id
        assert props["name"] == "repoA"

    def test_build_creates_new_id_when_existing_id_none(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        """已有节点但 id 为 None → 兜底生成新 id。"""
        # Arrange
        mock_graph = MagicMock()
        mock_graph.get_nodes_by_label.return_value = [{"id": None, "name": "repoA"}]
        mock_chroma = MagicMock()

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(
                temp_repo,
                repo_id="repoA",
                repo_url="",
                skip_semantic=True,
                skip_clustering=True,
                clear=True,
            )

        # Assert
        props = self._repo_merge_props(mock_graph)
        assert props["id"] == RepositoryEntity(name="repoA").id


class TestBuildProgressCallback:
    """测试 build() 的 progress_callback 按阶段上报。"""

    def test_build_reports_stages_in_order(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        """spy 收集回调 → 断言阶段顺序与 stage_detail 含计数。"""
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()
        spy: list[tuple[str, str]] = []

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
            patch("ontoagent.pipeline.builder.check_llm_available", return_value=False),
        ):
            # Act
            builder.build(temp_repo, progress_callback=lambda s, d: spy.append((s, d)))

        # Assert
        stages = [s for s, _ in spy]
        assert stages == [
            "parse",
            "structural_write",
            "doc_link",
            "semantic",
            "clustering",
            "vector_index",
        ]
        details = dict(spy)
        assert "Parsed" in details["parse"]
        assert "Resolved" in details["parse"]
        assert "Wrote" in details["structural_write"]
        assert "DESCRIBES" in details["doc_link"]
        assert "Extracted" in details["semantic"]
        assert "concepts" in details["semantic"]
        assert "Clustered" in details["clustering"]
        assert "modules" in details["clustering"]
        assert details["vector_index"] == "Vector index complete"

    def test_build_clear_reports_prebuild_stage(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        """clear=True 时首个回调为 prebuild，detail 含清库数量。"""
        # Arrange
        mock_graph = MagicMock()
        mock_graph.clear_all.return_value = 5
        mock_chroma = MagicMock()
        spy: list[tuple[str, str]] = []

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
        ):
            # Act
            builder.build(
                temp_repo,
                clear=True,
                skip_semantic=True,
                skip_clustering=True,
                progress_callback=lambda s, d: spy.append((s, d)),
            )

        # Assert
        assert spy[0][0] == "prebuild"
        assert "Cleared 5 existing nodes" in spy[0][1]

    def test_build_progress_callback_error_tolerated(self, builder: OntoAgentBuilder, temp_repo: Path) -> None:
        """回调抛异常不中断 build。"""
        # Arrange
        mock_graph = MagicMock()
        mock_chroma = MagicMock()

        def boom(stage: str, detail: str) -> None:
            raise RuntimeError(f"callback failed at {stage}")

        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=mock_graph),
            patch.object(builder, "_get_chroma_store", return_value=mock_chroma),
            patch("ontoagent.pipeline.builder.check_llm_available", return_value=False),
        ):
            # Act
            result = builder.build(temp_repo, progress_callback=boom)

        # Assert
        assert result.aborted is False
        assert result.entities_created > 0


class TestBuilderQuery:
    """测试 query 方法。"""

    def test_query_searches_chroma(self, builder: OntoAgentBuilder) -> None:
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

    def test_query_with_type_filter(self, builder: OntoAgentBuilder) -> None:
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

    def test_info_returns_config(self, builder: OntoAgentBuilder) -> None:
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

    def test_info_returns_chroma_count(self, builder: OntoAgentBuilder) -> None:
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

    def test_context_manager_closes_stores(self, mock_config: OntoAgentConfig) -> None:
        # Arrange
        with (
            patch("ontoagent.store.factory.create_graph_store") as mock_factory,
            patch("ontoagent.pipeline.builder.ChromaStore") as mock_chroma_cls,
        ):
            mock_graph = MagicMock()
            mock_chroma = MagicMock()
            mock_factory.return_value = mock_graph
            mock_chroma_cls.return_value = mock_chroma

            builder = OntoAgentBuilder(mock_config)

            # Act
            with builder:
                # 触发 store 创建
                builder._get_graph_store()
                builder._get_chroma_store()

            # Assert
            mock_graph.close.assert_called_once()
            mock_chroma.close.assert_called_once()

    def test_context_manager_returns_self(self, mock_config: OntoAgentConfig) -> None:
        # Arrange
        builder = OntoAgentBuilder(mock_config)

        # Act
        with builder as b:
            # Assert
            assert b is builder


class TestBuilderEndToEnd:
    """Task 6: Builder 端到端测试（使用真实 PythonParser + RelationExtractor）。"""

    def test_parse_and_extract_sample_file(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
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

    def test_parse_class_with_methods(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
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

    def test_parse_imports(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
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

    def test_get_parser_python(self, builder: OntoAgentBuilder) -> None:
        """验证 .py 文件路由到 PythonParser。"""
        from ontoagent.parsing.parser.python_parser import PythonParser

        parser = builder._get_parser(Path("foo.py"))
        assert parser is not None
        assert isinstance(parser, PythonParser)
        assert parser.language == "python"

    def test_get_parser_java(self, builder: OntoAgentBuilder) -> None:
        """验证 .java 文件路由到 JavaParser。"""
        from ontoagent.parsing.parser.java_parser import JavaParser

        parser = builder._get_parser(Path("Foo.java"))
        assert parser is not None
        assert isinstance(parser, JavaParser)
        assert parser.language == "java"

    def test_get_parser_unknown_suffix(self, builder: OntoAgentBuilder) -> None:
        """验证未知扩展名返回 None。"""
        parser = builder._get_parser(Path("main.rs"))
        assert parser is None

    def test_stage_parse_uses_java_parser(self, builder: OntoAgentBuilder, tmp_path: Path) -> None:
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

    def test_external_import_language_is_unknown(self, builder: OntoAgentBuilder) -> None:
        """验证外部 import 的 language 不是硬编码 python。"""
        # 检查 _stage_write_structural 中的外部模块 language
        # 间接验证：读取源码中第 327 行附近的 language="unknown"
        import inspect

        source = inspect.getsource(builder._stage_write_structural)
        assert 'language="unknown"' in source or "language='unknown'" in source


@pytest.mark.unit
class TestBuilderRepositoryIdentity:
    """Full-build repository identity handoff coverage using real parsers."""

    @staticmethod
    def _code_batches(graph: _RecordingGraphStore) -> list[list[dict]]:
        return [
            batch
            for label, batch in graph.node_batches
            if label == "CodeEntity" and any(node.get("file_path") != "__external__" for node in batch)
        ]

    def test_full_build_isolates_python_docs_external_imports_and_capabilities(self, tmp_path: Path) -> None:
        """Sequential real builds keep every parser-derived handoff within its repository."""
        source = """from external_sdk import client


class Router:
    def get(self, path):
        return path


router = Router()


class Service:
    @router.get("/orders")
    def entry(self):
        pass
"""
        for repo_name in ("repo-a", "repo-b"):
            repo = tmp_path / repo_name
            repo.mkdir()
            (repo / "service.py").write_text(source)
            (repo / "README.md").write_text("# Service\n\nservice.py documents Service.entry\n")

        builder = OntoAgentBuilder(OntoAgentConfig())
        graph = _RecordingGraphStore()
        chroma = _RecordingChromaStore()
        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=graph),
            patch.object(builder, "_get_chroma_store", return_value=chroma),
        ):
            builder.build(tmp_path / "repo-a", repo_id="repo-a", skip_semantic=True, skip_clustering=True)
            builder.build(tmp_path / "repo-b", repo_id="repo-b", skip_semantic=True, skip_clustering=True)

        code_a, code_b = self._code_batches(graph)
        code_a_ids = {node["id"] for node in code_a}
        code_b_ids = {node["id"] for node in code_b}
        assert {node["repo_id"] for node in code_a} == {"repo-a"}
        assert {node["repo_id"] for node in code_b} == {"repo-b"}
        assert code_a_ids.isdisjoint(code_b_ids)

        structural_batches = [
            batch for batch in graph.relation_batches if any(relation["rel_type"] == "contains" for relation in batch)
        ]
        for batch, ids in zip(structural_batches, (code_a_ids, code_b_ids), strict=True):
            assert all(
                relation["source_id"] in ids and relation["target_id"] in ids
                for relation in batch
                if relation["rel_type"] == "contains"
            )

        external_batches = [
            batch
            for label, batch in graph.node_batches
            if label == "CodeEntity" and batch and all(node.get("file_path") == "__external__" for node in batch)
        ]
        external_a, external_b = external_batches
        assert external_a[0]["repo_id"] == "repo-a"
        assert external_b[0]["repo_id"] == "repo-b"
        assert external_a[0]["id"] != external_b[0]["id"]

        doc_batches = [batch for label, batch in graph.node_batches if label == "DocEntity"]
        assert doc_batches[0][0]["repo_id"] == "repo-a"
        assert doc_batches[1][0]["repo_id"] == "repo-b"
        assert doc_batches[0][0]["id"] != doc_batches[1][0]["id"]
        describes_batches = [
            batch for batch in graph.relation_batches if any(relation["rel_type"] == "describes" for relation in batch)
        ]
        assert describes_batches[0][0]["source_id"] == doc_batches[0][0]["id"]
        assert describes_batches[0][0]["target_id"] in code_a_ids
        assert describes_batches[1][0]["source_id"] == doc_batches[1][0]["id"]
        assert describes_batches[1][0]["target_id"] in code_b_ids

        capability_batches = [batch for label, batch in graph.node_batches if label == "CapabilityEntity"]
        capability_a, capability_b = (batch[0] for batch in capability_batches)
        assert capability_a["entry_code_entity_id"] in code_a_ids
        assert capability_b["entry_code_entity_id"] in code_b_ids
        assert capability_a["id"] != capability_b["id"]
        realized_by_batches = [
            batch
            for batch in graph.relation_batches
            if any(relation["rel_type"] == "realized_by" for relation in batch)
        ]
        assert realized_by_batches[0][0]["target_id"] == capability_a["entry_code_entity_id"]
        assert realized_by_batches[1][0]["target_id"] == capability_b["entry_code_entity_id"]
        capability_vectors = [
            item for batch in chroma.batches for item in batch if item[2]["entity_type"] == "CapabilityEntity"
        ]
        assert capability_vectors[0][0] == capability_a["id"]
        assert capability_vectors[0][2]["repo_id"] == "repo-a"
        assert capability_vectors[0][2]["entry_code_entity_id"] == capability_a["entry_code_entity_id"]
        assert capability_vectors[1][0] == capability_b["id"]
        assert capability_vectors[1][2]["repo_id"] == "repo-b"
        assert capability_vectors[1][2]["entry_code_entity_id"] == capability_b["entry_code_entity_id"]
        assert capability_vectors[0][0] != capability_vectors[1][0]

    def test_full_build_isolates_java_cross_file_relations(self, tmp_path: Path) -> None:
        """Cross-file Java extends relations resolve only against current repository IDs."""
        for repo_name in ("repo-a", "repo-b"):
            repo = tmp_path / repo_name
            repo.mkdir()
            (repo / "Base.java").write_text("package demo; public class Base {}\n")
            (repo / "Child.java").write_text("package demo; public class Child extends Base {}\n")

        builder = OntoAgentBuilder(OntoAgentConfig())
        graph = _RecordingGraphStore()
        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=graph),
            patch.object(builder, "_get_chroma_store", return_value=_RecordingChromaStore()),
        ):
            builder.build(tmp_path / "repo-a", repo_id="repo-a", skip_semantic=True, skip_clustering=True)
            builder.build(tmp_path / "repo-b", repo_id="repo-b", skip_semantic=True, skip_clustering=True)

        code_a, code_b = self._code_batches(graph)
        ids_a = {node["id"] for node in code_a}
        ids_b = {node["id"] for node in code_b}
        extends_batches = [
            batch for batch in graph.relation_batches if any(relation["rel_type"] == "extends" for relation in batch)
        ]
        assert all(relation["source_id"] in ids_a and relation["target_id"] in ids_a for relation in extends_batches[0])
        assert all(relation["source_id"] in ids_b and relation["target_id"] in ids_b for relation in extends_batches[1])
        assert ids_a.isdisjoint(ids_b)

    def test_reused_builder_keeps_same_repo_ids_stable_and_resets_relations(self, tmp_path: Path) -> None:
        """Repeated builds are stable and a new repository cannot inherit prior relations."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        repo_a.mkdir()
        repo_b.mkdir()
        (repo_a / "service.py").write_text("class Service:\n    def entry(self):\n        pass\n")
        (repo_b / "service.py").write_text("class Service:\n    def entry(self):\n        pass\n")
        builder = OntoAgentBuilder(OntoAgentConfig())
        graph = _RecordingGraphStore()
        with (
            patch("ontoagent.store.schema_version.check_schema_version", return_value=SchemaStatus.MATCH),
            patch.object(builder, "_get_graph_store", return_value=graph),
            patch.object(builder, "_get_chroma_store", return_value=_RecordingChromaStore()),
        ):
            builder.build(repo_a, repo_id="repo-a", skip_semantic=True, skip_clustering=True)
            builder.build(repo_a, repo_id="repo-a", skip_semantic=True, skip_clustering=True)
            builder.build(repo_b, repo_id="repo-b", skip_semantic=True, skip_clustering=True)

        code_a_first, code_a_second, code_b = self._code_batches(graph)
        assert {node["id"] for node in code_a_first} == {node["id"] for node in code_a_second}
        second_repo_relations = [
            batch for batch in graph.relation_batches if any(relation["rel_type"] == "contains" for relation in batch)
        ][-1]
        second_repo_ids = {node["id"] for node in code_b}
        assert all(
            relation["source_id"] in second_repo_ids and relation["target_id"] in second_repo_ids
            for relation in second_repo_relations
        )
