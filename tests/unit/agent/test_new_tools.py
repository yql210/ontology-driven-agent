"""新工具单元测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


def test_impact_analysis_entity_not_found() -> None:
    """impact_analysis 实体不存在时返回错误"""
    with patch("layerkg.agent.tools.get_neo4j") as mock_fn:
        mock_neo4j = MagicMock()
        mock_neo4j.query.return_value = []
        mock_fn.return_value = mock_neo4j

        from layerkg.agent.tools import impact_analysis

        result = impact_analysis.invoke({"entity_name": "不存在"})
        data = json.loads(result)
        assert "error" in data


def test_impact_analysis_calls_propagator() -> None:
    """impact_analysis 调用 ImpactPropagator.compute_impact"""
    with (
        patch("layerkg.agent.tools.get_neo4j") as mock_neo4j_fn,
        patch("layerkg.agent.tools.get_impact_propagator") as mock_prop_fn,
    ):
        mock_neo4j = MagicMock()
        mock_neo4j.query.return_value = [{"id": "test-123"}]
        mock_neo4j_fn.return_value = mock_neo4j

        mock_impact = MagicMock()
        mock_impact.to_dict.return_value = {"node_id": "x", "name": "y"}
        mock_propagator = MagicMock()
        mock_propagator.compute_impact.return_value = [mock_impact]
        mock_prop_fn.return_value = mock_propagator

        from layerkg.agent.tools import impact_analysis

        result = impact_analysis.invoke({"entity_name": "test"})
        data = json.loads(result)
        assert data["source"] == "test"
        assert data["total_count"] == 1
        mock_propagator.compute_impact.assert_called_once()


def test_get_context_returns_json() -> None:
    """get_context 返回 node + relations + similar"""
    with (
        patch("layerkg.agent.tools.get_neo4j") as mock_neo4j_fn,
        patch("layerkg.agent.tools.get_chroma") as mock_chroma_fn,
    ):
        mock_neo4j = MagicMock()
        mock_neo4j.query.return_value = [{"id": "test-123"}]
        mock_neo4j.get_node.return_value = {"name": "test", "entity_type": "function"}
        mock_neo4j.get_relations.return_value = [{"type": "CALLS", "target": "other"}]
        mock_neo4j_fn.return_value = mock_neo4j

        mock_chroma = MagicMock()
        mock_chroma.search.return_value = [{"name": "similar1"}]
        mock_chroma_fn.return_value = mock_chroma

        from layerkg.agent.tools import get_context

        result = get_context.invoke({"entity_name": "test"})
        data = json.loads(result)
        assert "node" in data
        assert "relations" in data


def test_list_concepts_calls_aligner() -> None:
    """list_concepts 调用 ConceptAligner"""
    with patch("layerkg.agent.tools.get_aligner") as mock_fn:
        mock_aligner = MagicMock()
        mock_aligner.list_concepts.return_value = [{"name": "test-concept"}]
        mock_fn.return_value = mock_aligner

        from layerkg.agent.tools import list_concepts

        result = list_concepts.invoke({})
        data = json.loads(result)
        assert len(data) == 1
        mock_aligner.list_concepts.assert_called_once()


def test_export_graph_returns_json() -> None:
    """export_graph 返回 nodes + edges"""
    with patch("layerkg.agent.tools.get_neo4j") as mock_fn:
        mock_neo4j = MagicMock()
        mock_neo4j.query.side_effect = [
            [{"id": "1", "name": "A", "labels": ["CodeEntity"]}],
            [{"source": "1", "target": "2", "type": "CALLS", "properties": {}}],
        ]
        mock_fn.return_value = mock_neo4j

        from layerkg.agent.tools import export_graph

        result = export_graph.invoke({"limit": 10})
        data = json.loads(result)
        assert data["node_count"] == 1
        assert data["edge_count"] == 1


def test_all_tools_count() -> None:
    """ALL_TOOLS 包含 8 个工具"""
    from layerkg.agent.tools import ALL_TOOLS

    assert len(ALL_TOOLS) == 8
