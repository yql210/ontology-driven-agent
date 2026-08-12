"""测试 graph.py 的状态图构建"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ontoagent.config import OntoAgentConfig


def test_agent_state_is_messages_state() -> None:
    """验证 AgentState 是 MessagesState 子类"""
    from ontoagent.agent.graph import AgentState

    # MessagesState 是一个 TypedDict，不能用 issubclass 检查
    # 改为检查 AgentState 是否有 messages 属性
    assert hasattr(AgentState, "__annotations__")
    assert "messages" in AgentState.__annotations__


def test_create_agent_returns_compiled_graph() -> None:
    """验证 create_agent() 返回有 invoke/ainvoke 方法的编译图"""
    # Mock get_config 避免读取环境变量
    with patch("ontoagent.agent._helpers.get_config") as mock_get_config:
        mock_config = MagicMock()
        mock_config.agent_llm_model = "gpt-4"
        mock_config.agent_base_url = "https://api.example.com"
        mock_config.agent_api_key = "test-key"
        mock_config.agent_llm_extra_body = None
        mock_get_config.return_value = mock_config

        # Mock ChatAnthropic 避免真实 API 调用
        with patch("ontoagent.agent.graph.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.bind_tools.return_value = mock_llm
            mock_llm.ainvoke.return_value = MagicMock(content="test response")
            mock_llm_class.return_value = mock_llm

            from ontoagent.agent.graph import create_agent

            graph = create_agent()

            # 验证返回的是编译后的图（通过检查方法存在性）
            assert hasattr(graph, "invoke")
            assert hasattr(graph, "ainvoke")
            assert hasattr(graph, "nodes")
            assert callable(graph.invoke)
            assert callable(graph.ainvoke)


def test_create_agent_has_correct_nodes() -> None:
    """验证 create_agent() 创建的图包含 agent 和 tools 节点"""
    with patch("ontoagent.agent._helpers.get_config") as mock_get_config:
        mock_config = MagicMock()
        mock_config.agent_llm_model = "gpt-4"
        mock_config.agent_base_url = "https://api.example.com"
        mock_config.agent_api_key = "test-key"
        mock_config.agent_llm_extra_body = None
        mock_get_config.return_value = mock_config

        with patch("ontoagent.agent.graph.ChatOpenAI") as mock_llm_class:
            mock_llm = MagicMock()
            mock_llm.bind_tools.return_value = mock_llm
            mock_llm.ainvoke.return_value = MagicMock(content="test response")
            mock_llm_class.return_value = mock_llm

            from ontoagent.agent.graph import create_agent

            graph = create_agent()

            # 获取图的节点
            node_names = set(graph.nodes.keys())
            assert "agent" in node_names
            assert "tools" in node_names


# ===== LLM Singleton Tests =====


def test_llm_singleton() -> None:
    """验证多次调用 _get_llm() 返回同一实例"""
    with patch("ontoagent.agent._helpers.get_config") as mock_get_config:
        mock_config = MagicMock()
        mock_config.agent_llm_model = "gpt-4"
        mock_config.agent_base_url = "https://api.example.com"
        mock_config.agent_api_key = "test-key"
        mock_config.agent_llm_extra_body = None
        mock_get_config.return_value = mock_config

        from ontoagent.agent.graph import _get_llm, _reset_llm

        # Reset first
        _reset_llm()

        # First call creates instance
        llm1 = _get_llm()
        assert llm1 is not None

        # Second call returns same instance
        llm2 = _get_llm()
        assert llm1 is llm2


def test_llm_reset() -> None:
    """验证 _reset_llm() 后创建新实例"""
    with patch("ontoagent.agent._helpers.get_config") as mock_get_config:
        mock_config = MagicMock()
        mock_config.agent_llm_model = "gpt-4"
        mock_config.agent_base_url = "https://api.example.com"
        mock_config.agent_api_key = "test-key"
        mock_config.agent_llm_extra_body = None
        mock_get_config.return_value = mock_config

        from ontoagent.agent.graph import _get_llm, _reset_llm

        # Get first instance
        llm1 = _get_llm()
        assert llm1 is not None

        # Reset
        _reset_llm()

        # Get new instance — should be different object
        llm2 = _get_llm()
        assert llm1 is not llm2


# ===== _create_llm extra_body 透传 Tests =====


def test_create_llm_passes_extra_body_via_model_kwargs() -> None:
    """验证 _create_llm 将 extra_body 嵌套透传到 ChatOpenAI model_kwargs。"""
    from ontoagent.agent.graph import _create_llm

    cfg = OntoAgentConfig(agent_llm_extra_body={"thinking": True})
    with (
        patch("ontoagent.agent._helpers.get_config", return_value=cfg),
        patch("ontoagent.agent.graph.ChatOpenAI") as mock_llm_class,
    ):
        _create_llm()
        mock_llm_class.assert_called_once()
        kwargs = mock_llm_class.call_args.kwargs
        assert kwargs["model_kwargs"] == {"extra_body": {"thinking": True}}


def test_create_llm_without_extra_body_omits_model_kwargs() -> None:
    """验证不设 extra_body 时 ChatOpenAI 不收 model_kwargs 参数。"""
    from ontoagent.agent.graph import _create_llm

    cfg = OntoAgentConfig()
    with (
        patch("ontoagent.agent._helpers.get_config", return_value=cfg),
        patch("ontoagent.agent.graph.ChatOpenAI") as mock_llm_class,
    ):
        _create_llm()
        mock_llm_class.assert_called_once()
        kwargs = mock_llm_class.call_args.kwargs
        assert "model_kwargs" not in kwargs


def test_make_config_uses_agent_recursion_limit() -> None:
    """_make_config 的 recursion_limit 取自 config.agent_recursion_limit。"""
    from ontoagent.agent.graph import _make_config

    cfg = OntoAgentConfig(agent_recursion_limit=30)
    with patch("ontoagent.agent._helpers.get_config", return_value=cfg):
        config = _make_config("test-thread")
    assert config["recursion_limit"] == 30
