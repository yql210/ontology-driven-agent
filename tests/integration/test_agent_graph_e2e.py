"""Agent 图结构集成测试"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestAgentGraphStructure:
    """验证 Agent 图的结构和配置"""

    def test_create_agent_has_checkpointer(self):
        """create_agent 返回带 checkpointer 的编译图"""
        with patch("layerkg.agent._helpers.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                agent_llm_model="test",
                agent_base_url="http://test",
                agent_api_key="k",
                langfuse_public_key="",
                langfuse_secret_key="",
            )
            from layerkg.agent.graph import create_agent

            agent = create_agent()
            assert agent.checkpointer is not None

    def test_make_config_includes_thread_id(self):
        """_make_config 包含 thread_id 和 recursion_limit"""
        with patch("layerkg.agent._helpers.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                langfuse_public_key="",
                langfuse_secret_key="",
            )
            from layerkg.agent.graph import _make_config

            config = _make_config("test-thread")
            assert config["configurable"]["thread_id"] == "test-thread"
            assert config["recursion_limit"] == 50

    def test_all_tools_registered_in_graph(self):
        """图节点包含 agent 和 tools"""
        with patch("layerkg.agent._helpers.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                agent_llm_model="test",
                agent_base_url="http://test",
                agent_api_key="k",
                langfuse_public_key="",
                langfuse_secret_key="",
            )
            from layerkg.agent.graph import create_agent

            agent = create_agent()
            assert "agent" in agent.nodes
            assert "tools" in agent.nodes

    def test_global_checkpointer_is_singleton(self):
        """全局 checkpointer 是单例"""
        from layerkg.agent.graph import _get_checkpointer

        cp1 = _get_checkpointer()
        cp2 = _get_checkpointer()
        assert cp1 is cp2
