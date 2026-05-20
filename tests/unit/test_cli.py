from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from layerkg.builder import BuildResult
from layerkg.cli import main
from layerkg.schema_version import SchemaStatus


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


class TestServeCommand:
    """测试 serve 命令。"""

    def test_serve_stdio_calls_mcp_run(self, runner: CliRunner) -> None:
        """测试 stdio 模式调用 mcp.run()。"""
        # Arrange
        mock_run = MagicMock()

        with patch("layerkg.mcp_server.mcp.run", mock_run):
            # Act
            result = runner.invoke(main, ["serve"])

            # Assert
            assert result.exit_code == 0
            assert "Starting MCP server on stdio" in result.output
            mock_run.assert_called_once_with()

    def test_serve_http_calls_mcp_run_with_params(self, runner: CliRunner) -> None:
        """测试 http 模式调用 mcp.run() 并传递参数。"""
        # Arrange
        mock_run = MagicMock()

        with patch("layerkg.mcp_server.mcp.run", mock_run):
            # Act
            result = runner.invoke(main, ["serve", "--transport", "http", "--port", "9000"])

            # Assert
            assert result.exit_code == 0
            assert "Starting MCP server on http://localhost:9000" in result.output
            mock_run.assert_called_once_with(transport="http", port=9000)


class TestUpdateCommand:
    """测试 update 命令。"""

    def test_update_command_success(self, runner: CliRunner, tmp_path: Path) -> None:
        """测试 update 命令成功执行。"""
        # Arrange
        mock_updater = MagicMock()
        mock_report = MagicMock()
        mock_report.to_dict.return_value = {"files": 5, "entities": 42}
        mock_updater.update.return_value = mock_report
        mock_updater.__enter__ = MagicMock(return_value=mock_updater)
        mock_updater.__exit__ = MagicMock(return_value=False)

        with patch("layerkg.cli.IncrementalUpdater", return_value=mock_updater):
            # Act
            result = runner.invoke(main, ["update", str(tmp_path)])

            # Assert
            assert result.exit_code == 0
            assert "Update complete" in result.output
            mock_updater.update.assert_called_once_with("HEAD~1", dry_run=False, full_scan=False)

    def test_update_command_dry_run(self, runner: CliRunner, tmp_path: Path) -> None:
        """测试 update 命令 dry-run 模式。"""
        # Arrange
        mock_updater = MagicMock()
        mock_report = MagicMock()
        mock_report.to_dict.return_value = {"files": 3, "dry_run": True}
        mock_updater.update.return_value = mock_report
        mock_updater.__enter__ = MagicMock(return_value=mock_updater)
        mock_updater.__exit__ = MagicMock(return_value=False)

        with patch("layerkg.cli.IncrementalUpdater", return_value=mock_updater):
            # Act
            result = runner.invoke(main, ["update", str(tmp_path), "--dry-run"])

            # Assert
            assert result.exit_code == 0
            mock_updater.update.assert_called_once_with("HEAD~1", dry_run=True, full_scan=False)


class TestHelpContent:
    """测试 help 内容完整性。"""

    def test_main_help_includes_update_and_serve(self, runner: CliRunner) -> None:
        """测试 --help 包含 update 和 serve 命令。"""
        # Act
        result = runner.invoke(main, ["--help"])

        # Assert
        assert result.exit_code == 0
        assert "update" in result.output
        assert "serve" in result.output


class TestErrorHandling:
    """测试错误处理。"""

    def test_build_command_missing_path_argument(self, runner: CliRunner) -> None:
        """测试 build 命令缺少路径参数。"""
        # Act
        result = runner.invoke(main, ["build"])

        # Assert
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "requires" in result.output.lower()

    def test_query_command_without_limit_shows_results(self, runner: CliRunner) -> None:
        """测试 query 命令不传 --limit 时正常返回结果。"""
        # Arrange
        mock_builder = MagicMock()
        mock_builder.query.return_value = [
            {
                "id": "456",
                "text": "def bar(): pass",
                "metadata": {"entity_type": "function", "name": "bar"},
                "distance": 0.5678,
            }
        ]
        mock_builder.__enter__ = MagicMock(return_value=mock_builder)
        mock_builder.__exit__ = MagicMock(return_value=False)

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.LayerKGBuilder", return_value=mock_builder),
        ):
            mock_config_cls.from_env.return_value = MagicMock()

            # Act - 不传 --limit 选项
            result = runner.invoke(main, ["query", "bar"])

            # Assert
            assert result.exit_code == 0
            assert "bar" in result.output
            # 验证传递了默认的 limit 值 (10)
            mock_builder.query.assert_called_once_with("bar", n_results=10, entity_type=None)


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


class TestMigrateCommand:
    """测试 migrate 命令。"""

    def test_migrate_command_no_pending(self, runner: CliRunner) -> None:
        """migrate 命令在无待迁移时应成功。"""
        # Arrange
        mock_store = MagicMock()
        mock_config = MagicMock()

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.Neo4jGraphStore", return_value=mock_store),
            patch("layerkg.cli.check_schema_version") as mock_check,
            patch("layerkg.cli.MigrationRunner") as MockRunner,
            patch("layerkg.cli.MigrationRegistry") as MockRegistry,
        ):
            mock_config_cls.from_env.return_value = mock_config
            mock_config.neo4j_uri = "bolt://localhost:7687"
            mock_config.neo4j_user = "neo4j"
            mock_config.neo4j_password = "password"
            mock_check.return_value = SchemaStatus.MATCH
            mock_runner = MagicMock()
            mock_runner.run_pending.return_value = []
            MockRunner.return_value = mock_runner
            MockRegistry.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["migrate"])

            # Assert
            assert result.exit_code == 0
            assert "No pending migrations" in result.output
            mock_runner.run_pending.assert_called_once()

    def test_migrate_command_applies_migrations(self, runner: CliRunner) -> None:
        """migrate 命令应能应用迁移。"""
        # Arrange
        mock_store = MagicMock()
        mock_config = MagicMock()

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.Neo4jGraphStore", return_value=mock_store),
            patch("layerkg.cli.check_schema_version") as mock_check,
            patch("layerkg.cli.MigrationRunner") as MockRunner,
            patch("layerkg.cli.MigrationRegistry") as MockRegistry,
        ):
            mock_config_cls.from_env.return_value = mock_config
            mock_config.neo4j_uri = "bolt://localhost:7687"
            mock_config.neo4j_user = "neo4j"
            mock_config.neo4j_password = "password"
            mock_check.return_value = SchemaStatus.BEHIND
            mock_runner = MagicMock()
            mock_runner.run_pending.return_value = ["1.0.0"]
            MockRunner.return_value = mock_runner
            MockRegistry.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["migrate"])

            # Assert
            assert result.exit_code == 0
            assert "Applied 1 migrations" in result.output
            assert "1.0.0" in result.output

    def test_migrate_command_with_target_rollback(self, runner: CliRunner) -> None:
        """migrate 命令支持回滚到指定版本。"""
        # Arrange
        mock_store = MagicMock()
        mock_config = MagicMock()

        with (
            patch("layerkg.cli.LayerKGConfig") as mock_config_cls,
            patch("layerkg.cli.Neo4jGraphStore", return_value=mock_store),
            patch("layerkg.cli.MigrationRunner") as MockRunner,
            patch("layerkg.cli.MigrationRegistry") as MockRegistry,
        ):
            mock_config_cls.from_env.return_value = mock_config
            mock_config.neo4j_uri = "bolt://localhost:7687"
            mock_config.neo4j_user = "neo4j"
            mock_config.neo4j_password = "password"
            mock_runner = MagicMock()
            mock_runner.rollback.return_value = ["0.9.0"]
            MockRunner.return_value = mock_runner
            MockRegistry.return_value = MagicMock()

            # Act
            result = runner.invoke(main, ["migrate", "--target", "0.9.0"])

            # Assert
            assert result.exit_code == 0
            assert "Rolled back" in result.output
            mock_runner.rollback.assert_called_once_with("0.9.0")
