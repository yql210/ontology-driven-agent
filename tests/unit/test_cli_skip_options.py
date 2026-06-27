from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ontoagent.api.cli import main
from ontoagent.pipeline.builder import BuildResult


@pytest.fixture
def runner() -> CliRunner:
    """创建 Click CliRunner。"""
    return CliRunner()


@pytest.fixture
def mock_build_result() -> BuildResult:
    """模拟构建结果。"""
    return BuildResult(
        files_scanned=5,
        entities_created=42,
        relations_created=18,
    )


class TestBuildCommandSkipOptions:
    """测试 build 命令 skip 选项。"""

    def test_build_command_skip_semantic(
        self, runner: CliRunner, tmp_path: Path, mock_build_result: BuildResult
    ) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = mock_build_result
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("ontoagent.api.cli.OntoAgentConfig") as mock_config_cls,
            patch("ontoagent.api.cli.OntoAgentBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["build", str(tmp_path), "--skip-semantic"])

            # Assert
            assert result.exit_code == 0
            mock_builder.build.assert_called_once()
            call_kwargs = mock_builder.build.call_args[1]
            assert call_kwargs.get("skip_semantic") is True

    def test_build_command_skip_clustering(
        self, runner: CliRunner, tmp_path: Path, mock_build_result: BuildResult
    ) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = mock_build_result
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("ontoagent.api.cli.OntoAgentConfig") as mock_config_cls,
            patch("ontoagent.api.cli.OntoAgentBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["build", str(tmp_path), "--skip-clustering"])

            # Assert
            assert result.exit_code == 0
            mock_builder.build.assert_called_once()
            call_kwargs = mock_builder.build.call_args[1]
            assert call_kwargs.get("skip_clustering") is True

    def test_build_command_both_skip(self, runner: CliRunner, tmp_path: Path, mock_build_result: BuildResult) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = mock_build_result
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("ontoagent.api.cli.OntoAgentConfig") as mock_config_cls,
            patch("ontoagent.api.cli.OntoAgentBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["build", str(tmp_path), "--skip-semantic", "--skip-clustering"])

            # Assert
            assert result.exit_code == 0
            mock_builder.build.assert_called_once()
            call_kwargs = mock_builder.build.call_args[1]
            assert call_kwargs.get("skip_semantic") is True
            assert call_kwargs.get("skip_clustering") is True

    def test_build_command_verbose_output(self, runner: CliRunner, tmp_path: Path) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_result = BuildResult(
            files_scanned=10,
            entities_created=100,
            relations_created=50,
            doc_entities_created=3,
            concepts_created=5,
            semantic_relations_created=7,
            modules_created=2,
            skipped_semantic=False,
        )
        mock_builder.build.return_value = mock_result
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("ontoagent.api.cli.OntoAgentConfig") as mock_config_cls,
            patch("ontoagent.api.cli.OntoAgentBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["build", str(tmp_path), "--verbose-build"])

            # Assert
            assert result.exit_code == 0
            assert "Build Report:" in result.output
            assert "Semantic stage:" in result.output
            assert "[+] completed" in result.output

    def test_build_command_default_output_unchanged(
        self, runner: CliRunner, tmp_path: Path, mock_build_result: BuildResult
    ) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = mock_build_result
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("ontoagent.api.cli.OntoAgentConfig") as mock_config_cls,
            patch("ontoagent.api.cli.OntoAgentBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act - 无 flag
            result = runner.invoke(main, ["build", str(tmp_path)])

            # Assert - 保持原有格式
            assert result.exit_code == 0
            assert "Build complete:" in result.output
            assert "files scanned" in result.output
            assert "entities created" in result.output
            assert "relations created" in result.output
            # verbose 输出不应出现
            assert "Build Report:" not in result.output
