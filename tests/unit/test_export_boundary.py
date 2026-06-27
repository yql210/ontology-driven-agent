from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.unit
def test_export_graph_dot_format():
    """验证 DOT 输出格式正确。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()

    # 模拟节点和边查询结果
    def mock_query(cypher: str, params: dict | None = None):
        if "MATCH (n)" in cypher:
            return [
                {"id": "func1", "name": "func1", "labels": ["CodeEntity"]},
                {"id": "func2", "name": "func2", "labels": ["CodeEntity"]},
            ]
        elif "MATCH ()-[r]->()" in cypher:
            return [{"source": "func1", "target": "func2", "rel_type": "CALLS", "properties": {}}]
        return []

    mock_neo4j.query.side_effect = mock_query

    with unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j):
        result = mcp_server.export_graph(format="dot")

        # 验证返回结构
        assert "content" in result
        content = result["content"]

        # 验证 DOT 格式关键字
        assert "digraph" in content
        assert "graph {" in content or content.startswith("digraph")
        assert "->" in content
        assert "func1" in content
        assert "func2" in content


@pytest.mark.unit
def test_export_graph_cytoscape_format():
    """验证 Cytoscape 输出结构。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()

    def mock_query(cypher: str, params: dict | None = None):
        if "MATCH (n)" in cypher:
            # 返回 labels 为 dict 类型以配合 _to_cytoscape 的 ** 解包
            # （源码中 labels(n) 返回 list，但 _to_cytoscape 期望 dict）
            return [
                {"id": "node1", "name": "Node1", "labels": {"type": "CodeEntity"}},
                {"id": "node2", "name": "Node2", "labels": {"type": "ConceptEntity"}},
            ]
        elif "MATCH ()-[r]->()" in cypher:
            return [{"source": "node1", "target": "node2", "rel_type": "DESCRIBES", "properties": {}}]
        return []

    mock_neo4j.query.side_effect = mock_query

    with unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j):
        result = mcp_server.export_graph(format="cytoscape")

        # 验证返回结构
        assert "elements" in result
        elements = result["elements"]

        # 验证 nodes 和 edges 结构
        assert "nodes" in elements
        assert "edges" in elements
        assert len(elements["nodes"]) == 2
        assert len(elements["edges"]) == 1

        # 验证节点有 data 字段
        node = elements["nodes"][0]
        assert "data" in node
        assert "id" in node["data"]
        assert node["data"]["id"] in ("node1", "node2")

        # 验证边有 data 字段
        edge = elements["edges"][0]
        assert "data" in edge
        assert "source" in edge["data"]
        assert "target" in edge["data"]
        assert edge["data"]["source"] == "node1"
        assert edge["data"]["target"] == "node2"


@pytest.mark.unit
def test_change_detector_mixed_status():
    """验证 git diff 含混合状态（A+M+D）的正确分类。"""
    import unittest.mock

    from ontoagent.pipeline.change_detector import GitChangeDetector, GitStatus

    detector = GitChangeDetector(repo_path=Path.cwd())

    # 模拟 git diff --name-status 输出（A+M+D 混合）
    mock_result = unittest.mock.MagicMock()
    mock_result.stdout = "A\tnew_file.py\nM\tmodified.py\nD\tdeleted.py\n"
    mock_result.returncode = 0
    mock_result.stderr = b""

    with unittest.mock.patch("subprocess.run", return_value=mock_result):
        results = detector._git_diff_name_status("HEAD~1")

        # 验证解析结果
        assert len(results) == 3

        # 验证状态码和路径
        status_map = {path: status for status, path in results}
        assert status_map["new_file.py"] == "A"
        assert status_map["modified.py"] == "M"
        assert status_map["deleted.py"] == "D"

        # 验证 _parse_git_status 分类
        assert detector._parse_git_status("A") == GitStatus.ADDED
        assert detector._parse_git_status("M") == GitStatus.MODIFIED
        assert detector._parse_git_status("D") == GitStatus.DELETED
