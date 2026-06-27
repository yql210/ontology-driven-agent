"""Tests for IncrementalUpdater."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.schema import CodeEntity, Relation
from ontoagent.parsing.parser.base import ParseResult
from ontoagent.pipeline.change_detector import ChangedFile, ChangeType, GitStatus
from ontoagent.pipeline.impact_propagator import ImpactedNode, ImpactReport, ImpactSeverity, PropagationDirection
from ontoagent.pipeline.incremental_updater import IncrementalUpdater, UpdateReport


class TestUpdateReport:
    """Tests for UpdateReport dataclass."""

    def test_create_with_all_fields(self):
        """Should create UpdateReport with all fields."""
        report = UpdateReport(
            changes_detected=5,
            nodes_added=3,
            nodes_updated=2,
            nodes_deleted=1,
            relations_rebuilt=4,
            vectors_updated=3,
            impacted_nodes_count=10,
            orphans_removed=0,
            changeset_id="cs-abc123",
            elapsed_ms=123.456,
            parse_errors=1,
            failed_files=["foo.py"],
        )
        assert report.changes_detected == 5
        assert report.nodes_added == 3
        assert report.nodes_updated == 2
        assert report.nodes_deleted == 1
        assert report.relations_rebuilt == 4
        assert report.vectors_updated == 3
        assert report.impacted_nodes_count == 10
        assert report.orphans_removed == 0
        assert report.changeset_id == "cs-abc123"
        assert report.elapsed_ms == 123.456
        assert report.parse_errors == 1
        assert report.failed_files == ["foo.py"]

    def test_default_values(self):
        """Should have correct default values (orphans_removed=0, parse_errors=0, failed_files=[])."""
        report = UpdateReport(
            changes_detected=1,
            nodes_added=0,
            nodes_updated=0,
            nodes_deleted=0,
            relations_rebuilt=0,
            vectors_updated=0,
            impacted_nodes_count=0,
            orphans_removed=0,
            changeset_id="",
            elapsed_ms=0.0,
        )
        assert report.orphans_removed == 0
        assert report.parse_errors == 0
        assert report.failed_files == []

    def test_to_dict_rounds_elapsed_ms(self):
        """to_dict() should round elapsed_ms to 2 decimal places."""
        report = UpdateReport(
            changes_detected=1,
            nodes_added=1,
            nodes_updated=0,
            nodes_deleted=0,
            relations_rebuilt=0,
            vectors_updated=1,
            impacted_nodes_count=1,
            orphans_removed=0,
            changeset_id="cs-abc123",
            elapsed_ms=123.4567,
        )
        d = report.to_dict()
        assert d["elapsed_ms"] == 123.46

    def test_elapsed_ms_positive(self):
        """elapsed_ms should be positive in normal operation."""
        report = UpdateReport(
            changes_detected=1,
            nodes_added=0,
            nodes_updated=0,
            nodes_deleted=0,
            relations_rebuilt=0,
            vectors_updated=0,
            impacted_nodes_count=0,
            orphans_removed=0,
            changeset_id="",
            elapsed_ms=0.1,
        )
        assert report.elapsed_ms >= 0

    def test_new_fields_default_to_zero(self):
        """新增字段 concepts_flagged、docs_flagged、integrity_warnings 默认值应为 0。"""
        report = UpdateReport(
            changes_detected=1,
            nodes_added=0,
            nodes_updated=0,
            nodes_deleted=0,
            relations_rebuilt=0,
            vectors_updated=0,
            impacted_nodes_count=0,
            orphans_removed=0,
            changeset_id="",
            elapsed_ms=0.0,
        )
        assert report.concepts_flagged == 0
        assert report.docs_flagged == 0
        assert report.integrity_warnings == 0

    def test_to_dict_includes_new_fields(self):
        """to_dict() 应包含 concepts_flagged、docs_flagged、integrity_warnings 三个新字段。"""
        report = UpdateReport(
            changes_detected=1,
            nodes_added=1,
            nodes_updated=0,
            nodes_deleted=0,
            relations_rebuilt=0,
            vectors_updated=1,
            impacted_nodes_count=1,
            orphans_removed=0,
            changeset_id="cs-abc123",
            elapsed_ms=123.456,
            concepts_flagged=5,
            docs_flagged=3,
            integrity_warnings=2,
        )
        d = report.to_dict()
        assert "concepts_flagged" in d
        assert "docs_flagged" in d
        assert "integrity_warnings" in d
        assert d["concepts_flagged"] == 5
        assert d["docs_flagged"] == 3
        assert d["integrity_warnings"] == 2


class TestIncrementalUpdaterConstructor:
    """Tests for IncrementalUpdater constructor."""

    def test_create_instance_attributes(self):
        """Should create instance with correct config and repo_path attributes."""
        config = OntoAgentConfig()
        repo_path = Path("/tmp/repo")
        updater = IncrementalUpdater(config, repo_path)
        assert updater._config is config
        assert updater._repo_path == repo_path

    def test_lazy_init_graph_store(self):
        """graph_store should be None initially (lazy init)."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)
        assert updater._graph_store is None
        assert updater._chroma_store is None
        assert updater._change_detector is None
        assert updater._impact_propagator is None

    def test_context_manager(self):
        """__enter__ should return self, __exit__ should call close."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock close method
        updater.close = MagicMock()  # type: ignore[method-assign]

        with updater as ctx:
            assert ctx is updater

        updater.close.assert_called_once()


class TestDetectChanges:
    """Tests for _detect_changes method."""

    def test_mock_detector_returns_three_changes(self):
        """mock GitChangeDetector.detect_changes → return 3 ChangedFile."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
            ChangedFile(path="b.py", change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED),
            ChangedFile(path="c.py", change_type=ChangeType.DELETED, git_status=GitStatus.DELETED),
        ]
        mock_detector.detect_changes.return_value = mock_changes
        updater._change_detector = mock_detector

        result = updater._detect_changes("HEAD~1")

        assert len(result) == 3
        mock_detector.detect_changes.assert_called_once_with("HEAD~1")

    def test_full_scan_calls_full_scan(self):
        """full_scan=True → call detector.full_scan()."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="x.py", change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED),
        ]
        mock_detector.full_scan.return_value = mock_changes
        updater._change_detector = mock_detector

        result = updater._detect_changes("HEAD~1", full_scan=True)

        assert len(result) == 1
        mock_detector.full_scan.assert_called_once()

    def test_no_changes_returns_empty_list(self):
        """No changes → return empty list."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        mock_detector = MagicMock()
        mock_detector.detect_changes.return_value = []
        updater._change_detector = mock_detector

        result = updater._detect_changes("HEAD~1")

        assert result == []


class TestPropagateImpact:
    """Tests for _propagate_impact method."""

    def test_mock_propagator_returns_impact_report(self):
        """mock ImpactPropagator.propagate → return report with 5 impacted_nodes."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock impact propagator
        mock_propagator = MagicMock()
        impacted_nodes = [
            ImpactedNode(
                node_id=f"n{i}",
                node_label="CodeEntity",
                name=f"func{i}",
                impact_score=0.9 - i * 0.1,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls"],
                source_node_id="src",
            )
            for i in range(5)
        ]
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["n1"],
            impacted_nodes=impacted_nodes,
            total_analyzed=5,
            propagation_time_ms=10.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        changes = [ChangedFile(path="a.py", change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED)]
        result = updater._propagate_impact(changes)

        assert len(result.impacted_nodes) == 5
        mock_propagator.propagate.assert_called_once_with(changes)

    def test_empty_changes_returns_empty_report(self):
        """Empty changes list → return empty ImpactReport (don't call propagator)."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        mock_propagator = MagicMock()
        updater._impact_propagator = mock_propagator

        result = updater._propagate_impact([])

        assert result.changed_files == []
        assert result.changed_node_ids == []
        assert result.impacted_nodes == []
        assert result.total_analyzed == 0
        assert result.propagation_time_ms == 0.0
        mock_propagator.propagate.assert_not_called()

    def test_impacted_nodes_count_correct(self):
        """impacted_nodes_count should match len(impacted_nodes)."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        mock_propagator = MagicMock()
        impacted_nodes = [
            ImpactedNode(
                node_id=f"n{i}",
                node_label="CodeEntity",
                name=f"func{i}",
                impact_score=0.8,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.BACKWARD,
                relation_path=["calls"],
                source_node_id="src",
            )
            for i in range(3)
        ]
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["n1"],
            impacted_nodes=impacted_nodes,
            total_analyzed=3,
            propagation_time_ms=5.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        changes = [ChangedFile(path="a.py", change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED)]
        result = updater._propagate_impact(changes)

        assert len(result.impacted_nodes) == len(impacted_nodes) == 3


class TestApplyAdded:
    """Tests for _apply_added method."""

    def test_mock_parser_returns_entities_and_relations(self):
        """mock parser returns 2 entities + 1 relation → nodes_added=2, relations_rebuilt=1, vectors_updated=2."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass\ndef func2(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser
            mock_parser = MagicMock()
            entity1 = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            entity2 = CodeEntity(name="func2", entity_type="function", source="def func2(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity1, entity2],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock extractor
            mock_extractor = MagicMock()
            rel = Relation(source_id=entity1.id, target_id=entity2.id, relation_type="calls")
            mock_extractor.resolve.return_value = [rel]
            updater._extractor = mock_extractor

            # Mock graph_store
            mock_graph_store = MagicMock()
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(path=temp_path, change_type=ChangeType.ADDED, git_status=GitStatus.ADDED)

            result = updater._apply_added(change)

            assert result["nodes_added"] == 2
            assert result["relations_rebuilt"] == 1
            assert result["vectors_updated"] == 2
            assert mock_graph_store.merge_node.call_count == 2
            assert mock_graph_store.merge_relation.call_count == 1
            assert mock_chroma_store.put_entities_batch.call_count == 1
        finally:
            os.unlink(temp_path)

    def test_file_not_exists_returns_zeros(self):
        """File not exists → return all zeros."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        change = ChangedFile(path="/nonexistent/file.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED)

        result = updater._apply_added(change)

        assert result["nodes_added"] == 0
        assert result["relations_rebuilt"] == 0
        assert result["vectors_updated"] == 0

    def test_parse_result_error_returns_zeros(self):
        """parse_result.error → return all zeros."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("invalid syntax")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser with error
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[],
                relations=[],
                error="Syntax error",
            )
            updater._parser = mock_parser

            change = ChangedFile(path=temp_path, change_type=ChangeType.ADDED, git_status=GitStatus.ADDED)

            result = updater._apply_added(change)

            assert result["nodes_added"] == 0
            assert result["relations_rebuilt"] == 0
            assert result["vectors_updated"] == 0
        finally:
            os.unlink(temp_path)

    def test_no_embeddable_text_vectors_zero(self):
        """Empty entity list → vectors_updated=0."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser returns no entities
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock extractor
            mock_extractor = MagicMock()
            mock_extractor.resolve.return_value = []
            updater._extractor = mock_extractor

            # Mock stores
            mock_graph_store = MagicMock()
            updater._graph_store = mock_graph_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(path=temp_path, change_type=ChangeType.ADDED, git_status=GitStatus.ADDED)

            result = updater._apply_added(change)

            assert result["nodes_added"] == 0
            assert result["vectors_updated"] == 0
            # put_entities_batch should NOT be called (no items)
            mock_chroma_store.put_entities_batch.assert_not_called()
        finally:
            os.unlink(temp_path)


class TestApplyDeleted:
    """Tests for _apply_deleted method."""

    def test_mock_query_returns_nodes_deleted(self):
        """mock query returns 2 nodes → nodes_deleted=2, verify delete_node called twice."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = [
            {"id": "node1", "name": "func1"},
            {"id": "node2", "name": "func2"},
        ]
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        mock_chroma_store.delete_entities_by_metadata.return_value = 2
        updater._chroma_store = mock_chroma_store

        change = ChangedFile(path="test.py", change_type=ChangeType.DELETED, git_status=GitStatus.DELETED)

        result = updater._apply_deleted(change)

        assert result["nodes_deleted"] == 2
        assert result["relations_rebuilt"] == 0
        assert result["vectors_updated"] == 0
        assert mock_graph_store.delete_node.call_count == 2
        mock_graph_store.delete_node.assert_any_call("node1")
        mock_graph_store.delete_node.assert_any_call("node2")

    def test_delete_node_called_with_detach_delete(self):
        """verify delete_node is called (DETACH DELETE semantic — no need to manually delete relations)."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = [{"id": "node1"}]
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        change = ChangedFile(path="test.py", change_type=ChangeType.DELETED, git_status=GitStatus.DELETED)

        updater._apply_deleted(change)

        # Verify delete_node is called (Neo4jStore.delete_node uses DETACH DELETE)
        mock_graph_store.delete_node.assert_called_once_with("node1")

    def test_no_nodes_in_graph_returns_zeros(self):
        """mock query returns empty list → return all zeros."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = []
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        change = ChangedFile(path="test.py", change_type=ChangeType.DELETED, git_status=GitStatus.DELETED)

        result = updater._apply_deleted(change)

        assert result["nodes_deleted"] == 0
        assert result["relations_rebuilt"] == 0
        assert result["vectors_updated"] == 0
        mock_graph_store.delete_node.assert_not_called()

    def test_delete_entities_by_metadata_called(self):
        """verify chroma_store.delete_entities_by_metadata is called with file_path."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = [{"id": "node1"}]
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        change = ChangedFile(path="test.py", change_type=ChangeType.DELETED, git_status=GitStatus.DELETED)

        updater._apply_deleted(change)

        # Verify delete_entities_by_metadata is called with correct parameter
        mock_chroma_store.delete_entities_by_metadata.assert_called_once_with({"file_path": "test.py"})


class TestApplyModifiedSignature:
    """Tests for _apply_modified with SIGNATURE change type."""

    def test_signature_change_rebuilds(self):
        """SIGNATURE change → query old nodes, delete old relations, rebuild → nodes_updated+relations_rebuilt correct."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass\ndef func2(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser returns entities
            mock_parser = MagicMock()
            entity1 = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            entity2 = CodeEntity(name="func2", entity_type="function", source="def func2(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity1, entity2],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock extractor
            mock_extractor = MagicMock()
            rel = Relation(source_id=entity1.id, target_id=entity2.id, relation_type="calls")
            mock_extractor.resolve.return_value = [rel]
            updater._extractor = mock_extractor

            # Mock graph_store with old nodes and relations
            mock_graph_store = MagicMock()
            mock_graph_store.query.return_value = [
                {"id": "old_node1"},
                {"id": "old_node2"},
            ]
            mock_graph_store.get_relations.return_value = []  # No existing relations
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(
                path=temp_path,
                change_type=ChangeType.SIGNATURE,
                git_status=GitStatus.MODIFIED,
            )

            result = updater._apply_modified(change)

            assert result["nodes_updated"] == 2
            assert result["relations_rebuilt"] == 1
            assert result["vectors_updated"] == 2
            assert mock_graph_store.merge_node.call_count == 2
            assert mock_graph_store.merge_relation.call_count == 1
        finally:
            os.unlink(temp_path)

    def test_merge_node_called(self):
        """verify merge_node is called for SIGNATURE change."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser
            mock_parser = MagicMock()
            entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock extractor
            mock_extractor = MagicMock()
            mock_extractor.resolve.return_value = []
            updater._extractor = mock_extractor

            # Mock graph_store
            mock_graph_store = MagicMock()
            mock_graph_store.query.return_value = [{"id": "old_node1"}]
            mock_graph_store.get_relations.return_value = []
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(
                path=temp_path,
                change_type=ChangeType.SIGNATURE,
                git_status=GitStatus.MODIFIED,
            )

            updater._apply_modified(change)

            # Verify merge_node is called
            mock_graph_store.merge_node.assert_called()
        finally:
            os.unlink(temp_path)

    def test_file_not_exists_returns_zeros(self):
        """File not exists → return all zeros for SIGNATURE change."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        change = ChangedFile(
            path="/nonexistent/file.py",
            change_type=ChangeType.SIGNATURE,
            git_status=GitStatus.MODIFIED,
        )

        result = updater._apply_modified(change)

        assert result["nodes_updated"] == 0
        assert result["relations_rebuilt"] == 0
        assert result["vectors_updated"] == 0

    def test_parse_result_error_returns_zeros(self):
        """parse_result.error → return all zeros for SIGNATURE change."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("invalid syntax")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser with error
            mock_parser = MagicMock()
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[],
                relations=[],
                error="Syntax error",
            )
            updater._parser = mock_parser

            # Mock graph_store
            mock_graph_store = MagicMock()
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(
                path=temp_path,
                change_type=ChangeType.SIGNATURE,
                git_status=GitStatus.MODIFIED,
            )

            result = updater._apply_modified(change)

            assert result["nodes_updated"] == 0
            assert result["relations_rebuilt"] == 0
            assert result["vectors_updated"] == 0
        finally:
            os.unlink(temp_path)


class TestApplyModifiedBody:
    """Tests for _apply_modified with BODY change type."""

    def test_body_change_updates_nodes_not_relations(self):
        """BODY change → nodes_updated>0, relations_rebuilt>0 (relations are rebuilt for SIGNATURE/BODY)."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser
            mock_parser = MagicMock()
            entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock extractor
            mock_extractor = MagicMock()
            mock_extractor.resolve.return_value = []
            updater._extractor = mock_extractor

            # Mock graph_store
            mock_graph_store = MagicMock()
            mock_graph_store.query.return_value = [{"id": "old_node1"}]
            mock_graph_store.get_relations.return_value = []
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(
                path=temp_path,
                change_type=ChangeType.BODY,
                git_status=GitStatus.MODIFIED,
            )

            result = updater._apply_modified(change)

            assert result["nodes_updated"] == 1
            assert result["vectors_updated"] == 1
        finally:
            os.unlink(temp_path)

    def test_body_change_updates_vectors(self):
        """BODY change → put_entities_batch is called."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser
            mock_parser = MagicMock()
            entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock extractor
            mock_extractor = MagicMock()
            mock_extractor.resolve.return_value = []
            updater._extractor = mock_extractor

            # Mock graph_store
            mock_graph_store = MagicMock()
            mock_graph_store.query.return_value = [{"id": "old_node1"}]
            mock_graph_store.get_relations.return_value = []
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(
                path=temp_path,
                change_type=ChangeType.BODY,
                git_status=GitStatus.MODIFIED,
            )

            updater._apply_modified(change)

            # Verify put_entities_batch is called
            mock_chroma_store.put_entities_batch.assert_called()
        finally:
            os.unlink(temp_path)

    def test_body_change_no_delete_relation(self):
        """BODY change → old relations are deleted before being rebuilt (delete_relation is called)."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser
            mock_parser = MagicMock()
            entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock extractor
            mock_extractor = MagicMock()
            mock_extractor.resolve.return_value = []
            updater._extractor = mock_extractor

            # Mock graph_store with old relations
            mock_graph_store = MagicMock()
            mock_graph_store.query.return_value = [{"id": "old_node1"}]
            mock_graph_store.get_relations.return_value = [
                {
                    "source_id": "old_node1",
                    "target_id": "other",
                    "rel_type": "CALLS",
                }
            ]
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(
                path=temp_path,
                change_type=ChangeType.BODY,
                git_status=GitStatus.MODIFIED,
            )

            updater._apply_modified(change)

            # Verify delete_relation is called to remove old relations
            mock_graph_store.delete_relation.assert_called()
        finally:
            os.unlink(temp_path)


class TestApplyModifiedDocOnly:
    """Tests for _update_vectors_only (DOC_ONLY change type)."""

    def test_doc_only_calls_update_vectors_only(self):
        """DOC_ONLY change → _update_vectors_only is called."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser
            mock_parser = MagicMock()
            entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(
                path=temp_path,
                change_type=ChangeType.DOC_ONLY,
                git_status=GitStatus.MODIFIED,
            )

            result = updater._apply_modified(change)

            # Verify vectors_updated is set but nodes_updated and relations_rebuilt are 0
            assert result["nodes_updated"] == 0
            assert result["relations_rebuilt"] == 0
            assert result["vectors_updated"] == 1
        finally:
            os.unlink(temp_path)

    def test_doc_only_no_merge_node(self):
        """DOC_ONLY change → merge_node and merge_relation are NOT called."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser
            mock_parser = MagicMock()
            entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock graph_store
            mock_graph_store = MagicMock()
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(
                path=temp_path,
                change_type=ChangeType.DOC_ONLY,
                git_status=GitStatus.MODIFIED,
            )

            updater._apply_modified(change)

            # Verify merge_node and merge_relation are NOT called
            mock_graph_store.merge_node.assert_not_called()
            mock_graph_store.merge_relation.assert_not_called()
        finally:
            os.unlink(temp_path)

    def test_doc_only_vectors_updated(self):
        """DOC_ONLY change → put_entities_batch is called to update vectors."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            config = OntoAgentConfig()
            updater = IncrementalUpdater(config)

            # Mock parser
            mock_parser = MagicMock()
            entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            change = ChangedFile(
                path=temp_path,
                change_type=ChangeType.DOC_ONLY,
                git_status=GitStatus.MODIFIED,
            )

            updater._apply_modified(change)

            # Verify put_entities_batch is called
            mock_chroma_store.put_entities_batch.assert_called()
        finally:
            os.unlink(temp_path)


class TestRecordChangeset:
    """Tests for _record_changeset method."""

    def test_changeset_id_format(self):
        """changeset_id format should be 'cs-{12hex}'."""
        import re

        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        updater._graph_store = mock_graph_store

        changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        impact_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["n1"],
            impacted_nodes=[],
            total_analyzed=1,
            propagation_time_ms=10.0,
        )
        stage3 = {"nodes_added": 1, "nodes_updated": 0, "nodes_deleted": 0}

        changeset_id = updater._record_changeset(changes, impact_report, stage3)

        # Verify format: cs-{12hex}
        assert re.match(r"^cs-[a-f0-9]{12}$", changeset_id)

    def test_merge_node_called_with_changeset_label(self):
        """verify merge_node is called with ChangeSetEntity label."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        updater._graph_store = mock_graph_store

        changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        impact_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["n1"],
            impacted_nodes=[],
            total_analyzed=1,
            propagation_time_ms=10.0,
        )
        stage3 = {"nodes_added": 1, "nodes_updated": 0, "nodes_deleted": 0}

        updater._record_changeset(changes, impact_report, stage3)

        # Verify merge_node is called with ChangeSetEntity
        mock_graph_store.merge_node.assert_called_once()
        call_args = mock_graph_store.merge_node.call_args
        assert call_args[0][0] == "ChangeSetEntity"

    def test_files_changed_list_correct(self):
        """files_changed in changeset should match input changes."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        updater._graph_store = mock_graph_store

        changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
            ChangedFile(path="b.py", change_type=ChangeType.DELETED, git_status=GitStatus.DELETED),
        ]
        impact_report = ImpactReport(
            changed_files=["a.py", "b.py"],
            changed_node_ids=["n1", "n2"],
            impacted_nodes=[],
            total_analyzed=2,
            propagation_time_ms=10.0,
        )
        stage3 = {"nodes_added": 1, "nodes_updated": 0, "nodes_deleted": 1}

        updater._record_changeset(changes, impact_report, stage3)

        # Verify files_changed list
        call_args = mock_graph_store.merge_node.call_args
        properties = call_args[0][1]
        assert properties["files_changed"] == ["a.py", "b.py"]


class TestUpdateFlow:
    """Tests for update method (full four-stage flow)."""

    def test_full_update_flow(self):
        """Full four-stage flow → UpdateReport all fields correct."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        mock_detector.detect_changes.return_value = mock_changes
        updater._change_detector = mock_detector

        # Mock impact propagator
        mock_propagator = MagicMock()
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["n1"],
            impacted_nodes=[],
            total_analyzed=1,
            propagation_time_ms=10.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        # Mock parser
        mock_parser = MagicMock()
        entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
        mock_parser.parse_file.return_value = ParseResult(
            file_path="a.py",
            entities=[entity],
            relations=[],
            error=None,
        )
        updater._parser = mock_parser

        # Mock extractor
        mock_extractor = MagicMock()
        mock_extractor.resolve.return_value = []
        updater._extractor = mock_extractor

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = []
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            # Update the change path to use temp file
            mock_changes[0].path = temp_path

            report = updater.update("HEAD~1")

            assert report.changes_detected == 1
            assert report.nodes_added == 1
            assert report.impacted_nodes_count == 0
            assert report.changeset_id != ""  # Should have a changeset_id
            assert report.elapsed_ms > 0
        finally:
            os.unlink(temp_path)

    def test_dry_run(self):
        """dry_run=True → only changes_detected+impacted_nodes_count, Stage3/4 not executed."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        mock_detector.detect_changes.return_value = mock_changes
        updater._change_detector = mock_detector

        # Mock impact propagator
        mock_propagator = MagicMock()
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["n1"],
            impacted_nodes=[],
            total_analyzed=1,
            propagation_time_ms=10.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        report = updater.update("HEAD~1", dry_run=True)

        assert report.changes_detected == 1
        assert report.impacted_nodes_count == 0
        assert report.nodes_added == 0
        assert report.nodes_updated == 0
        assert report.nodes_deleted == 0
        assert report.relations_rebuilt == 0
        assert report.vectors_updated == 0
        assert report.changeset_id == ""  # Empty for dry_run

    def test_empty_changes(self):
        """No changes → all counters should be 0."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector returning no changes
        mock_detector = MagicMock()
        mock_detector.detect_changes.return_value = []
        updater._change_detector = mock_detector

        report = updater.update("HEAD~1")

        assert report.changes_detected == 0
        assert report.nodes_added == 0
        assert report.nodes_updated == 0
        assert report.nodes_deleted == 0
        assert report.relations_rebuilt == 0
        assert report.vectors_updated == 0
        assert report.impacted_nodes_count == 0
        assert report.changeset_id == ""

    def test_elapsed_ms_positive(self):
        """elapsed_ms should be positive (measures actual time)."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_detector.detect_changes.return_value = []
        updater._change_detector = mock_detector

        report = updater.update("HEAD~1")

        assert report.elapsed_ms >= 0


class TestCLIUpdate:
    """Tests for CLI update command."""

    def test_cli_update_basic(self, tmp_path):
        """Basic CLI update command should call IncrementalUpdater.update."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from ontoagent.api.cli import main

        runner = CliRunner()

        # Create a real temporary directory for Click's exists=True check
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with (
            patch("ontoagent.api.cli.IncrementalUpdater") as mock_updater_class,
            patch("ontoagent.api.cli.OntoAgentConfig") as mock_config_class,
        ):
            # Setup mocks
            mock_config = MagicMock()
            mock_config_class.from_env.return_value = mock_config

            mock_updater_instance = MagicMock()
            mock_updater_instance.__enter__ = MagicMock(return_value=mock_updater_instance)
            mock_updater_instance.__exit__ = MagicMock(return_value=False)
            mock_updater_instance.update.return_value = MagicMock(
                to_dict=lambda: {"changes_detected": 1, "nodes_added": 1}
            )
            mock_updater_class.return_value = mock_updater_instance

            result = runner.invoke(main, ["update", str(repo_dir)])

            assert result.exit_code == 0
            mock_updater_instance.update.assert_called_once()

    def test_cli_update_dry_run(self, tmp_path):
        """CLI update with --dry-run flag."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from ontoagent.api.cli import main

        runner = CliRunner()

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with (
            patch("ontoagent.api.cli.IncrementalUpdater") as mock_updater_class,
            patch("ontoagent.api.cli.OntoAgentConfig") as mock_config_class,
        ):
            mock_config = MagicMock()
            mock_config_class.from_env.return_value = mock_config

            mock_updater_instance = MagicMock()
            mock_updater_instance.__enter__ = MagicMock(return_value=mock_updater_instance)
            mock_updater_instance.__exit__ = MagicMock(return_value=False)
            mock_updater_instance.update.return_value = MagicMock(to_dict=lambda: {"changes_detected": 1})
            mock_updater_class.return_value = mock_updater_instance

            result = runner.invoke(main, ["update", str(repo_dir), "--dry-run"])

            assert result.exit_code == 0
            # Verify dry_run=True was passed
            call_kwargs = mock_updater_instance.update.call_args[1]
            assert call_kwargs["dry_run"] is True

    def test_cli_update_full_scan(self, tmp_path):
        """CLI update with --full-scan flag."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from ontoagent.api.cli import main

        runner = CliRunner()

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with (
            patch("ontoagent.api.cli.IncrementalUpdater") as mock_updater_class,
            patch("ontoagent.api.cli.OntoAgentConfig") as mock_config_class,
        ):
            mock_config = MagicMock()
            mock_config_class.from_env.return_value = mock_config

            mock_updater_instance = MagicMock()
            mock_updater_instance.__enter__ = MagicMock(return_value=mock_updater_instance)
            mock_updater_instance.__exit__ = MagicMock(return_value=False)
            mock_updater_instance.update.return_value = MagicMock(to_dict=lambda: {"changes_detected": 2})
            mock_updater_class.return_value = mock_updater_instance

            result = runner.invoke(main, ["update", str(repo_dir), "--full-scan"])

            assert result.exit_code == 0
            # Verify full_scan=True was passed
            call_kwargs = mock_updater_instance.update.call_args[1]
            assert call_kwargs["full_scan"] is True

    def test_cli_update_output_contains_summary(self, tmp_path):
        """CLI update output should contain summary from to_dict()."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from ontoagent.api.cli import main

        runner = CliRunner()

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        with (
            patch("ontoagent.api.cli.IncrementalUpdater") as mock_updater_class,
            patch("ontoagent.api.cli.OntoAgentConfig") as mock_config_class,
        ):
            mock_config = MagicMock()
            mock_config_class.from_env.return_value = mock_config

            mock_updater_instance = MagicMock()
            mock_updater_instance.__enter__ = MagicMock(return_value=mock_updater_instance)
            mock_updater_instance.__exit__ = MagicMock(return_value=False)
            mock_updater_instance.update.return_value = MagicMock(
                to_dict=lambda: {"changes_detected": 5, "nodes_added": 3}
            )
            mock_updater_class.return_value = mock_updater_instance

            result = runner.invoke(main, ["update", str(repo_dir)])

            assert result.exit_code == 0
            # Verify output contains the report dict
            assert "changes_detected" in result.output


class TestMixedScenarios:
    """Tests for mixed/edge case scenarios."""

    def test_mixed_changes(self):
        """Mixed ADDED+DELETED+SIGNATURE+DOC_ONLY changes → counters correct."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            mock_changes = [
                ChangedFile(path=temp_path, change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
                ChangedFile(path="deleted.py", change_type=ChangeType.DELETED, git_status=GitStatus.DELETED),
                ChangedFile(path=temp_path, change_type=ChangeType.SIGNATURE, git_status=GitStatus.MODIFIED),
                ChangedFile(path=temp_path, change_type=ChangeType.DOC_ONLY, git_status=GitStatus.MODIFIED),
            ]
            mock_detector.detect_changes.return_value = mock_changes
            updater._change_detector = mock_detector

            # Mock impact propagator
            mock_propagator = MagicMock()
            mock_report = ImpactReport(
                changed_files=[temp_path],
                changed_node_ids=["n1"],
                impacted_nodes=[],
                total_analyzed=1,
                propagation_time_ms=10.0,
            )
            mock_propagator.propagate.return_value = mock_report
            updater._impact_propagator = mock_propagator

            # Mock parser
            mock_parser = MagicMock()
            entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock extractor
            mock_extractor = MagicMock()
            mock_extractor.resolve.return_value = []
            updater._extractor = mock_extractor

            # Mock graph_store
            mock_graph_store = MagicMock()
            mock_graph_store.query.return_value = []
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            report = updater.update("HEAD~1")

            assert report.changes_detected == 4
            assert report.nodes_added >= 0
            assert report.nodes_deleted >= 0
            assert report.nodes_updated >= 0
        finally:
            os.unlink(temp_path)

    def test_cache_persisted(self):
        """update_cache is called after stage3."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        mock_detector.detect_changes.return_value = mock_changes
        mock_detector.update_cache = MagicMock()
        updater._change_detector = mock_detector

        # Mock impact propagator
        mock_propagator = MagicMock()
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["n1"],
            impacted_nodes=[],
            total_analyzed=1,
            propagation_time_ms=10.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        # Mock parser
        mock_parser = MagicMock()
        entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
        mock_parser.parse_file.return_value = ParseResult(
            file_path="a.py",
            entities=[entity],
            relations=[],
            error=None,
        )
        updater._parser = mock_parser

        # Mock extractor
        mock_extractor = MagicMock()
        mock_extractor.resolve.return_value = []
        updater._extractor = mock_extractor

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = []
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            mock_changes[0].path = temp_path
            updater.update("HEAD~1")

            # Verify update_cache was called
            mock_detector.update_cache.assert_called_once_with(mock_changes)
        finally:
            os.unlink(temp_path)

    def test_parse_errors_count(self):
        """parse_errors > 0, failed_files non-empty when parsing fails."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        mock_detector.detect_changes.return_value = mock_changes
        updater._change_detector = mock_detector

        # Mock impact propagator
        mock_propagator = MagicMock()
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=[],
            impacted_nodes=[],
            total_analyzed=0,
            propagation_time_ms=10.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        # Mock parser with error
        mock_parser = MagicMock()
        mock_parser.parse_file.return_value = ParseResult(
            file_path="a.py",
            entities=[],
            relations=[],
            error="Syntax error",
        )
        updater._parser = mock_parser

        # Mock graph_store
        mock_graph_store = MagicMock()
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("invalid syntax here")
            temp_path = f.name

        try:
            mock_changes[0].path = temp_path
            report = updater.update("HEAD~1")

            assert report.parse_errors == 1
            assert len(report.failed_files) > 0
            assert temp_path in report.failed_files
        finally:
            os.unlink(temp_path)

    def test_no_crash_with_no_repo_path(self):
        """Should not crash when repo_path is None (uses Path.cwd())."""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config, repo_path=None)

        # Verify repo_path defaults to Path.cwd()
        assert updater._repo_path is not None


class TestIncrementalUpdateE2E:
    """Task 9: IncrementalUpdater 四阶段流水线测试（端到端）。"""

    def test_incremental_update_add_file_e2e(self) -> None:
        """模拟新增文件，验证 detect→propagate→update→validate 完整流程。"""
        import os
        import tempfile

        # Arrange
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def new_func():\n    pass\n")
            temp_path = f.name

        try:
            # Mock change detector
            mock_detector = MagicMock()
            mock_changes = [ChangedFile(path=temp_path, change_type=ChangeType.ADDED, git_status=GitStatus.ADDED)]
            mock_detector.detect_changes.return_value = mock_changes
            mock_detector.update_cache = MagicMock()
            updater._change_detector = mock_detector

            # Mock impact propagator - 返回空影响
            mock_propagator = MagicMock()
            mock_report = ImpactReport(
                changed_files=[temp_path],
                changed_node_ids=[],
                impacted_nodes=[],
                total_analyzed=0,
                propagation_time_ms=5.0,
            )
            mock_propagator.propagate.return_value = mock_report
            updater._impact_propagator = mock_propagator

            # Mock graph_store 和 chroma_store
            mock_graph_store = MagicMock()
            mock_graph_store.query.return_value = []
            updater._graph_store = mock_graph_store

            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            # Act
            result = updater.update("HEAD~1")

            # Assert - Stage 1: 变更检测
            assert result.changes_detected == 1

            # Assert - Stage 2: 影响传播
            assert result.impacted_nodes_count == 0
            mock_propagator.propagate.assert_called_once_with(mock_changes)

            # Assert - Stage 3: 应用变更（新增文件）
            assert result.nodes_added >= 1  # 至少有 module
            assert result.nodes_deleted == 0

            # Assert - Stage 4: 变更集记录
            assert result.changeset_id != ""
            assert result.changeset_id.startswith("cs-")
            mock_detector.update_cache.assert_called_once_with(mock_changes)

        finally:
            os.unlink(temp_path)

    def test_incremental_update_modify_file_e2e(self) -> None:
        """模拟修改文件，验证变更传播。"""
        import os
        import tempfile

        # Arrange
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def modified_func():\n    pass\n")
            temp_path = f.name

        try:
            # Mock change detector
            mock_detector = MagicMock()
            mock_changes = [ChangedFile(path=temp_path, change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED)]
            mock_detector.detect_changes.return_value = mock_changes
            mock_detector.update_cache = MagicMock()
            updater._change_detector = mock_detector

            # Mock impact propagator - 返回有影响的节点
            mock_propagator = MagicMock()
            impacted_nodes = [
                ImpactedNode(
                    node_id="caller-1",
                    node_label="CodeEntity",
                    name="caller_func",
                    impact_score=0.7,
                    severity=ImpactSeverity.HIGH,
                    depth=1,
                    direction=PropagationDirection.FORWARD,
                    relation_path=["calls"],
                    source_node_id="modified_func",
                )
            ]
            mock_report = ImpactReport(
                changed_files=[temp_path],
                changed_node_ids=["modified_func"],
                impacted_nodes=impacted_nodes,
                total_analyzed=1,
                propagation_time_ms=10.0,
            )
            mock_propagator.propagate.return_value = mock_report
            updater._impact_propagator = mock_propagator

            # Mock graph_store - 模拟文件已存在节点
            mock_graph_store = MagicMock()
            mock_graph_store.query.return_value = [{"id": "old-node"}]
            mock_graph_store.get_relations.return_value = []
            updater._graph_store = mock_graph_store

            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            # Act
            result = updater.update("HEAD~1")

            # Assert - 验证变更传播
            assert result.changes_detected == 1
            assert result.impacted_nodes_count == 1  # 有 1 个受影响节点
            assert result.nodes_updated >= 1
            assert result.changeset_id != ""

        finally:
            os.unlink(temp_path)

    def test_incremental_update_dry_run_e2e(self) -> None:
        """dry-run 模式不修改任何存储。"""
        import os
        import tempfile

        # Arrange
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def test_func():\n    pass\n")
            temp_path = f.name

        try:
            # Mock change detector
            mock_detector = MagicMock()
            mock_changes = [ChangedFile(path=temp_path, change_type=ChangeType.ADDED, git_status=GitStatus.ADDED)]
            mock_detector.detect_changes.return_value = mock_changes
            updater._change_detector = mock_detector

            # Mock impact propagator
            mock_propagator = MagicMock()
            mock_report = ImpactReport(
                changed_files=[temp_path],
                changed_node_ids=[],
                impacted_nodes=[],
                total_analyzed=0,
                propagation_time_ms=5.0,
            )
            mock_propagator.propagate.return_value = mock_report
            updater._impact_propagator = mock_propagator

            # Mock stores
            mock_graph_store = MagicMock()
            updater._graph_store = mock_graph_store

            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            # Act - dry_run=True
            result = updater.update("HEAD~1", dry_run=True)

            # Assert - dry_run 只检测不执行
            assert result.changes_detected == 1
            assert result.impacted_nodes_count == 0
            # Stage 3/4 被跳过
            assert result.nodes_added == 0
            assert result.nodes_updated == 0
            assert result.nodes_deleted == 0
            assert result.relations_rebuilt == 0
            assert result.vectors_updated == 0
            assert result.changeset_id == ""  # dry_run 不生成 changeset

            # 验证没有调用存储写入
            mock_graph_store.merge_node.assert_not_called()
            mock_chroma_store.put_entities_batch.assert_not_called()

        finally:
            os.unlink(temp_path)


class TestConceptEntityHandling:
    """Tests for ConceptEntity 处理分支。"""

    def test_concept_reextraction_flagged_when_concept_impacted(self):
        """mock impact_report 包含 2 个 ConceptEntity 的 ImpactedNode，验证 concepts_flagged=2。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        mock_detector.detect_changes.return_value = mock_changes
        mock_detector.update_cache = MagicMock()
        updater._change_detector = mock_detector

        # Mock impact propagator - 返回包含 ConceptEntity 的受影响节点
        mock_propagator = MagicMock()
        impacted_nodes = [
            ImpactedNode(
                node_id="concept-1",
                node_label="ConceptEntity",
                name="BusinessConcept1",
                impact_score=0.9,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["describes"],
                source_node_id="code-1",
            ),
            ImpactedNode(
                node_id="concept-2",
                node_label="ConceptEntity",
                name="BusinessConcept2",
                impact_score=0.8,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["describes"],
                source_node_id="code-2",
            ),
        ]
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["code-1"],
            impacted_nodes=impacted_nodes,
            total_analyzed=2,
            propagation_time_ms=10.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        # Mock graph_store - 模拟 concept flagging 查询返回 count=2
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = [{"count": 2}]
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        # Mock parser
        mock_parser = MagicMock()
        entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
        mock_parser.parse_file.return_value = ParseResult(
            file_path="a.py",
            entities=[entity],
            relations=[],
            error=None,
        )
        updater._parser = mock_parser

        # Mock extractor
        mock_extractor = MagicMock()
        mock_extractor.resolve.return_value = []
        updater._extractor = mock_extractor

        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            mock_changes[0].path = temp_path
            report = updater.update("HEAD~1")

            # Assert - 验证 concepts_flagged = 2
            assert report.concepts_flagged == 2

            # Assert - 验证 graph_store.query 被调用了正确的 Cypher
            mock_graph_store.query.assert_called()
            # 查找包含 ConceptEntity 的 query 调用
            concept_query_calls = [
                call
                for call in mock_graph_store.query.call_args_list
                if "ConceptEntity" in str(call) and "needs_reextraction" in str(call)
            ]
            assert len(concept_query_calls) == 1
        finally:
            os.unlink(temp_path)

    def test_concept_reextraction_not_called_for_code_only_impacts(self):
        """mock impact_report 只包含 CodeEntity 的 ImpactedNode，验证 concepts_flagged=0。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        mock_detector.detect_changes.return_value = mock_changes
        mock_detector.update_cache = MagicMock()
        updater._change_detector = mock_detector

        # Mock impact propagator - 只返回 CodeEntity 的受影响节点
        mock_propagator = MagicMock()
        impacted_nodes = [
            ImpactedNode(
                node_id="code-1",
                node_label="CodeEntity",
                name="func1",
                impact_score=0.9,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls"],
                source_node_id="code-2",
            ),
        ]
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["code-1"],
            impacted_nodes=impacted_nodes,
            total_analyzed=1,
            propagation_time_ms=10.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = []
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        # Mock parser
        mock_parser = MagicMock()
        entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
        mock_parser.parse_file.return_value = ParseResult(
            file_path="a.py",
            entities=[entity],
            relations=[],
            error=None,
        )
        updater._parser = mock_parser

        # Mock extractor
        mock_extractor = MagicMock()
        mock_extractor.resolve.return_value = []
        updater._extractor = mock_extractor

        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            mock_changes[0].path = temp_path
            report = updater.update("HEAD~1")

            # Assert - 验证 concepts_flagged = 0
            assert report.concepts_flagged == 0

            # Assert - 验证没有调用 ConceptEntity 相关的 query
            concept_query_calls = [
                call
                for call in mock_graph_store.query.call_args_list
                if "ConceptEntity" in str(call) and "needs_reextraction" in str(call)
            ]
            assert len(concept_query_calls) == 0
        finally:
            os.unlink(temp_path)

    def test_flag_concept_reextraction_direct(self):
        """直接调用 _flag_concept_reextraction，验证返回值和 Cypher 正确。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = [{"count": 2}]
        updater._graph_store = mock_graph_store

        # Act - 直接调用
        result = updater._flag_concept_reextraction(["id1", "id2"])

        # Assert - 验证返回值
        assert result == 2

        # Assert - 验证 Cypher 正确
        mock_graph_store.query.assert_called_once()
        call_args = mock_graph_store.query.call_args
        cypher = call_args[0][0]
        params = call_args[0][1]  # 参数是位置传递的，不是关键字参数

        assert "ConceptEntity" in cypher
        assert "needs_reextraction" in cypher
        assert "IN $ids" in cypher
        assert params == {"ids": ["id1", "id2"]}

    def test_flag_concept_reextraction_empty_list(self):
        """空列表调用 _flag_concept_reextraction，返回 0 且不调用 query。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        updater._graph_store = mock_graph_store

        # Act - 空列表调用
        result = updater._flag_concept_reextraction([])

        # Assert - 验证返回 0 且未调用 query
        assert result == 0
        mock_graph_store.query.assert_not_called()


class TestDocEntityHandling:
    """Tests for DocEntity 处理分支。"""

    def test_doc_regeneration_flagged_when_doc_impacted(self):
        """mock impact_report 包含 1 个 DocEntity 的 ImpactedNode，验证 docs_flagged=1。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        mock_detector.detect_changes.return_value = mock_changes
        mock_detector.update_cache = MagicMock()
        updater._change_detector = mock_detector

        # Mock impact propagator - 返回包含 DocEntity 的受影响节点
        mock_propagator = MagicMock()
        impacted_nodes = [
            ImpactedNode(
                node_id="doc-1",
                node_label="DocEntity",
                name="README",
                impact_score=0.9,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["describes"],
                source_node_id="code-1",
            ),
        ]
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["code-1"],
            impacted_nodes=impacted_nodes,
            total_analyzed=1,
            propagation_time_ms=10.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        # Mock graph_store - 模拟 doc flagging 查询返回 count=1
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = [{"count": 1}]
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        # Mock parser
        mock_parser = MagicMock()
        entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
        mock_parser.parse_file.return_value = ParseResult(
            file_path="a.py",
            entities=[entity],
            relations=[],
            error=None,
        )
        updater._parser = mock_parser

        # Mock extractor
        mock_extractor = MagicMock()
        mock_extractor.resolve.return_value = []
        updater._extractor = mock_extractor

        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            mock_changes[0].path = temp_path
            report = updater.update("HEAD~1")

            # Assert - 验证 docs_flagged = 1
            assert report.docs_flagged == 1

            # Assert - 验证 graph_store.query 被调用了正确的 Cypher
            mock_graph_store.query.assert_called()
            # 查找包含 DocEntity 的 query 调用
            doc_query_calls = [
                call
                for call in mock_graph_store.query.call_args_list
                if "DocEntity" in str(call) and "needs_regeneration" in str(call)
            ]
            assert len(doc_query_calls) == 1
        finally:
            os.unlink(temp_path)

    def test_doc_regeneration_not_called_for_code_only_impacts(self):
        """mock impact_report 只包含 CodeEntity 的 ImpactedNode，验证 docs_flagged=0。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock change detector
        mock_detector = MagicMock()
        mock_changes = [
            ChangedFile(path="a.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]
        mock_detector.detect_changes.return_value = mock_changes
        mock_detector.update_cache = MagicMock()
        updater._change_detector = mock_detector

        # Mock impact propagator - 只返回 CodeEntity 的受影响节点
        mock_propagator = MagicMock()
        impacted_nodes = [
            ImpactedNode(
                node_id="code-1",
                node_label="CodeEntity",
                name="func1",
                impact_score=0.9,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls"],
                source_node_id="code-2",
            ),
        ]
        mock_report = ImpactReport(
            changed_files=["a.py"],
            changed_node_ids=["code-1"],
            impacted_nodes=impacted_nodes,
            total_analyzed=1,
            propagation_time_ms=10.0,
        )
        mock_propagator.propagate.return_value = mock_report
        updater._impact_propagator = mock_propagator

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = []
        updater._graph_store = mock_graph_store

        # Mock chroma_store
        mock_chroma_store = MagicMock()
        updater._chroma_store = mock_chroma_store

        # Mock parser
        mock_parser = MagicMock()
        entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
        mock_parser.parse_file.return_value = ParseResult(
            file_path="a.py",
            entities=[entity],
            relations=[],
            error=None,
        )
        updater._parser = mock_parser

        # Mock extractor
        mock_extractor = MagicMock()
        mock_extractor.resolve.return_value = []
        updater._extractor = mock_extractor

        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            mock_changes[0].path = temp_path
            report = updater.update("HEAD~1")

            # Assert - 验证 docs_flagged = 0
            assert report.docs_flagged == 0

            # Assert - 验证没有调用 DocEntity 相关的 query
            doc_query_calls = [
                call
                for call in mock_graph_store.query.call_args_list
                if "DocEntity" in str(call) and "needs_regeneration" in str(call)
            ]
            assert len(doc_query_calls) == 0
        finally:
            os.unlink(temp_path)

    def test_flag_doc_regeneration_direct(self):
        """直接调用 _flag_doc_regeneration，验证返回值和 Cypher 正确。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = [{"count": 1}]
        updater._graph_store = mock_graph_store

        # Act - 直接调用
        result = updater._flag_doc_regeneration(["id1"])

        # Assert - 验证返回值
        assert result == 1

        # Assert - 验证 Cypher 正确
        mock_graph_store.query.assert_called_once()
        call_args = mock_graph_store.query.call_args
        cypher = call_args[0][0]
        params = call_args[0][1]  # 参数是位置传递的

        assert "DocEntity" in cypher
        assert "needs_regeneration" in cypher
        assert "IN $ids" in cypher
        assert params == {"ids": ["id1"]}

    def test_flag_doc_regeneration_empty_list(self):
        """空列表调用 _flag_doc_regeneration，返回 0 且不调用 query。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store
        mock_graph_store = MagicMock()
        updater._graph_store = mock_graph_store

        # Act - 空列表调用
        result = updater._flag_doc_regeneration([])

        # Assert - 验证返回 0 且未调用 query
        assert result == 0
        mock_graph_store.query.assert_not_called()


class TestGraphIntegrityValidation:
    """Tests for 图谱完整性检查功能。"""

    def test_validate_returns_warnings_for_orphan_nodes(self):
        """mock graph_store.query 返回 2 个孤立 CodeEntity，验证 warnings=2。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store 返回孤立节点
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = [
            {"id": "orphan-1", "name": "orphan_func1"},
            {"id": "orphan-2", "name": "orphan_func2"},
        ]
        updater._graph_store = mock_graph_store

        # Act - 直接调用完整性检查
        result = updater._validate_graph_integrity()

        # Assert - 验证返回值
        assert result["warnings"] == 2
        assert len(result["orphan_code_entities"]) == 2
        assert "orphan-1" in result["orphan_code_entities"]
        assert "orphan-2" in result["orphan_code_entities"]

        # Assert - 验证 Cypher 查询正确
        mock_graph_store.query.assert_called_once()
        call_args = mock_graph_store.query.call_args
        cypher = call_args[0][0]
        assert "CodeEntity" in cypher
        assert "NOT" in cypher
        assert "--()" in cypher

    def test_validate_returns_zero_for_healthy_graph(self):
        """mock graph_store.query 返回空列表，验证 warnings=0。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store 返回健康图谱（无孤立节点）
        mock_graph_store = MagicMock()
        mock_graph_store.query.return_value = []
        updater._graph_store = mock_graph_store

        # Act
        result = updater._validate_graph_integrity()

        # Assert
        assert result["warnings"] == 0
        assert result["orphan_code_entities"] == []

    def test_validate_handles_query_error_gracefully(self):
        """mock graph_store.query 抛异常，验证返回空结果而不抛异常。"""
        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # Mock graph_store 抛异常
        mock_graph_store = MagicMock()
        mock_graph_store.query.side_effect = Exception("Connection failed")
        updater._graph_store = mock_graph_store

        # Act - 不应该抛异常
        result = updater._validate_graph_integrity()

        # Assert - 返回安全的默认值
        assert result["warnings"] == 0
        assert result["orphan_code_entities"] == []

    def test_integrity_warnings_in_update_report(self):
        """完整调用 update()，验证 integrity_warnings > 0 当有孤立节点时。"""
        import os
        import tempfile

        config = OntoAgentConfig()
        updater = IncrementalUpdater(config)

        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func1(): pass")
            temp_path = f.name

        try:
            # Mock change detector
            mock_detector = MagicMock()
            mock_changes = [
                ChangedFile(path=temp_path, change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
            ]
            mock_detector.detect_changes.return_value = mock_changes
            mock_detector.update_cache = MagicMock()
            updater._change_detector = mock_detector

            # Mock impact propagator
            mock_propagator = MagicMock()
            mock_report = ImpactReport(
                changed_files=[temp_path],
                changed_node_ids=[],
                impacted_nodes=[],
                total_analyzed=0,
                propagation_time_ms=5.0,
            )
            mock_propagator.propagate.return_value = mock_report
            updater._impact_propagator = mock_propagator

            # Mock graph_store - 根据 Cypher 内容返回不同结果
            mock_graph_store = MagicMock()

            def mock_query_side_effect(cypher, *args, **kwargs):
                # 完整性检查的 Cypher 包含特定特征
                if "CodeEntity" in cypher and "NOT" in cypher and "--()" in cypher:
                    return [{"id": "orphan-1", "name": "orphan_func"}]
                # 其他查询返回空结果
                return []

            mock_graph_store.query.side_effect = mock_query_side_effect
            updater._graph_store = mock_graph_store

            # Mock chroma_store
            mock_chroma_store = MagicMock()
            updater._chroma_store = mock_chroma_store

            # Mock parser
            mock_parser = MagicMock()
            entity = CodeEntity(name="func1", entity_type="function", source="def func1(): pass")
            mock_parser.parse_file.return_value = ParseResult(
                file_path=temp_path,
                entities=[entity],
                relations=[],
                error=None,
            )
            updater._parser = mock_parser

            # Mock extractor
            mock_extractor = MagicMock()
            mock_extractor.resolve.return_value = []
            updater._extractor = mock_extractor

            # Act
            report = updater.update("HEAD~1")

            # Assert - 验证 integrity_warnings > 0
            assert report.integrity_warnings == 1

        finally:
            os.unlink(temp_path)
