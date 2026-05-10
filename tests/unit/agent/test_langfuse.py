"""Langfuse 回调集成测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_langfuse_handler_returns_none_without_keys() -> None:
    """无 Langfuse key 时返回 None"""
    with patch("layerkg.agent._helpers.get_config") as mock_config_fn:
        mock_cfg = MagicMock()
        mock_cfg.langfuse_public_key = ""
        mock_cfg.langfuse_secret_key = ""
        mock_config_fn.return_value = mock_cfg

        from layerkg.agent.graph import _get_langfuse_handler

        assert _get_langfuse_handler() is None


def test_langfuse_handler_returns_handler_with_keys() -> None:
    """有 Langfuse key 时返回 CallbackHandler（如果 langchain 可用）"""
    with patch("layerkg.agent._helpers.get_config") as mock_config_fn:
        mock_cfg = MagicMock()
        mock_cfg.langfuse_public_key = "pk-test"
        mock_cfg.langfuse_secret_key = "sk-test"
        mock_cfg.langfuse_host = "http://localhost:3000"
        mock_config_fn.return_value = mock_cfg

        from layerkg.agent.graph import _get_langfuse_handler

        try:
            handler = _get_langfuse_handler()
            assert handler is not None
        except ModuleNotFoundError as e:
            # langchain 未安装时跳过测试
            import pytest

            pytest.skip(f"langchain 未安装: {e}")
