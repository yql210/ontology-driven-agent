"""测试 tools.py 的工具封装"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ontoagent.agent.tools import ALL_TOOLS, graph_query, semantic_search


def test_all_tools_defined() -> None:
    """ALL_TOOLS 长度为 10，包含所有工具"""
    assert len(ALL_TOOLS) == 10
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
        "express_intent",
        "check_operation",
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

    with patch("ontoagent.agent.tools.get_chroma", return_value=mock_chroma):
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

    with patch("ontoagent.agent.tools.get_neo4j", return_value=mock_neo4j):
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

    with patch("ontoagent.agent.tools.get_neo4j", return_value=mock_neo4j):
        result = graph_query.invoke({"cypher": "INVALID"})

        # 验证返回的错误信息
        assert isinstance(result, str)
        assert "错误" in result or "error" in result.lower()
        assert "Syntax error" in result


def test_semantic_search_default_top_k() -> None:
    """semantic_search 使用默认 top_k=5"""
    mock_chroma = MagicMock()
    mock_chroma.search.return_value = []

    with patch("ontoagent.agent.tools.get_chroma", return_value=mock_chroma):
        semantic_search.invoke({"query": "test"})

        # 验证使用默认值 5
        mock_chroma.search.assert_called_once_with(query_text="test", n_results=5)


def test_get_action_executor_has_guard_pipeline() -> None:
    """Verify that _get_action_executor wires a guard_pipeline to ActionExecutor."""
    from ontoagent.agent.tools import _get_action_executor

    # Reset the global singleton to force re-initialization
    import ontoagent.agent.tools as tools_module

    tools_module._action_executor = None
    tools_module._function_runner = None

    mock_graph_store = MagicMock()

    # Mock the constraints.yaml to exist with valid content
    mock_constraints_data = {
        "traversal_constraints": {
            "data_sensitivity": {
                "name": "data_sensitivity",
                "source_label": "CodeEntity",
                "relation_chain": ["PROCESSES_DATA"],
                "target_label": "DataAsset",
                "collect_property": "sensitivity",
                "value_mapping": {"restricted": "block", "confidential": "warn", "internal": "allow", "public": "allow"},
                "aggregation": "max",
            }
        },
        "propagation_rules": {
            "upstream_risk": {
                "name": "upstream_risk",
                "along": ["CALLS"],
                "direction": "backward",
                "max_depth": 5,
                "collect_property": "entryCategory",
                "value_mapping": {"http_api": "warn"},
                "aggregation": "exists",
            }
        },
    }

    with patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(mock_constraints_data)
        mock_open.return_value.__enter__.return_value.__iter__.return_value = json.dumps(mock_constraints_data).splitlines()

        from pathlib import Path
        from unittest.mock import mock_open as mock_open_func

        import yaml

        # Use a YAML mock path - create a temp approach
        with patch.object(Path, "exists", return_value=True):
            with patch("yaml.safe_load", return_value=mock_constraints_data):
                executor = _get_action_executor(mock_graph_store)

    # Verify guard pipeline is wired
    assert executor._guard_pipeline is not None, "guard_pipeline should be wired to ActionExecutor"
    guard_names = [type(g).__name__ for g in executor._guard_pipeline.guards]
    assert "EntityExistsGuard" in guard_names, f"Expected EntityExistsGuard in {guard_names}"
    assert "EntityPropertyGuard" in guard_names, f"Expected EntityPropertyGuard in {guard_names}"
    assert "OntologyTraversalGuard" in guard_names, f"Expected OntologyTraversalGuard in {guard_names}"
    assert "OntologyPropagationGuard" in guard_names, f"Expected OntologyPropagationGuard in {guard_names}"
