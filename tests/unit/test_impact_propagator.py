from __future__ import annotations

from unittest.mock import Mock

import pytest

from layerkg.change_detector import ChangeType
from layerkg.impact_propagator import (
    DEFAULT_DECAY_SCHEDULE,
    DEFAULT_WEIGHT_MATRIX,
    ImpactedNode,
    ImpactPropagator,
    ImpactReport,
    ImpactSeverity,
    PropagationDirection,
)


class TestPropagationDirection:
    """Tests for PropagationDirection enum."""

    def test_forward_value_exists(self) -> None:
        """FORWARD should have value 'FORWARD'."""
        assert PropagationDirection.FORWARD.value == "FORWARD"

    def test_backward_value_exists(self) -> None:
        """BACKWARD should have value 'BACKWARD'."""
        assert PropagationDirection.BACKWARD.value == "BACKWARD"


class TestImpactSeverity:
    """Tests for ImpactSeverity enum."""

    def test_all_severities_exist(self) -> None:
        """All four severity levels should exist."""
        expected = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        actual = {s.value for s in ImpactSeverity}
        assert actual == expected

    def test_severity_values_are_strings(self) -> None:
        """Severity values should be strings."""
        for severity in ImpactSeverity:
            assert isinstance(severity.value, str)

    def test_from_score_critical(self) -> None:
        """Score >= 0.8 should be CRITICAL."""
        assert ImpactSeverity.from_score(0.8) == ImpactSeverity.CRITICAL
        assert ImpactSeverity.from_score(1.0) == ImpactSeverity.CRITICAL

    def test_from_score_high(self) -> None:
        """Score >= 0.5 should be HIGH."""
        assert ImpactSeverity.from_score(0.5) == ImpactSeverity.HIGH
        assert ImpactSeverity.from_score(0.79) == ImpactSeverity.HIGH

    def test_from_score_medium(self) -> None:
        """Score >= 0.2 should be MEDIUM."""
        assert ImpactSeverity.from_score(0.2) == ImpactSeverity.MEDIUM
        assert ImpactSeverity.from_score(0.49) == ImpactSeverity.MEDIUM

    def test_from_score_low(self) -> None:
        """Score < 0.2 should be LOW."""
        assert ImpactSeverity.from_score(0.0) == ImpactSeverity.LOW
        assert ImpactSeverity.from_score(0.19) == ImpactSeverity.LOW


class TestImpactedNode:
    """Tests for ImpactedNode dataclass."""

    def test_create_full_node(self) -> None:
        """Create ImpactedNode with all fields."""
        node = ImpactedNode(
            node_id="node-1",
            node_label="CodeEntity",
            name="function_foo",
            file_path="src/foo.py",
            impact_score=0.85,
            severity=ImpactSeverity.CRITICAL,
            depth=1,
            direction=PropagationDirection.FORWARD,
            relation_path=["calls"],
            source_node_id="source-1",
        )
        assert node.node_id == "node-1"
        assert node.node_label == "CodeEntity"
        assert node.name == "function_foo"
        assert node.file_path == "src/foo.py"

    def test_impact_score_in_valid_range(self) -> None:
        """impact_score should be in [0, 1]."""
        node = ImpactedNode(
            node_id="node-1",
            node_label="CodeEntity",
            name="foo",
            impact_score=0.5,
            severity=ImpactSeverity.MEDIUM,
            depth=1,
            direction=PropagationDirection.FORWARD,
            relation_path=[],
            source_node_id="source-1",
        )
        assert 0.0 <= node.impact_score <= 1.0

    def test_impact_score_at_boundaries(self) -> None:
        """impact_score can be 0.0 and 1.0."""
        for score in [0.0, 1.0]:
            node = ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=score,
                severity=ImpactSeverity.from_score(score),
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=[],
                source_node_id="source-1",
            )
            assert node.impact_score == score

    def test_depth_must_be_positive(self) -> None:
        """depth must be >= 1."""
        node = ImpactedNode(
            node_id="node-1",
            node_label="CodeEntity",
            name="foo",
            impact_score=0.5,
            severity=ImpactSeverity.MEDIUM,
            depth=1,
            direction=PropagationDirection.FORWARD,
            relation_path=[],
            source_node_id="source-1",
        )
        assert node.depth >= 1

    def test_source_node_id_required(self) -> None:
        """source_node_id should not be empty."""
        node = ImpactedNode(
            node_id="node-1",
            node_label="CodeEntity",
            name="foo",
            impact_score=0.5,
            severity=ImpactSeverity.MEDIUM,
            depth=1,
            direction=PropagationDirection.FORWARD,
            relation_path=[],
            source_node_id="source-1",
        )
        assert node.source_node_id == "source-1"

    def test_to_dict_output(self) -> None:
        """to_dict() should serialize all fields correctly."""
        node = ImpactedNode(
            node_id="node-1",
            node_label="CodeEntity",
            name="foo",
            file_path="src/foo.py",
            impact_score=0.85,
            severity=ImpactSeverity.CRITICAL,
            depth=2,
            direction=PropagationDirection.FORWARD,
            relation_path=["calls", "imports"],
            source_node_id="source-1",
        )
        result = node.to_dict()
        assert result["node_id"] == "node-1"
        assert result["node_label"] == "CodeEntity"
        assert result["name"] == "foo"
        assert result["file_path"] == "src/foo.py"
        assert result["impact_score"] == 0.85
        assert result["severity"] == "CRITICAL"
        assert result["depth"] == 2
        assert result["direction"] == "FORWARD"
        assert result["relation_path"] == ["calls", "imports"]
        assert result["source_node_id"] == "source-1"


class TestWeightMatrix:
    """Tests for DEFAULT_WEIGHT_MATRIX."""

    def test_contains_all_relation_types(self) -> None:
        """DEFAULT_WEIGHT_MATRIX should contain 8 relation types."""
        expected_types = {
            "calls",
            "implements",
            "extends",
            "imports",
            "semantic_impact",
            "describes",
            "derived_from",
            "affects",
        }
        actual_types = set(DEFAULT_WEIGHT_MATRIX.keys())
        assert actual_types == expected_types

    def test_all_relations_have_change_type_keys(self) -> None:
        """Each relation should have ADDED/DELETED/SIGNATURE/BODY/DOC_ONLY keys."""
        change_types = {"ADDED", "DELETED", "SIGNATURE", "BODY", "DOC_ONLY"}
        for _relation, weights in DEFAULT_WEIGHT_MATRIX.items():
            assert set(weights.keys()) == change_types

    def test_all_weights_in_valid_range(self) -> None:
        """All weight values should be in [0, 1]."""
        for relation, weights in DEFAULT_WEIGHT_MATRIX.items():
            for change_type, weight in weights.items():
                assert 0.0 <= weight <= 1.0, f"{relation}.{change_type} = {weight}"

    def test_unknown_relation_type_returns_zero(self) -> None:
        """Unknown relation types should default to 0.0."""
        # This is tested through the ImpactPropagator._compute_score method
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        # Unknown relation type should give 0.0 weight
        score = propagator._compute_score("unknown_relation", ChangeType.SIGNATURE, 1)
        assert score == 0.0

    def test_specific_weight_values(self) -> None:
        """Specific weight values should match the plan."""
        assert DEFAULT_WEIGHT_MATRIX["calls"]["DELETED"] == 1.0
        assert DEFAULT_WEIGHT_MATRIX["calls"]["ADDED"] == 0.9
        assert DEFAULT_WEIGHT_MATRIX["imports"]["DOC_ONLY"] == 0.0
        assert DEFAULT_WEIGHT_MATRIX["semantic_impact"]["SIGNATURE"] == 0.5


class TestDecaySchedule:
    """Tests for DEFAULT_DECAY_SCHEDULE."""

    def test_contains_depth_1_2_3(self) -> None:
        """DEFAULT_DECAY_SCHEDULE should contain depth 1, 2, 3."""
        expected = {1: 1.0, 2: 0.6, 3: 0.3}
        assert expected == DEFAULT_DECAY_SCHEDULE

    def test_depth_beyond_3_returns_zero(self) -> None:
        """Depth > 3 should return 0.0 (stop propagation)."""
        assert 4 not in DEFAULT_DECAY_SCHEDULE
        # This behavior is tested via _compute_score which uses get() with default 0.0

    def test_depth_0_not_in_schedule(self) -> None:
        """Depth 0 should not be in schedule."""
        assert 0 not in DEFAULT_DECAY_SCHEDULE


class TestImpactReport:
    """Tests for ImpactReport dataclass."""

    def test_create_report_with_impacted_nodes(self) -> None:
        """Create ImpactReport with impacted nodes."""
        nodes = [
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.9,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=[],
                source_node_id="source-1",
            ),
            ImpactedNode(
                node_id="node-2",
                node_label="CodeEntity",
                name="bar",
                impact_score=0.3,
                severity=ImpactSeverity.MEDIUM,
                depth=2,
                direction=PropagationDirection.BACKWARD,
                relation_path=[],
                source_node_id="source-1",
            ),
        ]
        report = ImpactReport(
            changed_files=["src/foo.py"],
            changed_node_ids=["source-1"],
            impacted_nodes=nodes,
            total_analyzed=10,
            propagation_time_ms=100.0,
        )
        assert len(report.impacted_nodes) == 2
        assert report.changed_files == ["src/foo.py"]

    def test_critical_count_property(self) -> None:
        """critical_count should count CRITICAL severity nodes."""
        nodes = [
            ImpactedNode(
                node_id=f"node-{i}",
                node_label="CodeEntity",
                name=f"func_{i}",
                impact_score=0.8 + i * 0.01,
                severity=ImpactSeverity.CRITICAL if i < 3 else ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=[],
                source_node_id="source-1",
            )
            for i in range(5)
        ]
        report = ImpactReport(
            changed_files=[],
            changed_node_ids=[],
            impacted_nodes=nodes,
            total_analyzed=5,
            propagation_time_ms=50.0,
        )
        assert report.critical_count == 3

    def test_affected_files_property(self) -> None:
        """affected_files should return unique set of file paths."""
        nodes = [
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.9,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=[],
                source_node_id="source-1",
                file_path="src/foo.py",
            ),
            ImpactedNode(
                node_id="node-2",
                node_label="CodeEntity",
                name="bar",
                impact_score=0.7,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=[],
                source_node_id="source-1",
                file_path="src/foo.py",
            ),
            ImpactedNode(
                node_id="node-3",
                node_label="CodeEntity",
                name="baz",
                impact_score=0.5,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=[],
                source_node_id="source-1",
                file_path="src/bar.py",
            ),
        ]
        report = ImpactReport(
            changed_files=["src/changed.py"],
            changed_node_ids=["source-1"],
            impacted_nodes=nodes,
            total_analyzed=3,
            propagation_time_ms=50.0,
        )
        assert report.affected_files == {"src/foo.py", "src/bar.py"}

    def test_nodes_by_severity_property(self) -> None:
        """nodes_by_severity should group nodes by severity."""
        nodes = [
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="critical",
                impact_score=0.9,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=[],
                source_node_id="source-1",
            ),
            ImpactedNode(
                node_id="node-2",
                node_label="CodeEntity",
                name="high",
                impact_score=0.6,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=[],
                source_node_id="source-1",
            ),
        ]
        report = ImpactReport(
            changed_files=[],
            changed_node_ids=[],
            impacted_nodes=nodes,
            total_analyzed=2,
            propagation_time_ms=50.0,
        )
        grouped = report.nodes_by_severity
        assert len(grouped[ImpactSeverity.CRITICAL]) == 1
        assert len(grouped[ImpactSeverity.HIGH]) == 1
        assert len(grouped[ImpactSeverity.MEDIUM]) == 0
        assert len(grouped[ImpactSeverity.LOW]) == 0

    def test_to_dict_output(self) -> None:
        """to_dict() should serialize report completely."""
        nodes = [
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.9,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls"],
                source_node_id="source-1",
                file_path="src/foo.py",
            )
        ]
        report = ImpactReport(
            changed_files=["src/foo.py"],
            changed_node_ids=["source-1"],
            impacted_nodes=nodes,
            total_analyzed=5,
            propagation_time_ms=100.0,
        )
        result = report.to_dict()
        assert result["changed_files"] == ["src/foo.py"]
        assert result["changed_node_ids"] == ["source-1"]
        assert len(result["impacted_nodes"]) == 1
        assert result["total_analyzed"] == 5
        assert result["propagation_time_ms"] == 100.0
        assert result["critical_count"] == 1


class TestImpactPropagatorComputeScore:
    """Tests for _compute_score internal method."""

    def test_calls_signature_depth_1(self) -> None:
        """calls + SIGNATURE + depth=1 → 0.9 × 1.0 = 0.9."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        score = propagator._compute_score("calls", ChangeType.SIGNATURE, 1)
        assert score == pytest.approx(0.9)

    def test_imports_body_depth_2(self) -> None:
        """imports + BODY + depth=2 → 0.3 × 0.6 = 0.18."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        score = propagator._compute_score("imports", ChangeType.BODY, 2)
        assert score == pytest.approx(0.18)

    def test_imports_doc_only_depth_1(self) -> None:
        """imports + DOC_ONLY + depth=1 → 0.0 × 1.0 = 0.0 (no propagation)."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        score = propagator._compute_score("imports", ChangeType.DOC_ONLY, 1)
        assert score == 0.0

    def test_describes_doc_only_depth_3(self) -> None:
        """describes + DOC_ONLY + depth=3 → 0.3 × 0.3 = 0.09."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        score = propagator._compute_score("describes", ChangeType.DOC_ONLY, 3)
        assert score == pytest.approx(0.09)

    def test_unknown_relation_type(self) -> None:
        """Unknown relation type → 0.0."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        score = propagator._compute_score("unknown", ChangeType.SIGNATURE, 1)
        assert score == 0.0

    def test_depth_4_returns_zero(self) -> None:
        """depth=4 → 0.0 (beyond schedule)."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        score = propagator._compute_score("calls", ChangeType.SIGNATURE, 4)
        assert score == 0.0

    def test_calls_deleted_depth_1(self) -> None:
        """calls + DELETED + depth=1 → 1.0 × 1.0 = 1.0."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        score = propagator._compute_score("calls", ChangeType.DELETED, 1)
        assert score == 1.0


class TestImpactPropagatorClassifySeverity:
    """Tests for _classify_severity internal method."""

    def test_score_0_9_is_critical(self) -> None:
        """Score 0.9 → CRITICAL."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        assert propagator._classify_severity(0.9) == ImpactSeverity.CRITICAL

    def test_score_0_54_is_high(self) -> None:
        """Score 0.54 → HIGH."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        assert propagator._classify_severity(0.54) == ImpactSeverity.HIGH

    def test_score_0_25_is_medium(self) -> None:
        """Score 0.25 → MEDIUM."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        assert propagator._classify_severity(0.25) == ImpactSeverity.MEDIUM

    def test_score_0_05_is_low(self) -> None:
        """Score 0.05 → LOW."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        assert propagator._classify_severity(0.05) == ImpactSeverity.LOW


class TestImpactPropagatorConstructor:
    """Tests for ImpactPropagator constructor."""

    def test_create_with_mock_graph_store(self) -> None:
        """Create ImpactPropagator with mock GraphStore."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store)
        assert propagator._graph_store is mock_store
        assert propagator._max_depth == 3
        assert propagator._impact_threshold == 0.05

    def test_max_depth_zero_raises_error(self) -> None:
        """max_depth=0 should raise ValueError."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        with pytest.raises(ValueError, match="max_depth must be positive"):
            ImpactPropagator(mock_store, max_depth=0)

    def test_custom_max_depth(self) -> None:
        """Custom max_depth should be stored."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store, max_depth=5)
        assert propagator._max_depth == 5

    def test_custom_threshold(self) -> None:
        """Custom impact_threshold should be stored."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        propagator = ImpactPropagator(mock_store, impact_threshold=0.1)
        assert propagator._impact_threshold == 0.1

    def test_custom_weight_matrix(self) -> None:
        """Custom weight_matrix should be stored."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        custom_weights = {"calls": {"ADDED": 0.5}}
        propagator = ImpactPropagator(mock_store, weight_matrix=custom_weights)
        assert propagator._weight_matrix == custom_weights

    def test_custom_decay_schedule(self) -> None:
        """Custom decay_schedule should be stored."""
        mock_store = Mock(spec=ImpactPropagator.__bases__)
        custom_decay = {1: 0.8}
        propagator = ImpactPropagator(mock_store, decay_schedule=custom_decay)
        assert propagator._decay_schedule == custom_decay


class TestMapFilesToNodes:
    """Tests for map_files_to_nodes method (Task 9)."""

    def test_single_file_mapping_success(self) -> None:
        """Single file mapping success (mock query returns nodes)."""
        from layerkg.change_detector import ChangedFile, GitStatus

        mock_store = Mock()
        mock_store.query.return_value = [
            {"id": "node-1", "name": "foo_func", "labels": ["CodeEntity"]},
            {"id": "node-2", "name": "bar_class", "labels": ["CodeEntity"]},
        ]

        propagator = ImpactPropagator(mock_store)
        changes = [
            ChangedFile(
                path="src/foo.py",
                change_type=ChangeType.BODY,
                git_status=GitStatus.MODIFIED,
            )
        ]

        result = propagator.map_files_to_nodes(changes)

        assert result == {"src/foo.py": ["node-1", "node-2"]}
        mock_store.query.assert_called_once()
        call_args = mock_store.query.call_args
        assert "file_path" in call_args[0][0] or "$fp" in call_args[0][0]

    def test_multiple_nodes_per_file(self) -> None:
        """Multiple nodes mapping (one file has multiple functions/classes)."""
        from layerkg.change_detector import ChangedFile, GitStatus

        mock_store = Mock()
        mock_store.query.return_value = [
            {"id": "node-1", "name": "func_a", "labels": ["CodeEntity"]},
            {"id": "node-2", "name": "func_b", "labels": ["CodeEntity"]},
            {"id": "node-3", "name": "class_c", "labels": ["CodeEntity"]},
        ]

        propagator = ImpactPropagator(mock_store)
        changes = [
            ChangedFile(
                path="src/big_file.py",
                change_type=ChangeType.BODY,
                git_status=GitStatus.MODIFIED,
            )
        ]

        result = propagator.map_files_to_nodes(changes)

        assert result == {"src/big_file.py": ["node-1", "node-2", "node-3"]}

    def test_file_not_in_graph_returns_empty_dict(self) -> None:
        """File not in graph → returns empty dict (no error)."""
        from layerkg.change_detector import ChangedFile, GitStatus

        mock_store = Mock()
        mock_store.query.return_value = []

        propagator = ImpactPropagator(mock_store)
        changes = [
            ChangedFile(
                path="src/nonexistent.py",
                change_type=ChangeType.ADDED,
                git_status=GitStatus.ADDED,
            )
        ]

        result = propagator.map_files_to_nodes(changes)

        assert result == {}

    def test_multiple_files_batch_mapping(self) -> None:
        """Multiple files batch mapping."""
        from layerkg.change_detector import ChangedFile, GitStatus

        mock_store = Mock()

        def mock_query(cypher: str, params: dict | None = None) -> list[dict]:
            fp = params.get("fp") if params else ""
            if fp == "src/a.py":
                return [{"id": "node-a", "name": "a", "labels": ["CodeEntity"]}]
            if fp == "src/b.py":
                return [
                    {"id": "node-b1", "name": "b1", "labels": ["CodeEntity"]},
                    {"id": "node-b2", "name": "b2", "labels": ["CodeEntity"]},
                ]
            return []

        mock_store.query.side_effect = mock_query

        propagator = ImpactPropagator(mock_store)
        changes = [
            ChangedFile(path="src/a.py", change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED),
            ChangedFile(path="src/b.py", change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED),
            ChangedFile(path="src/c.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED),
        ]

        result = propagator.map_files_to_nodes(changes)

        assert result == {
            "src/a.py": ["node-a"],
            "src/b.py": ["node-b1", "node-b2"],
        }
        assert mock_store.query.call_count == 3


class TestMergeImpacts:
    """Tests for _merge_impacts method (Task 10)."""

    def test_same_node_same_direction_takes_max_score(self) -> None:
        """Same node same direction takes MAX score."""
        mock_store = Mock()
        propagator = ImpactPropagator(mock_store)

        impacts = [
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.5,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls"],
                source_node_id="source-1",
            ),
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.8,
                severity=ImpactSeverity.CRITICAL,
                depth=2,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls", "imports"],
                source_node_id="source-1",
            ),
        ]

        result = propagator._merge_impacts(impacts)

        assert len(result) == 1
        assert result[0].node_id == "node-1"
        assert result[0].impact_score == 0.8
        assert result[0].direction == PropagationDirection.FORWARD

    def test_different_nodes_preserved(self) -> None:
        """Different nodes each preserved."""
        mock_store = Mock()
        propagator = ImpactPropagator(mock_store)

        impacts = [
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.5,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls"],
                source_node_id="source-1",
            ),
            ImpactedNode(
                node_id="node-2",
                node_label="CodeEntity",
                name="bar",
                impact_score=0.7,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls"],
                source_node_id="source-1",
            ),
        ]

        result = propagator._merge_impacts(impacts)

        assert len(result) == 2
        node_ids = {n.node_id for n in result}
        assert node_ids == {"node-1", "node-2"}

    def test_same_node_different_directions_preserved(self) -> None:
        """Same node different directions each preserved."""
        mock_store = Mock()
        propagator = ImpactPropagator(mock_store)

        impacts = [
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.6,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls"],
                source_node_id="source-1",
            ),
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.4,
                severity=ImpactSeverity.MEDIUM,
                depth=1,
                direction=PropagationDirection.BACKWARD,
                relation_path=["calls"],
                source_node_id="source-1",
            ),
        ]

        result = propagator._merge_impacts(impacts)

        assert len(result) == 2
        directions = {(n.node_id, n.direction) for n in result}
        assert directions == {("node-1", PropagationDirection.FORWARD), ("node-1", PropagationDirection.BACKWARD)}

    def test_empty_list_returns_empty(self) -> None:
        """Empty list → empty list."""
        mock_store = Mock()
        propagator = ImpactPropagator(mock_store)

        result = propagator._merge_impacts([])

        assert result == []

    def test_multi_source_changes_same_node(self) -> None:
        """Multi-source changes: two source_node_ids affect same node."""
        mock_store = Mock()
        propagator = ImpactPropagator(mock_store)

        impacts = [
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.7,
                severity=ImpactSeverity.HIGH,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["calls"],
                source_node_id="source-1",
            ),
            ImpactedNode(
                node_id="node-1",
                node_label="CodeEntity",
                name="foo",
                impact_score=0.9,
                severity=ImpactSeverity.CRITICAL,
                depth=1,
                direction=PropagationDirection.FORWARD,
                relation_path=["imports"],
                source_node_id="source-2",
            ),
        ]

        result = propagator._merge_impacts(impacts)

        # Since source_node_id is not part of the merge key, these are different impacts
        # But our merge key is (node_id, direction), so only highest score is kept
        assert len(result) == 1
        assert result[0].impact_score == 0.9
        assert result[0].source_node_id == "source-2"


class TestForwardBFS:
    """Tests for forward BFS single-hop propagation (Task 11)."""

    def test_forward_bfs_single_hop(self) -> None:
        """Forward BFS single-hop with mocked GraphStore returning neighbors."""
        mock_store = Mock()
        mock_store.get_relations.return_value = [
            {"source_id": "caller-1", "target_id": "source-1", "rel_type": "CALLS", "properties": {}},
            {"source_id": "caller-2", "target_id": "source-1", "rel_type": "CALLS", "properties": {}},
        ]
        mock_store.get_node.side_effect = lambda nid: {
            "caller-1": {"id": "caller-1", "name": "caller_func", "file_path": "src/caller.py", "label": "CodeEntity"},
            "caller-2": {
                "id": "caller-2",
                "name": "another_caller",
                "file_path": "src/another.py",
                "label": "CodeEntity",
            },
        }.get(nid)

        propagator = ImpactPropagator(mock_store)

        result = propagator._bidirectional_bfs(
            "source-1",
            ChangeType.SIGNATURE,
            PropagationDirection.FORWARD,
        )

        assert len(result) == 2
        assert all(n.depth == 1 for n in result)
        assert all(n.direction == PropagationDirection.FORWARD for n in result)
        assert all(n.source_node_id == "source-1" for n in result)
        assert result[0].relation_path == ["calls"]
        # Verify initial call was for source-1
        first_call_kwargs = mock_store.get_relations.call_args_list[0][1]
        assert first_call_kwargs.get("target_id") == "source-1"

    def test_zero_weight_relation_not_included(self) -> None:
        """weight × decay = 0 relations not included."""
        mock_store = Mock()
        mock_store.get_relations.return_value = [
            {"source_id": "node-1", "target_id": "source-1", "rel_type": "IMPORTS", "properties": {}},
        ]
        mock_store.get_node.return_value = {
            "id": "node-1",
            "name": "importer",
            "file_path": "src/importer.py",
            "label": "CodeEntity",
        }

        propagator = ImpactPropagator(mock_store)

        # imports + DOC_ONLY = 0.0 weight
        result = propagator._bidirectional_bfs(
            "source-1",
            ChangeType.DOC_ONLY,
            PropagationDirection.FORWARD,
        )

        assert len(result) == 0

    def test_score_below_threshold_not_included(self) -> None:
        """score < threshold nodes not included."""
        mock_store = Mock()
        mock_store.get_relations.return_value = [
            {"source_id": "node-1", "target_id": "source-1", "rel_type": "CALLS", "properties": {}},
        ]
        mock_store.get_node.return_value = {
            "id": "node-1",
            "name": "callee",
            "file_path": "src/callee.py",
            "label": "CodeEntity",
        }

        # Set threshold to 0.95, but calls+SIGNATURE+depth=1 = 0.9
        propagator = ImpactPropagator(mock_store, impact_threshold=0.95)

        result = propagator._bidirectional_bfs(
            "source-1",
            ChangeType.SIGNATURE,
            PropagationDirection.FORWARD,
        )

        assert len(result) == 0

    def test_circular_reference_no_infinite_loop(self) -> None:
        """Circular reference does not cause infinite loop (visited set)."""
        mock_store = Mock()

        # Setup: source-1 → caller-1 → source-1 (circular)
        call_count = {"count": 0}

        def mock_get_relations(**kwargs) -> list[dict]:
            call_count["count"] += 1
            if call_count["count"] == 1:
                # First call: source-1's callers
                return [{"source_id": "caller-1", "target_id": "source-1", "rel_type": "CALLS", "properties": {}}]
            elif call_count["count"] == 2:
                # Second call: caller-1's targets (includes source-1)
                return [{"source_id": "caller-1", "target_id": "source-1", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations
        mock_store.get_node.return_value = {
            "id": "caller-1",
            "name": "caller_func",
            "file_path": "src/caller.py",
            "label": "CodeEntity",
        }

        propagator = ImpactPropagator(mock_store)

        propagator._bidirectional_bfs(
            "source-1",
            ChangeType.SIGNATURE,
            PropagationDirection.BACKWARD,
        )

        # Should terminate and not infinite loop
        assert call_count["count"] <= 3

    def test_score_equal_to_threshold_included(self) -> None:
        """score == threshold → included (>= semantics)."""
        mock_store = Mock()
        mock_store.get_relations.return_value = [
            {"source_id": "node-1", "target_id": "source-1", "rel_type": "CALLS", "properties": {}},
        ]
        mock_store.get_node.return_value = {
            "id": "node-1",
            "name": "caller",
            "file_path": "src/caller.py",
            "label": "CodeEntity",
        }

        # calls+SIGNATURE+depth=1 = 0.9, set threshold to 0.9
        propagator = ImpactPropagator(mock_store, impact_threshold=0.9)

        result = propagator._bidirectional_bfs(
            "source-1",
            ChangeType.SIGNATURE,
            PropagationDirection.FORWARD,
        )

        assert len(result) == 1
        assert result[0].impact_score == pytest.approx(0.9)


class TestBackwardBFS:
    """Tests for backward BFS single-hop propagation (Task 12)."""

    def test_backward_queries_source_id(self) -> None:
        """Backward BFS queries get_relations(source_id=node_id)."""
        mock_store = Mock()
        mock_store.get_relations.return_value = [
            {"source_id": "source-1", "target_id": "dep-1", "rel_type": "CALLS", "properties": {}},
        ]
        mock_store.get_node.return_value = {
            "id": "dep-1",
            "name": "dependency",
            "file_path": "src/dep.py",
            "label": "CodeEntity",
        }

        propagator = ImpactPropagator(mock_store)

        result = propagator._bidirectional_bfs(
            "source-1",
            ChangeType.SIGNATURE,
            PropagationDirection.BACKWARD,
        )

        assert len(result) == 1
        assert result[0].node_id == "dep-1"
        # Verify initial call was for source-1
        first_call_kwargs = mock_store.get_relations.call_args_list[0][1]
        assert first_call_kwargs.get("source_id") == "source-1"

    def test_backward_neighbor_is_target_id(self) -> None:
        """Backward BFS neighbor is rel's target_id."""
        mock_store = Mock()
        mock_store.get_relations.return_value = [
            {"source_id": "source-1", "target_id": "dep-1", "rel_type": "CALLS", "properties": {}},
            {"source_id": "source-1", "target_id": "dep-2", "rel_type": "IMPORTS", "properties": {}},
        ]
        mock_store.get_node.side_effect = lambda nid: {
            "dep-1": {"id": "dep-1", "name": "dep1", "file_path": "src/dep1.py", "label": "CodeEntity"},
            "dep-2": {"id": "dep-2", "name": "dep2", "file_path": "src/dep2.py", "label": "CodeEntity"},
        }.get(nid)

        propagator = ImpactPropagator(mock_store)

        result = propagator._bidirectional_bfs(
            "source-1",
            ChangeType.SIGNATURE,
            PropagationDirection.BACKWARD,
        )

        assert len(result) == 2
        node_ids = {n.node_id for n in result}
        assert node_ids == {"dep-1", "dep-2"}

    def test_backward_direction_set_correctly(self) -> None:
        """Backward BFS sets ImpactedNode.direction = BACKWARD."""
        mock_store = Mock()
        mock_store.get_relations.return_value = [
            {"source_id": "source-1", "target_id": "dep-1", "rel_type": "CALLS", "properties": {}},
        ]
        mock_store.get_node.return_value = {
            "id": "dep-1",
            "name": "dep",
            "file_path": "src/dep.py",
            "label": "CodeEntity",
        }

        propagator = ImpactPropagator(mock_store)

        result = propagator._bidirectional_bfs(
            "source-1",
            ChangeType.SIGNATURE,
            PropagationDirection.BACKWARD,
        )

        assert len(result) == 1
        assert result[0].direction == PropagationDirection.BACKWARD


class TestMultiHopBFS:
    """Tests for multi-hop BFS with depth decay (Task 13)."""

    def test_depth_1_higher_than_depth_2_score(self) -> None:
        """depth=1 score > depth=2 score (decay working)."""
        mock_store = Mock()

        # Setup a chain: node-1 → node-2 → node-3
        call_count = {"count": 0}

        def mock_get_relations(**kwargs) -> list[dict]:
            call_count["count"] += 1
            target_id = kwargs.get("target_id")

            if target_id == "node-1":
                return [{"source_id": "node-2", "target_id": "node-1", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-2":
                return [{"source_id": "node-3", "target_id": "node-2", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations
        mock_store.get_node.side_effect = lambda nid: {
            "node-2": {"id": "node-2", "name": "n2", "file_path": "src/n2.py", "label": "CodeEntity"},
            "node-3": {"id": "node-3", "name": "n3", "file_path": "src/n3.py", "label": "CodeEntity"},
        }.get(nid)

        propagator = ImpactPropagator(mock_store)

        result = propagator._bidirectional_bfs(
            "node-1",
            ChangeType.SIGNATURE,
            PropagationDirection.FORWARD,
        )

        # depth=1: 0.9 * 1.0 = 0.9
        # depth=2: 0.9 * 0.6 = 0.54
        assert len(result) == 2
        depth_1_node = next(n for n in result if n.depth == 1)
        depth_2_node = next(n for n in result if n.depth == 2)
        assert depth_1_node.impact_score == pytest.approx(0.9)
        assert depth_2_node.impact_score == pytest.approx(0.54)

    def test_depth_3_still_propagates(self) -> None:
        """depth=3 still propagates (decay=0.3)."""
        mock_store = Mock()

        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")
            if target_id == "node-1":
                return [{"source_id": "node-2", "target_id": "node-1", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-2":
                return [{"source_id": "node-3", "target_id": "node-2", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-3":
                return [{"source_id": "node-4", "target_id": "node-3", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations
        mock_store.get_node.side_effect = lambda nid: {
            "node-2": {"id": "node-2", "name": "n2", "file_path": "n2.py", "label": "CodeEntity"},
            "node-3": {"id": "node-3", "name": "n3", "file_path": "n3.py", "label": "CodeEntity"},
            "node-4": {"id": "node-4", "name": "n4", "file_path": "n4.py", "label": "CodeEntity"},
        }.get(nid)

        propagator = ImpactPropagator(mock_store)

        result = propagator._bidirectional_bfs(
            "node-1",
            ChangeType.SIGNATURE,
            PropagationDirection.FORWARD,
        )

        # depth=3: 0.9 * 0.3 = 0.27 > threshold(0.05)
        assert len(result) == 3
        assert any(n.depth == 3 for n in result)

    def test_depth_4_stops_propagation(self) -> None:
        """depth=4 stops propagation (beyond schedule)."""
        mock_store = Mock()

        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")
            if target_id == "node-1":
                return [{"source_id": "node-2", "target_id": "node-1", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-2":
                return [{"source_id": "node-3", "target_id": "node-2", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-3":
                return [{"source_id": "node-4", "target_id": "node-3", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-4":
                return [{"source_id": "node-5", "target_id": "node-4", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations
        mock_store.get_node.side_effect = lambda nid: {
            "node-2": {"id": "node-2", "name": "n2", "file_path": "n2.py", "label": "CodeEntity"},
            "node-3": {"id": "node-3", "name": "n3", "file_path": "n3.py", "label": "CodeEntity"},
            "node-4": {"id": "node-4", "name": "n4", "file_path": "n4.py", "label": "CodeEntity"},
        }.get(nid)

        propagator = ImpactPropagator(mock_store)

        result = propagator._bidirectional_bfs(
            "node-1",
            ChangeType.SIGNATURE,
            PropagationDirection.FORWARD,
        )

        # depth=4 not in schedule, should stop at depth=3
        assert len(result) == 3
        assert all(n.depth <= 3 for n in result)

    def test_early_stop_when_frontier_empty(self) -> None:
        """Early stop: when frontier is empty."""
        mock_store = Mock()
        mock_store.get_relations.return_value = []
        mock_store.get_node.return_value = None

        propagator = ImpactPropagator(mock_store)

        result = propagator._bidirectional_bfs(
            "isolated-node",
            ChangeType.SIGNATURE,
            PropagationDirection.FORWARD,
        )

        assert result == []

    def test_relation_path_multi_hop_accumulates(self) -> None:
        """relation_path multi-hop accumulates: depth=2 has path length 2."""
        mock_store = Mock()

        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")
            if target_id == "node-1":
                return [{"source_id": "node-2", "target_id": "node-1", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-2":
                return [{"source_id": "node-3", "target_id": "node-2", "rel_type": "IMPORTS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations
        mock_store.get_node.side_effect = lambda nid: {
            "node-2": {"id": "node-2", "name": "n2", "file_path": "n2.py", "label": "CodeEntity"},
            "node-3": {"id": "node-3", "name": "n3", "file_path": "n3.py", "label": "CodeEntity"},
        }.get(nid)

        propagator = ImpactPropagator(mock_store)

        result = propagator._bidirectional_bfs(
            "node-1",
            ChangeType.SIGNATURE,
            PropagationDirection.FORWARD,
        )

        depth_2_node = next((n for n in result if n.depth == 2), None)
        assert depth_2_node is not None
        assert depth_2_node.relation_path == ["calls", "imports"]


class TestComputeImpact:
    """Tests for compute_impact complete flow (Task 14)."""

    def test_bidirectional_propagation_merged(self) -> None:
        """Bidirectional propagation merged."""
        mock_store = Mock()

        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")
            source_id = kwargs.get("source_id")

            if target_id == "source-1":
                # Forward: who calls source-1
                return [{"source_id": "caller", "target_id": "source-1", "rel_type": "CALLS", "properties": {}}]
            if source_id == "source-1":
                # Backward: what source-1 calls
                return [{"source_id": "source-1", "target_id": "callee", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations
        mock_store.get_node.side_effect = lambda nid: {
            "caller": {"id": "caller", "name": "caller_func", "file_path": "caller.py", "label": "CodeEntity"},
            "callee": {"id": "callee", "name": "callee_func", "file_path": "callee.py", "label": "CodeEntity"},
        }.get(nid)

        propagator = ImpactPropagator(mock_store)

        result = propagator.compute_impact(["source-1"], ChangeType.SIGNATURE)

        assert len(result) == 2
        directions = {n.direction for n in result}
        assert directions == {PropagationDirection.FORWARD, PropagationDirection.BACKWARD}

    def test_source_node_not_in_results(self) -> None:
        """Source node itself not in results."""
        mock_store = Mock()
        mock_store.get_relations.return_value = []
        mock_store.get_node.return_value = None

        propagator = ImpactPropagator(mock_store)

        result = propagator.compute_impact(["source-1"], ChangeType.SIGNATURE)

        assert result == []
        assert "source-1" not in [n.node_id for n in result]

    def test_added_file_no_graph_nodes_returns_empty(self) -> None:
        """ADDED type file (no graph nodes) → empty results."""
        mock_store = Mock()
        mock_store.get_relations.return_value = []
        mock_store.get_node.return_value = None

        propagator = ImpactPropagator(mock_store)

        result = propagator.compute_impact([], ChangeType.ADDED)

        assert result == []

    def test_signature_propagates_wider_than_doc_only(self) -> None:
        """SIGNATURE change propagates wider than DOC_ONLY."""
        mock_store = Mock()

        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")
            if target_id == "source-1":
                return [{"source_id": "caller", "target_id": "source-1", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations
        mock_store.get_node.return_value = {
            "id": "caller",
            "name": "caller",
            "file_path": "caller.py",
            "label": "CodeEntity",
        }

        propagator = ImpactPropagator(mock_store)

        result_sig = propagator.compute_impact(["source-1"], ChangeType.SIGNATURE)
        result_doc = propagator.compute_impact(["source-1"], ChangeType.DOC_ONLY)

        # SIGNATURE: 0.9 * 1.0 = 0.9
        # DOC_ONLY: 0.1 * 1.0 = 0.1
        assert len(result_sig) == 1
        assert len(result_doc) == 1
        assert result_sig[0].impact_score > result_doc[0].impact_score

    def test_deleted_change_propagates(self) -> None:
        """DELETED change propagates when graph has nodes."""
        mock_store = Mock()

        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")
            if target_id == "deleted-node":
                return [{"source_id": "caller", "target_id": "deleted-node", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations
        mock_store.get_node.return_value = {
            "id": "caller",
            "name": "caller",
            "file_path": "caller.py",
            "label": "CodeEntity",
        }

        propagator = ImpactPropagator(mock_store)

        result = propagator.compute_impact(["deleted-node"], ChangeType.DELETED)

        # DELETED weight is highest: 1.0 * 1.0 = 1.0
        assert len(result) == 1
        assert result[0].impact_score == pytest.approx(1.0)


class TestPropagate:
    """Tests for propagate main entry point (Task 15)."""

    def test_full_flow_changed_file_to_impact_report(self) -> None:
        """Full flow: ChangedFile → map → BFS → ImpactReport."""
        from layerkg.change_detector import ChangedFile, GitStatus

        mock_store = Mock()

        # map_files_to_nodes phase
        mock_store.query.return_value = [
            {"id": "node-1", "name": "changed_func", "labels": ["CodeEntity"]},
        ]

        # BFS phase
        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")
            if target_id == "node-1":
                return [{"source_id": "caller", "target_id": "node-1", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations
        mock_store.get_node.return_value = {
            "id": "caller",
            "name": "caller_func",
            "file_path": "src/caller.py",
            "label": "CodeEntity",
        }

        propagator = ImpactPropagator(mock_store)
        changes = [
            ChangedFile(path="src/changed.py", change_type=ChangeType.SIGNATURE, git_status=GitStatus.MODIFIED),
        ]

        result = propagator.propagate(changes)

        assert isinstance(result, ImpactReport)
        assert result.changed_files == ["src/changed.py"]
        assert result.changed_node_ids == ["node-1"]
        assert len(result.impacted_nodes) == 1
        assert result.impacted_nodes[0].node_id == "caller"

    def test_propagation_time_ms_positive(self) -> None:
        """propagation_time_ms > 0."""
        from layerkg.change_detector import ChangedFile, GitStatus

        mock_store = Mock()
        mock_store.query.return_value = [{"id": "node-1", "name": "f", "labels": ["CodeEntity"]}]
        mock_store.get_relations.return_value = []
        mock_store.get_node.return_value = None

        propagator = ImpactPropagator(mock_store)
        changes = [ChangedFile(path="src/f.py", change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED)]

        result = propagator.propagate(changes)

        assert result.propagation_time_ms >= 0

    def test_total_analyzed_count(self) -> None:
        """total_analyzed counts correctly."""
        from layerkg.change_detector import ChangedFile, GitStatus

        mock_store = Mock()

        # 2 files mapped to 3 nodes total
        def mock_query(cypher: str, params: dict | None = None) -> list[dict]:
            fp = params.get("fp") if params else ""
            if fp == "src/a.py":
                return [
                    {"id": "node-a1", "name": "a1", "labels": ["CodeEntity"]},
                    {"id": "node-a2", "name": "a2", "labels": ["CodeEntity"]},
                ]
            if fp == "src/b.py":
                return [{"id": "node-b", "name": "b", "labels": ["CodeEntity"]}]
            return []

        mock_store.query.side_effect = mock_query
        mock_store.get_relations.return_value = []
        mock_store.get_node.return_value = None

        propagator = ImpactPropagator(mock_store)
        changes = [
            ChangedFile(path="src/a.py", change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED),
            ChangedFile(path="src/b.py", change_type=ChangeType.BODY, git_status=GitStatus.MODIFIED),
        ]

        result = propagator.propagate(changes)

        # total_analyzed = number of unique source nodes = 3
        assert result.total_analyzed == 3

    def test_empty_graph_returns_empty_impacted_nodes(self) -> None:
        """Empty graph: GraphStore returns empty → impacted_nodes empty."""
        from layerkg.change_detector import ChangedFile, GitStatus

        mock_store = Mock()
        mock_store.query.return_value = []  # No nodes found
        mock_store.get_relations.return_value = []
        mock_store.get_node.return_value = None

        propagator = ImpactPropagator(mock_store)
        changes = [ChangedFile(path="src/new.py", change_type=ChangeType.ADDED, git_status=GitStatus.ADDED)]

        result = propagator.propagate(changes)

        assert result.impacted_nodes == []
        assert result.changed_node_ids == []
        assert result.total_analyzed == 0

    def test_empty_changes_list_returns_zero_counts(self) -> None:
        """Empty changes list: [] → all counts = 0."""
        mock_store = Mock()

        propagator = ImpactPropagator(mock_store)

        result = propagator.propagate([])

        assert result.changed_files == []
        assert result.changed_node_ids == []
        assert result.impacted_nodes == []
        assert result.total_analyzed == 0
        assert result.propagation_time_ms >= 0


class TestPropagationAlgorithmE2E:
    """Task 7: ImpactPropagator 算法流程验证（端到端）。"""

    def test_linear_chain_propagation_e2e(self) -> None:
        """A→B→C→D 链式传播，验证影响范围和深度衰减。"""
        from layerkg.change_detector import ChangedFile, GitStatus

        # Arrange: 构建链式图 A -> B -> C -> D
        mock_store = Mock()

        # map_files_to_nodes 阶段：返回 A 节点
        mock_store.query.return_value = [{"id": "node-a", "name": "A", "labels": ["CodeEntity"]}]

        # BFS 阶段：构建链式关系
        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")
            kwargs.get("source_id")  # consumed by interface

            if target_id == "node-a":
                # A 的 callers（反向）：谁调用 A
                return [{"source_id": "node-b", "target_id": "node-a", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-b":
                return [{"source_id": "node-c", "target_id": "node-b", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-c":
                return [{"source_id": "node-d", "target_id": "node-c", "rel_type": "CALLS", "properties": {}}]
            # 其他情况返回空
            return []

        mock_store.get_relations.side_effect = mock_get_relations

        def mock_get_node(node_id: str) -> dict | None:
            return {
                "node-b": {"id": "node-b", "name": "B", "file_path": "b.py", "label": "CodeEntity"},
                "node-c": {"id": "node-c", "name": "C", "file_path": "c.py", "label": "CodeEntity"},
                "node-d": {"id": "node-d", "name": "D", "file_path": "d.py", "label": "CodeEntity"},
            }.get(node_id)

        mock_store.get_node.side_effect = mock_get_node

        propagator = ImpactPropagator(mock_store)
        changes = [
            ChangedFile(path="a.py", change_type=ChangeType.SIGNATURE, git_status=GitStatus.MODIFIED)
        ]

        # Act
        result = propagator.propagate(changes)

        # Assert
        # depth=1 (B): 0.9 * 1.0 = 0.9
        # depth=2 (C): 0.9 * 0.6 = 0.54
        # depth=3 (D): 0.9 * 0.3 = 0.27
        assert len(result.impacted_nodes) == 3
        assert result.changed_node_ids == ["node-a"]

        # 验证深度衰减：depth 1 分数 > depth 2 > depth 3
        nodes_by_depth = {n.depth: n for n in result.impacted_nodes}
        assert nodes_by_depth[1].impact_score == pytest.approx(0.9)
        assert nodes_by_depth[2].impact_score == pytest.approx(0.54)
        assert nodes_by_depth[3].impact_score == pytest.approx(0.27)

    def test_diamond_propagation_e2e(self) -> None:
        """菱形依赖 A→B, A→C, B→D, C→D，验证 D 不被重复计算。"""
        from layerkg.change_detector import ChangedFile, GitStatus

        # Arrange: 构建菱形图
        #     A
        #    / \
        #   B   C
        #    \ /
        #     D
        mock_store = Mock()

        mock_store.query.return_value = [{"id": "node-a", "name": "A", "labels": ["CodeEntity"]}]

        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")

            if target_id == "node-a":
                # A 的 callers
                return [
                    {"source_id": "node-b", "target_id": "node-a", "rel_type": "CALLS", "properties": {}},
                    {"source_id": "node-c", "target_id": "node-a", "rel_type": "CALLS", "properties": {}},
                ]
            if target_id == "node-b":
                return [{"source_id": "node-d", "target_id": "node-b", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-c":
                return [{"source_id": "node-d", "target_id": "node-c", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations

        def mock_get_node(node_id: str) -> dict | None:
            return {
                "node-b": {"id": "node-b", "name": "B", "file_path": "b.py", "label": "CodeEntity"},
                "node-c": {"id": "node-c", "name": "C", "file_path": "c.py", "label": "CodeEntity"},
                "node-d": {"id": "node-d", "name": "D", "file_path": "d.py", "label": "CodeEntity"},
            }.get(node_id)

        mock_store.get_node.side_effect = mock_get_node

        propagator = ImpactPropagator(mock_store)
        changes = [
            ChangedFile(path="a.py", change_type=ChangeType.SIGNATURE, git_status=GitStatus.MODIFIED)
        ]

        # Act
        result = propagator.propagate(changes)

        # Assert
        # 应该有 3 个受影响节点: B, C, D（D 只出现一次）
        assert len(result.impacted_nodes) == 3

        # D 只应该出现一次，且分数取两条路径的最大值
        d_nodes = [n for n in result.impacted_nodes if n.name == "D"]
        assert len(d_nodes) == 1
        # D 的分数是 depth=2: 0.9 * 0.6 = 0.54
        assert d_nodes[0].impact_score == pytest.approx(0.54)

    def test_max_depth_cutoff_e2e(self) -> None:
        """构建深度 > max_depth 的链，验证传播在 max_depth 处停止。"""
        from layerkg.change_detector import ChangedFile, GitStatus

        # Arrange: 构建超长链 A -> B -> C -> D -> E (depth 4 超过 max_depth=3)
        mock_store = Mock()

        mock_store.query.return_value = [{"id": "node-a", "name": "A", "labels": ["CodeEntity"]}]

        def mock_get_relations(**kwargs) -> list[dict]:
            target_id = kwargs.get("target_id")

            if target_id == "node-a":
                return [{"source_id": "node-b", "target_id": "node-a", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-b":
                return [{"source_id": "node-c", "target_id": "node-b", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-c":
                return [{"source_id": "node-d", "target_id": "node-c", "rel_type": "CALLS", "properties": {}}]
            if target_id == "node-d":
                # 这个关系会导致 depth=4，应该被 cutoff
                return [{"source_id": "node-e", "target_id": "node-d", "rel_type": "CALLS", "properties": {}}]
            return []

        mock_store.get_relations.side_effect = mock_get_relations

        def mock_get_node(node_id: str) -> dict | None:
            return {
                "node-b": {"id": "node-b", "name": "B", "file_path": "b.py", "label": "CodeEntity"},
                "node-c": {"id": "node-c", "name": "C", "file_path": "c.py", "label": "CodeEntity"},
                "node-d": {"id": "node-d", "name": "D", "file_path": "d.py", "label": "CodeEntity"},
                "node-e": {"id": "node-e", "name": "E", "file_path": "e.py", "label": "CodeEntity"},
            }.get(node_id)

        mock_store.get_node.side_effect = mock_get_node

        propagator = ImpactPropagator(mock_store, max_depth=3)
        changes = [
            ChangedFile(path="a.py", change_type=ChangeType.SIGNATURE, git_status=GitStatus.MODIFIED)
        ]

        # Act
        result = propagator.propagate(changes)

        # Assert
        # 只应传播到 depth=3 (D)，E (depth=4) 不应被包含
        impacted_names = {n.name for n in result.impacted_nodes}
        assert "B" in impacted_names
        assert "C" in impacted_names
        assert "D" in impacted_names
        assert "E" not in impacted_names  # E 应该被 cutoff

        # 验证最大深度
        max_depth_reached = max(n.depth for n in result.impacted_nodes)
        assert max_depth_reached == 3
