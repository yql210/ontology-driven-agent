from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ontoagent.api.cli import main


@pytest.fixture
def runner() -> CliRunner:
    """创建 Click CliRunner。"""
    return CliRunner()


_VALID_SHAPE_YAML = """
version: "2.0"

shapes:
  - id: shape:test_only
    name: 测试 shape
    description: 仅用于 CLI 测试
    kind: operational
    target:
      entry_type: CodeEntity
      operation: UPDATE
    path: "SELF"
    constraint:
      field: entryCategory
      operator: in
      value: [http_api]
    severity: warn
    priority: 1
"""

_INVALID_SHAPE_YAML = """
version: "2.0"

shapes:
  - id: shape:bad_label
    name: 非法标签
    description: resource_type 不在合法标签集合
    kind: operational
    target:
      entry_type: NonExistentLabel
      operation: UPDATE
    path: "SELF"
    constraint:
      field: foo
      operator: in
      value: [bar]
    severity: warn
"""


class TestValidateShapesCommand:
    """测试 validate-shapes 命令。"""

    def test_validate_shapes_default_path_success(self, runner: CliRunner) -> None:
        """默认路径（pipeline/shapes.yaml）加载成功。"""
        # Act
        result = runner.invoke(main, ["validate-shapes"])

        # Assert
        assert result.exit_code == 0
        assert "Validated" in result.output

    def test_validate_shapes_with_custom_path(self, runner: CliRunner, tmp_path: Path) -> None:
        """--path 指定自定义 shapes.yaml。"""
        # Arrange
        shapes_file = tmp_path / "shapes.yaml"
        shapes_file.write_text(_VALID_SHAPE_YAML, encoding="utf-8")

        # Act
        result = runner.invoke(main, ["validate-shapes", "--path", str(shapes_file)])

        # Assert
        assert result.exit_code == 0
        assert "Validated 1 shapes" in result.output
        assert "shape:test_only" in result.output

    def test_validate_shapes_invalid_strict_exits_nonzero(self, runner: CliRunner, tmp_path: Path) -> None:
        """--strict 模式下校验失败应 exit(1)。"""
        # Arrange
        shapes_file = tmp_path / "shapes.yaml"
        shapes_file.write_text(_INVALID_SHAPE_YAML, encoding="utf-8")

        # Act
        result = runner.invoke(main, ["validate-shapes", "--path", str(shapes_file), "--strict"])

        # Assert
        assert result.exit_code == 1
        assert "Validation failed" in result.output

    def test_validate_shapes_invalid_without_strict_exits_zero(self, runner: CliRunner, tmp_path: Path) -> None:
        """非 strict 模式校验失败仍返回 0，但打印错误。"""
        # Arrange
        shapes_file = tmp_path / "shapes.yaml"
        shapes_file.write_text(_INVALID_SHAPE_YAML, encoding="utf-8")

        # Act
        result = runner.invoke(main, ["validate-shapes", "--path", str(shapes_file)])

        # Assert
        assert result.exit_code == 0
        assert "Validation failed" in result.output

    def test_validate_shapes_missing_file_strict_exits_nonzero(self, runner: CliRunner, tmp_path: Path) -> None:
        """--strict 模式下文件不存在应 exit(1)。"""
        # Arrange
        missing = tmp_path / "does-not-exist.yaml"

        # Act
        result = runner.invoke(main, ["validate-shapes", "--path", str(missing), "--strict"])

        # Assert
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_validate_shapes_missing_file_no_strict_exits_zero(self, runner: CliRunner, tmp_path: Path) -> None:
        """非 strict 模式下文件不存在 exit(0)。"""
        # Arrange
        missing = tmp_path / "does-not-exist.yaml"

        # Act
        result = runner.invoke(main, ["validate-shapes", "--path", str(missing)])

        # Assert
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_validate_shapes_help_lists_command(self, runner: CliRunner) -> None:
        """--help 应包含 validate-shapes。"""
        # Act
        result = runner.invoke(main, ["--help"])

        # Assert
        assert result.exit_code == 0
        assert "validate-shapes" in result.output
