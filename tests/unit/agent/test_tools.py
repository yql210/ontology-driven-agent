"""测试 tools.py 的工具封装"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from layerkg.agent.tools import ALL_TOOLS, graph_query, semantic_search


def test_all_tools_defined() -> None:
    """ALL_TOOLS 长度为 8，包含所有工具"""
    assert len(ALL_TOOLS) == 9
    tool_names = {t.name for t in ALL_TOOLS}
    expected_tools = {
        "semantic_search",
        "graph_query",
        "impact_analysis",
        "get_context",
        "list_concepts",
        "get_module_tree",
        "detect_changes",
        "export_graph",
        "ontology_action",
    }
    assert tool_names == expected_tools


def test_semantic_search_returns_json() -> None:
    """semantic_search 返回 JSON 格式，验证 chroma.search 被正确调用"""
    mock_chroma = MagicMock()
    mock_results = [
        {"id": "1", "metadata": {"name": "foo"}, "distance": 0.1},
        {"id": "2", "metadata": {"name": "bar"}, "distance": 0.2},
    ]
    mock_chroma.search.return_value = mock_results

    with patch("layerkg.agent.tools.get_chroma", return_value=mock_chroma):
        result = semantic_search.invoke({"query": "test query", "top_k": 5})

        # 验证 chroma.search 被正确调用
        mock_chroma.search.assert_called_once_with(query_text="test query", n_results=5)

        # 验证返回结果是 JSON 字符串
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == mock_results


def test_graph_query_returns_json() -> None:
    """graph_query 返回 JSON 格式，验证 neo4j.query 被正确调用"""
    mock_neo4j = MagicMock()
    mock_results = [{"a.name": "foo", "b.name": "bar"}]
    mock_neo4j.query.return_value = mock_results

    with patch("layerkg.agent.tools.get_neo4j", return_value=mock_neo4j):
        result = graph_query.invoke({"cypher": "MATCH (n) RETURN n"})

        # 验证 neo4j.query 被正确调用
        mock_neo4j.query.assert_called_once_with("MATCH (n) RETURN n")

        # 验证返回结果是 JSON 字符串
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed == mock_results


def test_graph_query_handles_error() -> None:
    """graph_query 处理异常，验证错误信息包含错误关键字"""
    mock_neo4j = MagicMock()
    mock_neo4j.query.side_effect = Exception("Syntax error")

    with patch("layerkg.agent.tools.get_neo4j", return_value=mock_neo4j):
        result = graph_query.invoke({"cypher": "INVALID"})

        # 验证返回的错误信息
        assert isinstance(result, str)
        assert "错误" in result or "error" in result.lower()
        assert "Syntax error" in result


def test_semantic_search_default_top_k() -> None:
    """semantic_search 使用默认 top_k=5"""
    mock_chroma = MagicMock()
    mock_chroma.search.return_value = []

    with patch("layerkg.agent.tools.get_chroma", return_value=mock_chroma):
        semantic_search.invoke({"query": "test"})

        # 验证使用默认值 5
        mock_chroma.search.assert_called_once_with(query_text="test", n_results=5)
