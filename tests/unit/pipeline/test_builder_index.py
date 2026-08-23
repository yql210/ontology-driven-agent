from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.index_health import (
    BusinessEntryIndexHealth,
    BusinessEntryIndexStatus,
    IndexHealthReason,
    VectorWriteOutcome,
)
from ontoagent.domain.schema import CodeEntity, ConceptEntity, DocEntity
from ontoagent.parsing.extractor.semantic import SemanticRelation
from ontoagent.pipeline.builder import OntoAgentBuilder


class TestNormalizePath:
    """测试 _normalize_path 方法。"""

    def test_relative_path_unchanged(self, tmp_path: Path) -> None:
        """相对路径保持原样。"""
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)
        result = builder._normalize_path("src/foo.py", tmp_path)
        assert result == "src/foo.py"

    def test_absolute_path_to_relative(self, tmp_path: Path) -> None:
        """绝对路径转为相对路径。"""
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)
        file_path = str(tmp_path / "src" / "foo.py")
        result = builder._normalize_path(file_path, tmp_path)
        assert result == "src/foo.py"

    def test_none_returns_empty(self, tmp_path: Path) -> None:
        """None 返回空字符串。"""
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)
        result = builder._normalize_path(None, tmp_path)
        assert result == ""


class TestBuildEntityIndex:
    """测试 _build_entity_index 方法。"""

    def test_basic_three_entities(self, tmp_path: Path) -> None:
        """3 个不同实体 → 3 个索引键。"""
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)

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
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)

        entities = [
            CodeEntity(name="foo", entity_type="function", file_path="src/a.py"),
            CodeEntity(name="foo", entity_type="function", file_path="src/b.py"),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        assert len(index) == 2
        assert all(len(ids) == 1 for ids in index.values())

    def test_same_name_same_file(self, tmp_path: Path) -> None:
        """同文件同名函数 → 同一个三元组键，值列表长度 2。"""
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)

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
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)
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
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)
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
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)

        entities = [
            ConceptEntity(name="Singleton", entity_type="design_pattern"),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        # (entity_type, "", name)
        expected_key = ("design_pattern", "", "Singleton")
        assert expected_key in index

    def test_doc_entity_with_file_path(self, tmp_path: Path) -> None:
        """DocEntity 有 file_path → 正确构建索引。"""
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)

        entities = [
            DocEntity(name="README", entity_type="readme", file_path="README.md"),
        ]

        index = builder._build_entity_index(entities, tmp_path)
        expected_key = ("readme", "README.md", "README")
        assert expected_key in index

    def test_missing_source_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """source name 找不到 → 跳过，skipped=1。"""
        caplog.set_level(logging.WARNING)
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)
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
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)
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
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)
        builder._repo_root = tmp_path

        index = builder._build_entity_index([], tmp_path)
        resolved, skipped = builder._resolve_semantic_names([], index)

        assert resolved == []
        assert skipped == 0

    def test_empty_index_all_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """空 index → 所有关系都 skipped。"""
        caplog.set_level(logging.WARNING)
        config = OntoAgentConfig()
        builder = OntoAgentBuilder(config)
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
        from ontoagent.pipeline.builder import BuildResult

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
        from ontoagent.pipeline.builder import BuildResult

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

    def test_business_entry_index_is_optional_json_safe_and_printed_when_present(self) -> None:
        from ontoagent.pipeline.builder import BuildResult

        health = BusinessEntryIndexHealth.from_build_facts(
            aborted=False,
            capability_extraction_failed=False,
            eligible_entries_seen=1,
            capabilities_merged=1,
            realized_by_submitted=1,
            capability_vector_outcome=VectorWriteOutcome(1, 1, 0),
        )
        result = BuildResult(1, 2, 3, business_entry_index=health)

        assert BuildResult(1, 2, 3).business_entry_index is None
        assert result.to_dict()["business_entry_index"] == health.to_dict()
        assert "Business entry index: healthy" in str(result)


class _HealthGraphRecorder:
    def ensure_constraints(self) -> None:
        pass

    def get_nodes_by_label(self, _label: str, _properties: list[str]) -> list[dict]:
        return []

    def merge_node(self, _label: str, _properties: dict) -> None:
        pass

    def merge_nodes_batch(self, _label: str, properties: list[dict], batch_size: int = 200) -> int:
        del batch_size
        return len(properties)

    def merge_relations_batch(self, relations: list[dict], batch_size: int = 200) -> int:
        del batch_size
        return len(relations)


class _HealthChromaRecorder:
    def __init__(self, outcome: VectorWriteOutcome | object | None = None) -> None:
        self.outcome = VectorWriteOutcome(0, 0, 0) if outcome is None else outcome
        self.generic_batches: list[list[tuple[str, str, dict]]] = []
        self.capability_batches: list[list[tuple[str, str, dict]]] = []

    def put_entities_batch(self, items: list[tuple[str, str, dict]]) -> None:
        self.generic_batches.append(items)

    def put_entities_batch_with_outcome(self, items: list[tuple[str, str, dict]]) -> VectorWriteOutcome:
        self.capability_batches.append(items)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


def _health_builder(tmp_path: Path, chroma: _HealthChromaRecorder) -> OntoAgentBuilder:
    builder = OntoAgentBuilder(OntoAgentConfig())
    builder._stage_parse = lambda _path: (  # type: ignore[method-assign]
        [CodeEntity(name="orders", entity_type="function", entry_category="http_api")],
        [],
        [],
        1,
        [],
    )
    builder._get_graph_store = lambda: _HealthGraphRecorder()  # type: ignore[method-assign]
    builder._get_chroma_store = lambda: chroma  # type: ignore[method-assign]
    builder._write_business_ontology = lambda *args: (0, 0, 0)  # type: ignore[method-assign]
    return builder


@pytest.mark.unit
def test_builder_reports_healthy_business_entry_index_from_real_stages(tmp_path: Path) -> None:
    builder = _health_builder(tmp_path, _HealthChromaRecorder(VectorWriteOutcome(1, 1, 0)))

    result = builder.build(tmp_path, skip_semantic=True, skip_clustering=True)

    assert result.business_entry_index is not None
    assert result.business_entry_index.status is BusinessEntryIndexStatus.HEALTHY
    assert result.business_entry_index.eligible_entries_seen == 1
    assert result.business_entry_index.capabilities_merged == 1
    assert result.business_entry_index.realized_by_submitted == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "expected_reason"),
    [
        (VectorWriteOutcome(1, 0, 1), IndexHealthReason.CAPABILITY_VECTOR_WRITE_FAILED),
        (VectorWriteOutcome(0, 0, 0), IndexHealthReason.NO_REALIZATIONS_SUBMITTED),
    ],
)
def test_builder_reports_capability_health_anomalies(
    tmp_path: Path, outcome: VectorWriteOutcome, expected_reason: IndexHealthReason
) -> None:
    builder = _health_builder(tmp_path, _HealthChromaRecorder(outcome))
    if outcome.submitted == 0:
        builder._extract_capabilities = lambda *args: (1, 0, 1)  # type: ignore[method-assign]

    result = builder.build(tmp_path, skip_semantic=True, skip_clustering=True)

    assert result.business_entry_index is not None
    assert result.business_entry_index.status is BusinessEntryIndexStatus.DEGRADED
    assert expected_reason in result.business_entry_index.reasons


@pytest.mark.unit
def test_builder_no_eligible_and_abort_have_unavailable_health(tmp_path: Path) -> None:
    builder = _health_builder(tmp_path, _HealthChromaRecorder())
    builder._stage_parse = lambda _path: ([CodeEntity(name="plain", entity_type="function")], [], [], 1, [])  # type: ignore[method-assign]
    no_eligible = builder.build(tmp_path, skip_semantic=True, skip_clustering=True)
    assert no_eligible.business_entry_index is not None
    assert no_eligible.business_entry_index.status is BusinessEntryIndexStatus.UNAVAILABLE
    assert no_eligible.business_entry_index.reasons == (IndexHealthReason.NO_ELIGIBLE_ENTRIES,)

    aborted_builder = _health_builder(tmp_path, _HealthChromaRecorder())
    aborted_builder._stage_write_structural = lambda *args: (_ for _ in ()).throw(RuntimeError("stage 2"))  # type: ignore[method-assign]
    aborted = aborted_builder.build(tmp_path, skip_semantic=True, skip_clustering=True)
    assert aborted.business_entry_index is not None
    assert aborted.business_entry_index.reasons == (IndexHealthReason.BUILD_ABORTED,)


@pytest.mark.unit
def test_builder_extraction_failure_is_degraded(tmp_path: Path) -> None:
    builder = _health_builder(tmp_path, _HealthChromaRecorder())
    builder._extract_capabilities = lambda *args: (_ for _ in ()).throw(RuntimeError("extract failed"))  # type: ignore[method-assign]

    result = builder.build(tmp_path, skip_semantic=True, skip_clustering=True)

    assert result.business_entry_index is not None
    assert result.business_entry_index.status is BusinessEntryIndexStatus.DEGRADED
    assert result.business_entry_index.reasons == (IndexHealthReason.CAPABILITY_EXTRACTION_FAILED,)


@pytest.mark.unit
def test_builder_ordinary_vector_failure_does_not_poison_capability_health(tmp_path: Path) -> None:
    class FailingOrdinaryChroma(_HealthChromaRecorder):
        def put_entities_batch(self, items: list[tuple[str, str, dict]]) -> None:
            del items
            raise RuntimeError("ordinary vector failure")

    builder = _health_builder(tmp_path, FailingOrdinaryChroma(VectorWriteOutcome(1, 1, 0)))
    result = builder.build(tmp_path, skip_semantic=True, skip_clustering=True)

    assert result.business_entry_index is not None
    assert IndexHealthReason.CAPABILITY_VECTOR_WRITE_FAILED not in result.business_entry_index.reasons
    assert result.business_entry_index.status is BusinessEntryIndexStatus.HEALTHY


@pytest.mark.unit
@pytest.mark.parametrize("origin", ["preparation", "outcome_call", "invalid_outcome"])
def test_builder_capability_specific_failures_use_synthetic_outcome(tmp_path: Path, origin: str, monkeypatch) -> None:
    chroma = _HealthChromaRecorder(VectorWriteOutcome(1, 1, 0))
    builder = _health_builder(tmp_path, chroma)
    if origin == "preparation":
        monkeypatch.setattr(
            "ontoagent.pipeline.builder.capability_to_searchable_text",
            lambda *args: (_ for _ in ()).throw(RuntimeError("preparation failed")),
        )
    elif origin == "outcome_call":
        chroma.outcome = RuntimeError("outcome call failed")
    else:
        chroma.outcome = object()

    result = builder.build(tmp_path, skip_semantic=True, skip_clustering=True)

    assert result.business_entry_index is not None
    health = result.business_entry_index
    assert health.status is BusinessEntryIndexStatus.DEGRADED
    assert health.capability_vectors_submitted == 1
    assert health.capability_vectors_confirmed == 0
    assert health.capability_vectors_failed == 1
    assert IndexHealthReason.CAPABILITY_VECTOR_WRITE_FAILED in health.reasons
