from __future__ import annotations

import os

from layerkg.config import LayerKGConfig


class TestConfigBuildFields:
    """测试 Config build 配置字段。"""

    def test_config_build_include_docs_default(self) -> None:
        # Act
        config = LayerKGConfig()

        # Assert
        assert config.build_include_docs is True

    def test_config_build_include_docs_from_env(self) -> None:
        # Arrange
        original = os.getenv("LAYERKG_BUILD_INCLUDE_DOCS")
        try:
            # Act
            os.environ["LAYERKG_BUILD_INCLUDE_DOCS"] = "false"
            config = LayerKGConfig.from_env()

            # Assert
            assert config.build_include_docs is False
        finally:
            if original is None:
                os.environ.pop("LAYERKG_BUILD_INCLUDE_DOCS", None)
            else:
                os.environ["LAYERKG_BUILD_INCLUDE_DOCS"] = original

    def test_config_build_doc_extensions_default(self) -> None:
        # Act
        config = LayerKGConfig()

        # Assert
        assert config.build_doc_extensions == [".md", ".rst"]

    def test_config_build_skip_dirs_default(self) -> None:
        # Act
        config = LayerKGConfig()

        # Assert - 验证包含常见跳过目录
        assert "__pycache__" in config.build_skip_dirs
        assert ".git" in config.build_skip_dirs
        assert ".mypy_cache" in config.build_skip_dirs
        assert ".ruff_cache" in config.build_skip_dirs
        assert "node_modules" in config.build_skip_dirs
        assert ".venv" in config.build_skip_dirs
        assert "site" in config.build_skip_dirs
        assert ".tox" in config.build_skip_dirs
        assert "dist" in config.build_skip_dirs
        assert "build" in config.build_skip_dirs
        assert "*.egg-info" in config.build_skip_dirs

    def test_config_build_skip_dirs_is_set(self) -> None:
        # Act
        config = LayerKGConfig()

        # Assert - 验证返回的是 set 类型
        assert isinstance(config.build_skip_dirs, set)

    def test_config_build_doc_extensions_is_list(self) -> None:
        # Act
        config = LayerKGConfig()

        # Assert - 验证返回的是 list 类型
        assert isinstance(config.build_doc_extensions, list)
