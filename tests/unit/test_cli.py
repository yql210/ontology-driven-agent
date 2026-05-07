from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from layerkg.builder import BuildResult
from layerkg.cli import main


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


class TestMain:
    """测试主命令。"""

    def test_main_help(self, runner: CliRunner) -> None:
        # Act
        result = runner.invoke(main, ["--help"])

        # Assert
        assert result.exit_code == 0
        assert "LayerKG" in result.output
        assert "build" in result.output
        assert "query" in result.output
        assert "info" in result.output


class TestBuildCommand:
    """测试 build 命令。"""

    def test_build_command_with_valid_path(
        self, runner: CliRunner, tmp_path: Path, mock_build_result: BuildResult
    ) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = mock_build_result
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["build", str(tmp_path)])

            # Assert
            assert result.exit_code == 0
            assert "Build complete" in result.output
            assert "5 files scanned" in result.output
            assert "42 entities created" in result.output
            assert "18 relations created" in result.output

    def test_build_command_nonexistent_path_fails(self, runner: CliRunner) -> None:
        # Act
        result = runner.invoke(main, ["build", "/nonexistent/path/that/does/not/exist"])

        # Assert
        assert result.exit_code != 0
        assert "does not exist" in result.output.lower() or "invalid" in result.output.lower()

    def test_build_command_shows_summary(
        self, runner: CliRunner, tmp_path: Path, mock_build_result: BuildResult
    ) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = mock_build_result
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["build", str(tmp_path)])

            # Assert
            assert "files scanned" in result.output
            assert "entities created" in result.output
            assert "relations created" in result.output


class TestQueryCommand:
    """测试 query 命令。"""

    def test_query_command_returns_results(self, runner: CliRunner) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.query.return_value = [
            {
                "id": "123",
                "text": "def foo(): pass",
                "metadata": {"entity_type": "function", "name": "foo"},
                "distance": 0.1234,
            }
        ]
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["query", "foo"])

            # Assert
            assert result.exit_code == 0
            assert "Found 1 result" in result.output
            assert "[function]" in result.output
            assert "foo" in result.output
            assert "0.1234" in result.output

    def test_query_command_no_results(self, runner: CliRunner) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.query.return_value = []
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["query", "nonexistent"])

            # Assert
            assert result.exit_code == 0
            assert "No results found" in result.output

    def test_query_with_type_option(self, runner: CliRunner) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.query.return_value = []
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["query", "test", "--type", "class"])

            # Assert
            assert result.exit_code == 0
            mock_builder.query.assert_called_once_with("test", n_results=10, entity_type="class")

    def test_query_with_limit_option(self, runner: CliRunner) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.query.return_value = []
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["query", "test", "-n", "5"])

            # Assert
            assert result.exit_code == 0
            mock_builder.query.assert_called_once_with("test", n_results=5, entity_type=None)

    def test_query_with_none_distance(self, runner: CliRunner) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.query.return_value = [
            {
                "id": "123",
                "text": "def foo(): pass",
                "metadata": {"entity_type": "function", "name": "foo"},
                "distance": None,
            }
        ]
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["query", "foo"])

            # Assert
            assert result.exit_code == 0
            assert "N/A" in result.output


class TestInfoCommand:
    """测试 info 命令。"""

    def test_info_command_shows_config(self, runner: CliRunner) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.info.return_value = {
            "config": {
                "neo4j_uri": "bolt://localhost:7687",
                "ollama_url": "http://localhost:11434",
                "model": "test-model",
                "chroma_dir": ".chroma",
            },
            "chroma_count": 123,
        }
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config = MagicMock()
            mock_config.neo4j_uri = "bolt://localhost:7687"
            mock_config.ollama_base_url = "http://localhost:11434"
            mock_config.embedding_model = "test-model"
            mock_config.chroma_persist_dir = ".chroma"
            mock_config_cls.from_env.return_value = mock_config

            # Act
            result = runner.invoke(main, ["info"])

            # Assert
            assert result.exit_code == 0
            assert "bolt://localhost:7687" in result.output
            assert "http://localhost:11434" in result.output
            assert "test-model" in result.output
            assert ".chroma" in result.output
            assert "123" in result.output


class TestVerboseFlag:
    """测试 verbose 标志。"""

    def test_verbose_flag_sets_debug(self, runner: CliRunner, tmp_path: Path) -> None:
        # Arrange
        mock_builder = MagicMock()
        mock_builder.build.return_value = BuildResult(0, 0, 0)
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["-v", "build", str(tmp_path)])

            # Assert
            # 命令应该成功执行（verbose 标志不影响功能）
            assert result.exit_code == 0
