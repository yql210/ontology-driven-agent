from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_mcp_components():
    """每个测试后重置组件缓存。"""
    from ontoagent.api import mcp_server

    yield
    mcp_server._reset_components()


def test_mcp_instance_exists():
    """测试 FastMCP 实例创建成功。"""
    from ontoagent.api.mcp_server import mcp

    assert mcp is not None
    assert mcp.name == "OntoAgent"


def test_reset_components():
    """测试 _reset_components 清空组件缓存。"""
    from ontoagent.api import mcp_server

    # 添加 mock 组件
    mcp_server._components["test"] = {"key": "value"}
    assert mcp_server._components == {"test": {"key": "value"}}

    # 调用 _reset_components
    mcp_server._reset_components()
    assert mcp_server._components == {}


def test_get_config():
    """测试 _get_config 返回 OntoAgentConfig 实例。"""
    from ontoagent.api import mcp_server
    from ontoagent.config import OntoAgentConfig

    # mock from_env 避免真实环境变量依赖
    config = OntoAgentConfig(
        neo4j_uri="bolt://test:7687",
        neo4j_user="test",
        neo4j_password="test",
        chroma_persist_dir=".chroma",
        ollama_base_url="http://test:11434",
    )

    mcp_server._components["config"] = config
    result = mcp_server._get_config()

    assert result is config
    assert result.neo4j_uri == "bolt://test:7687"


def test_get_neo4j_lazy():
    """测试 _get_neo4j 首次创建并缓存。"""
    from ontoagent.api import mcp_server
    from ontoagent.config import OntoAgentConfig

    # 注入测试配置
    config = OntoAgentConfig(
        neo4j_uri="bolt://test:7687",
        neo4j_user="test",
        neo4j_password="test",
    )
    mcp_server._components["config"] = config

    # mock Neo4jGraphStore 避免真实连接
    import unittest.mock

    with unittest.mock.patch("ontoagent.api.mcp_server.Neo4jGraphStore") as mock_neo4j:
        mock_instance = unittest.mock.MagicMock()
        mock_neo4j.return_value = mock_instance

        # 首次调用
        result1 = mcp_server._get_neo4j()
        assert result1 is mock_instance
        mock_neo4j.assert_called_once_with("bolt://test:7687", "test", "test")

        # 二次调用应返回缓存
        result2 = mcp_server._get_neo4j()
        assert result2 is result1
        assert mock_neo4j.call_count == 1


def test_reset_recreates():
    """测试 _reset_components 后重新创建组件。"""
    from ontoagent.api import mcp_server
    from ontoagent.config import OntoAgentConfig

    config = OntoAgentConfig(
        neo4j_uri="bolt://test:7687",
        neo4j_user="test",
        neo4j_password="test",
    )
    mcp_server._components["config"] = config

    import unittest.mock

    with unittest.mock.patch("ontoagent.api.mcp_server.Neo4jGraphStore") as mock_neo4j:
        # 首次创建
        mcp_server._get_neo4j()
        assert mock_neo4j.call_count == 1

        # 重置
        mcp_server._reset_components()
        # 需要重新注入配置
        mcp_server._components["config"] = config

        # 重新创建
        mcp_server._get_neo4j()
        # 应该再次调用 Neo4jGraphStore
        assert mock_neo4j.call_count == 2


def test_semantic_search_default():
    """测试 semantic_search 默认参数。"""
    # mock builder.query
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_builder = unittest.mock.MagicMock()
    mock_builder.query.return_value = [{"id": "1", "text": "foo", "metadata": {}, "distance": 0.1}]

    with unittest.mock.patch("ontoagent.api.mcp_server._get_builder", return_value=mock_builder):
        result = mcp_server.semantic_search("test query")

        # 验证参数传递：text 参数名是 text 不是 query
        mock_builder.query.assert_called_once_with("test query", n_results=10, entity_type=None)
        assert result == [{"id": "1", "text": "foo", "metadata": {}, "distance": 0.1}]


def test_semantic_search_custom_k():
    """测试 semantic_search 自定义 k 参数。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_builder = unittest.mock.MagicMock()
    mock_builder.query.return_value = []

    with unittest.mock.patch("ontoagent.api.mcp_server._get_builder", return_value=mock_builder):
        result = mcp_server.semantic_search("test", k=5)

        mock_builder.query.assert_called_once_with("test", n_results=5, entity_type=None)
        assert result == []


def test_semantic_search_with_entity_type():
    """测试 semantic_search 带实体类型过滤。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_builder = unittest.mock.MagicMock()
    mock_builder.query.return_value = [{"id": "2", "text": "bar", "metadata": {}, "distance": 0.2}]

    with unittest.mock.patch("ontoagent.api.mcp_server._get_builder", return_value=mock_builder):
        result = mcp_server.semantic_search("test", entity_type="function")

        mock_builder.query.assert_called_once_with("test", n_results=10, entity_type="function")
        assert result == [{"id": "2", "text": "bar", "metadata": {}, "distance": 0.2}]


def test_graph_query_basic():
    """测试 graph_query 基本查询。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()
    mock_neo4j.query.return_value = [{"id": "1", "name": "foo"}]

    with unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j):
        result = mcp_server.graph_query("MATCH (n) RETURN n")

        mock_neo4j.query.assert_called_once_with("MATCH (n) RETURN n")
        assert result == [{"id": "1", "name": "foo"}]


def test_graph_query_empty():
    """测试 graph_query 空结果。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()
    mock_neo4j.query.return_value = []

    with unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j):
        result = mcp_server.graph_query("MATCH (n) RETURN n")

        assert result == []


def test_impact_analysis_basic():
    """测试 impact_analysis 基本功能。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()
    # 模拟 Neo4j 返回影响路径
    mock_neo4j.query.return_value = [
        {
            "node": {"id": "func1", "name": "func1", "entityType": "function"},
            "target": {"id": "func2", "name": "func2", "entityType": "function"},
            "rel_type": "CALLS",
            "distance": 1,
        },
        {
            "node": {"id": "func1", "name": "func1", "entityType": "function"},
            "target": {"id": "class1", "name": "Class1", "entityType": "class"},
            "rel_type": "EXTENDS",
            "distance": 1,
        },
    ]

    with unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j):
        result = mcp_server.impact_analysis(entity_id="func1")

        # 验证返回格式
        assert result["entity"] == "func1"
        assert "impacted_entities" in result
        assert result["total_count"] == 2

        # 验证 Cypher 包含双向遍历
        call_args = mock_neo4j.query.call_args
        cypher = call_args[0][0]
        assert "MATCH" in cypher
        assert "entity_id" in cypher or "$entity_id" in cypher
        assert "*1..3" in cypher  # 可变长度路径（默认 depth=3）


def test_impact_analysis_with_depth():
    """测试 impact_analysis 自定义深度。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()
    mock_neo4j.query.return_value = []

    with unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j):
        result = mcp_server.impact_analysis(entity_id="func1", depth=2)

        # 验证 depth 参数传递到 Cypher
        call_args = mock_neo4j.query.call_args
        cypher = call_args[0][0]
        # depth 不能参数化，应该是字符串拼接
        assert "*1..2" in cypher
        assert result["impacted_entities"] == []


def test_impact_analysis_not_found():
    """测试 impact_analysis 实体不存在。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()
    mock_neo4j.query.return_value = []

    with unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j):
        result = mcp_server.impact_analysis(entity_id="nonexistent")

        assert result["entity"] == "nonexistent"
        assert result["impacted_entities"] == []
        assert result["total_count"] == 0


def test_get_context_basic():
    """测试 get_context 基本功能。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()
    mock_neo4j.get_node.return_value = {
        "id": "func1",
        "name": "func1",
        "entityType": "function",
        "filePath": "/path/to/file.py",
    }

    # 模拟 get_relations 对 source_id 和 target_id 的不同响应
    def mock_get_relations(source_id=None, target_id=None, rel_type=None):
        if source_id == "func1":
            return [
                {
                    "source_id": "func1",
                    "target_id": "func2",
                    "rel_type": "CALLS",
                    "properties": {},
                }
            ]
        return []

    mock_neo4j.get_relations.side_effect = mock_get_relations

    mock_chroma = unittest.mock.MagicMock()
    mock_chroma.search.return_value = [{"id": "func2", "text": "similar code", "metadata": {}, "distance": 0.3}]

    with (
        unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j),
        unittest.mock.patch("ontoagent.api.mcp_server._get_chroma", return_value=mock_chroma),
    ):
        result = mcp_server.get_context(entity_id="func1")

        # 验证返回格式
        assert result["node"]["id"] == "func1"
        assert result["node"]["name"] == "func1"
        assert len(result["relations"]) == 1
        assert result["relations"][0]["rel_type"] == "CALLS"
        assert len(result["similar"]) == 1
        assert result["similar"][0]["id"] == "func2"


def test_get_context_not_found():
    """测试 get_context 实体不存在。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()
    mock_neo4j.get_node.return_value = None
    mock_neo4j.get_relations.return_value = []

    mock_chroma = unittest.mock.MagicMock()
    mock_chroma.search.return_value = []

    with (
        unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j),
        unittest.mock.patch("ontoagent.api.mcp_server._get_chroma", return_value=mock_chroma),
    ):
        result = mcp_server.get_context(entity_id="nonexistent")

        assert result["node"] is None
        assert result["relations"] == []
        assert result["similar"] == []


def test_list_concepts():
    """测试 list_concepts 基本功能。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_aligner = unittest.mock.MagicMock()
    mock_aligner.list_concepts.return_value = [
        {
            "name": "UserAuthentication",
            "id": "concept-1",
            "aliases": ["auth", "login"],
            "entity_type": "business_concept",
        },
        {
            "name": "DataValidation",
            "id": "concept-2",
            "aliases": ["validation"],
            "entity_type": "business_concept",
        },
    ]

    with unittest.mock.patch("ontoagent.api.mcp_server._get_aligner", return_value=mock_aligner):
        result = mcp_server.list_concepts()

        mock_aligner.list_concepts.assert_called_once()
        assert len(result) == 2
        assert result[0]["name"] == "UserAuthentication"
        assert result[1]["name"] == "DataValidation"


def test_get_module_tree():
    """测试 get_module_tree 基本功能。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_clustering = unittest.mock.MagicMock()
    mock_clustering.get_module_tree.return_value = {
        "auth_module": {
            "entities": ["func1", "func2"],
            "cohesion": 0.85,
            "entity_count": 2,
        },
        "data_module": {
            "entities": ["class1", "class2"],
            "cohesion": 0.92,
            "entity_count": 2,
        },
    }

    with unittest.mock.patch("ontoagent.api.mcp_server._get_clustering", return_value=mock_clustering):
        result = mcp_server.get_module_tree()

        mock_clustering.get_module_tree.assert_called_once()
        assert "auth_module" in result
        assert result["auth_module"]["cohesion"] == 0.85
        assert result["data_module"]["entity_count"] == 2


def test_detect_changes_basic():
    """测试 detect_changes 基本功能。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    # 模拟 git diff 输出
    mock_result = unittest.mock.MagicMock()
    mock_result.stdout = b"M\tsrc/ontoagent/foo.py\nA\tsrc/ontoagent/bar.py\nD\tsrc/ontoagent/old.py\n"
    mock_result.returncode = 0

    with unittest.mock.patch("subprocess.run", return_value=mock_result):
        result = mcp_server.detect_changes(since="HEAD~1", repo_path=".")

        assert result["changed_files"] == 3
        assert result["modified"] == ["src/ontoagent/foo.py"]
        assert result["added"] == ["src/ontoagent/bar.py"]
        assert result["deleted"] == ["src/ontoagent/old.py"]


def test_detect_changes_error():
    """测试 detect_changes git 命令失败。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    # 模拟 git 命令失败
    mock_result = unittest.mock.MagicMock()
    mock_result.returncode = 128
    mock_result.stderr = b"fatal: not a git repository"

    with (
        unittest.mock.patch("subprocess.run", return_value=mock_result),
        pytest.raises(RuntimeError, match="Git command failed"),
    ):
        mcp_server.detect_changes(since="HEAD~1", repo_path=".")


def test_export_graph_json():
    """测试 export_graph JSON 格式。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()

    # 模拟节点查询结果
    def mock_query(cypher: str, params: dict | None = None):
        if "MATCH (n)" in cypher and "RETURN" in cypher:
            # 节点查询
            return [
                {
                    "id": "func1",
                    "name": "func1",
                    "labels": ["CodeEntity"],
                    "entityType": "function",
                },
                {
                    "id": "func2",
                    "name": "func2",
                    "labels": ["CodeEntity"],
                    "entityType": "function",
                },
            ]
        elif "MATCH ()-[r]->()" in cypher:
            # 关系查询
            return [
                {
                    "source": "func1",
                    "target": "func2",
                    "rel_type": "CALLS",
                    "properties": {},
                }
            ]
        return []

    mock_neo4j.query.side_effect = mock_query

    with unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j):
        result = mcp_server.export_graph(format="json")

        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["nodes"][0]["id"] == "func1"
        assert result["edges"][0]["source"] == "func1"
        assert result["edges"][0]["target"] == "func2"


def test_export_graph_empty():
    """测试 export_graph 空图。"""
    import unittest.mock

    from ontoagent.api import mcp_server

    mock_neo4j = unittest.mock.MagicMock()
    mock_neo4j.query.return_value = []

    with unittest.mock.patch("ontoagent.api.mcp_server._get_neo4j", return_value=mock_neo4j):
        result = mcp_server.export_graph(format="json")

        assert result["nodes"] == []
        assert result["edges"] == []


def test_serve_command_registered():
    """测试 serve 命令已注册到 CLI。"""
    from click.testing import CliRunner

    from ontoagent.api.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "serve" in result.output


def test_serve_options():
    """测试 serve 命令的选项。"""
    from click.testing import CliRunner

    from ontoagent.api.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--transport" in result.output
    assert "--port" in result.output


class TestToolRegistration:
    """测试 MCP 工具注册。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试前重置组件。"""
        from ontoagent.api import mcp_server

        mcp_server._reset_components()
        yield
        mcp_server._reset_components()

    def test_all_tools_registered(self):
        """验证 8 个工具都注册在 mcp 实例中。"""
        import asyncio

        from ontoagent.api.mcp_server import mcp

        expected_tools = {
            "semantic_search",
            "graph_query",
            "impact_analysis",
            "get_context",
            "list_concepts",
            "get_module_tree",
            "detect_changes",
            "export_graph",
        }

        tools = asyncio.run(mcp._local_provider.list_tools())
        tool_names = {t.name for t in tools}

        assert tool_names == expected_tools

    def test_tool_has_docstring(self):
        """验证每个工具函数都有 docstring。"""
        import asyncio

        from ontoagent.api.mcp_server import mcp

        tools = asyncio.run(mcp._local_provider.list_tools())

        for tool in tools:
            assert tool.fn.__doc__ is not None, f"Tool {tool.name} missing docstring"
            assert len(tool.fn.__doc__.strip()) > 0, f"Tool {tool.name} has empty docstring"

    def test_tool_decorator_applied(self):
        """验证工具函数有 FastMCP tool 标记。"""
        import asyncio

        from ontoagent.api.mcp_server import mcp

        tools = asyncio.run(mcp._local_provider.list_tools())

        for tool in tools:
            # FastMCP 装饰器会添加 __fastmcp__ 属性
            assert hasattr(tool.fn, "__fastmcp__"), f"Tool {tool.name} missing __fastmcp__ marker"
