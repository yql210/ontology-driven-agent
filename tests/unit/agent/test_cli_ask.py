"""测试 CLI ask 命令"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from click.testing import CliRunner


def test_ask_without_args_shows_hint() -> None:
    """不带参数运行 ask，输出包含提示信息"""
    runner = CliRunner()

    # Mock run_query 避免 async 运行
    with patch("layerkg.agent.graph.run_query", new=AsyncMock(return_value="test answer")):
        from layerkg.api.cli import main

        result = runner.invoke(main, ["ask"])

        assert result.exit_code == 0
        assert "请提供问题" in result.output


def test_ask_without_question_shows_hint() -> None:
    """不使用 -i 且不带问题时，显示提示"""
    runner = CliRunner()

    with patch("layerkg.agent.graph.run_query", new=AsyncMock(return_value="test answer")):
        from layerkg.api.cli import main

        result = runner.invoke(main, ["ask"])

        assert result.exit_code == 0
        assert "请提供问题" in result.output or "交互模式" in result.output


def test_ask_help_shows_usage() -> None:
    """ask --help 显示帮助信息"""
    runner = CliRunner()

    from layerkg.api.cli import main

    result = runner.invoke(main, ["ask", "--help"])

    assert result.exit_code == 0
    # 验证帮助信息的关键内容
    assert "ask" in result.output
    assert "QUESTION" in result.output
    assert "--interactive" in result.output or "-i" in result.output


def test_ask_with_question_runs_query() -> None:
    """带问题参数运行 ask，调用 run_query"""
    runner = CliRunner()

    # Mock run_query
    with patch("layerkg.agent.graph.run_query", new=AsyncMock(return_value="这是答案")):
        from layerkg.api.cli import main

        result = runner.invoke(main, ["ask", "测试问题"])

        assert result.exit_code == 0
        assert "这是答案" in result.output


def test_ask_with_interactive_shows_prompt() -> None:
    """使用 -i 进入交互模式，显示提示信息"""
    runner = CliRunner()

    # 模拟用户输入 "quit" 退出
    with patch("layerkg.agent.graph.run_query", new=AsyncMock(return_value="test answer")):
        from layerkg.api.cli import main

        result = runner.invoke(main, ["ask", "-i"], input="quit\n")

        assert result.exit_code == 0
        assert "交互模式" in result.output or "LayerKG" in result.output
