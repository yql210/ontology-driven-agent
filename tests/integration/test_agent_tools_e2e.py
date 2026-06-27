"""工具集成测试 — 验证工具→底层模块→结果的完整调用链"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from layerkg.pipeline.change_detector import ChangeType


class TestImpactAnalysisIntegration:
    """impact_analysis 集成测试"""

    def test_exact_match_to_propagator(self):
        """精确名称匹配 → ImpactPropagator.compute_impact"""
        with (
            patch("layerkg.agent.tools.get_neo4j") as mock_neo4j_fn,
            patch("layerkg.agent.tools.get_impact_propagator") as mock_prop_fn,
        ):
            mock_neo4j = MagicMock()
            mock_neo4j.query.return_value = [{"id": "abc-123"}]
            mock_neo4j_fn.return_value = mock_neo4j

            mock_impact = MagicMock()
            mock_impact.to_dict.return_value = {
                "node_id": "x",
                "name": "Y",
                "impact_score": 0.7,
                "severity": "HIGH",
            }
            mock_prop = MagicMock()
            mock_prop.compute_impact.return_value = [mock_impact]
            mock_prop_fn.return_value = mock_prop

            from layerkg.agent.tools import impact_analysis

            result = json.loads(impact_analysis.invoke({"entity_name": "Test"}))

            assert result["source"] == "Test"
            assert result["total_count"] == 1
            mock_prop.compute_impact.assert_called_once_with(["abc-123"], ChangeType.BODY)

    def test_fuzzy_match_suggestions(self):
        """模糊匹配返回建议列表"""
        with patch("layerkg.agent.tools.get_neo4j") as mock_fn:
            mock_neo4j = MagicMock()
            mock_neo4j.query.side_effect = [
                [],  # 精确匹配无结果
                [
                    {"id": "1", "name": "TestFunc"},
                    {"id": "2", "name": "TestClass"},
                ],  # 模糊匹配
            ]
            mock_fn.return_value = mock_neo4j

            from layerkg.agent.tools import impact_analysis

            result = json.loads(impact_analysis.invoke({"entity_name": "Test"}))

            assert "error" in result
            assert len(result["suggestions"]) == 2


class TestGetContextIntegration:
    """get_context 集成测试"""

    def test_bidirectional_relations(self):
        """get_relations 被调用两次（outgoing + incoming）"""
        with (
            patch("layerkg.agent.tools.get_neo4j") as mock_neo4j_fn,
            patch("layerkg.agent.tools.get_chroma") as mock_chroma_fn,
        ):
            mock_neo4j = MagicMock()
            mock_neo4j.query.return_value = [{"id": "n1"}]
            mock_neo4j.get_node.return_value = {"name": "F", "entity_type": "function"}
            mock_neo4j.get_relations.return_value = [{"type": "CALLS", "target_id": "n2"}]
            mock_neo4j_fn.return_value = mock_neo4j
            mock_chroma_fn.return_value = MagicMock(search=MagicMock(return_value=[]))

            from layerkg.agent.tools import get_context

            json.loads(get_context.invoke({"entity_name": "F"}))

            # 验证 get_relations 被调两次：source_id= + target_id=
            calls = mock_neo4j.get_relations.call_args_list
            assert len(calls) == 2
            assert calls[0].kwargs.get("source_id") == "n1"
            assert calls[1].kwargs.get("target_id") == "n1"


class TestExportGraphIntegration:
    """export_graph 集成测试"""

    def test_two_queries_separate(self):
        """节点查询和边查询分别调用"""
        with patch("layerkg.agent.tools.get_neo4j") as mock_fn:
            mock_neo4j = MagicMock()
            mock_neo4j.query.side_effect = [
                [{"id": "1", "name": "A", "labels": ["CodeEntity"]}],
                [
                    {
                        "source": "1",
                        "target": "2",
                        "type": "CALLS",
                        "properties": {},
                    }
                ],
            ]
            mock_fn.return_value = mock_neo4j

            from layerkg.agent.tools import export_graph

            result = json.loads(export_graph.invoke({"limit": 5}))

            assert result["node_count"] == 1
            assert result["edge_count"] == 1
            assert mock_neo4j.query.call_count == 2
