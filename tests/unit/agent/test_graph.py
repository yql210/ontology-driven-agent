"""测试 graph.py 的状态图构建"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_agent_state_is_messages_state() -> None:
    """验证 AgentState 是 MessagesState 子类"""
    from layerkg.agent.graph import AgentState

    # MessagesState 是一个 TypedDict，不能用 issubclass 检查
    # 改为检查 AgentState 是否有 messages 属性
    assert hasattr(AgentState, "__annotations__")
    assert "messages" in AgentState.__annotations__


def test_create_agent_returns_compiled_graph() -> None:
    """验证 create_agent() 返回有 invoke/ainvoke 方法的编译图"""
    # Mock get_config 避免读取环境变量
    with patch("layerkg.agent._helpers.get_config") as mock_get_config:
        mock_config = MagicMock()
        mock_config.agent_llm_model = "gpt-4"
        mock_config.agent_base_url = "https://api.example.com"
        mock_config.agent_api_key = "test-key"
        mock_get_config.return_value = mock_config

        # Mock ChatAnthropic 避免真实 API 调用
        with patch("layerkg.agent.graph.ChatAnthropic") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.bind_tools.return_value = mock_llm
            mock_llm.ainvoke.return_value = MagicMock(content="test response")
            mock_llm_class.return_value = mock_llm

            from layerkg.agent.graph import create_agent

            graph = create_agent()

            # 验证返回的是编译后的图（通过检查方法存在性）
            assert hasattr(graph, "invoke")
            assert hasattr(graph, "ainvoke")
            assert hasattr(graph, "nodes")
            assert callable(graph.invoke)
            assert callable(graph.ainvoke)


def test_create_agent_has_correct_nodes() -> None:
    """验证 create_agent() 创建的图包含 agent 和 tools 节点"""
    with patch("layerkg.agent._helpers.get_config") as mock_get_config:
        mock_config = MagicMock()
        mock_config.agent_llm_model = "gpt-4"
        mock_config.agent_base_url = "https://api.example.com"
        mock_config.agent_api_key = "test-key"
        mock_get_config.return_value = mock_config

        with patch("layerkg.agent.graph.ChatAnthropic") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.bind_tools.return_value = mock_llm
            mock_llm.ainvoke.return_value = MagicMock(content="test response")
            mock_llm_class.return_value = mock_llm

            from layerkg.agent.graph import create_agent

            graph = create_agent()

            # 获取图的节点
            node_names = set(graph.nodes.keys())
            assert "agent" in node_names
            assert "tools" in node_names
