from __future__ import annotations

import os

from ontoagent.config import OntoAgentConfig


class TestConfigNebulaFields:
    """测试 Config NebulaGraph 后端配置字段（Phase 1）。"""

    def test_graph_backend_default_is_nebula(self) -> None:
        """生产默认使用 NebulaGraph（Phase 9 改动）。"""
        config = OntoAgentConfig()
        assert config.graph_backend == "nebula"

    def test_nebula_host_default(self) -> None:
        config = OntoAgentConfig()
        assert config.nebula_host == "127.0.0.1"

    def test_nebula_port_default(self) -> None:
        config = OntoAgentConfig()
        assert config.nebula_port == 9669

    def test_nebula_user_default(self) -> None:
        config = OntoAgentConfig()
        assert config.nebula_user == "root"

    def test_nebula_password_default(self) -> None:
        config = OntoAgentConfig()
        assert config.nebula_password == "nebula"

    def test_nebula_space_default(self) -> None:
        config = OntoAgentConfig()
        assert config.nebula_space == "ontoagent"

    def test_nebula_vid_type_default(self) -> None:
        """vid_type 默认 FIXED_STRING(36) 匹配 OntoAgent UUID。"""
        config = OntoAgentConfig()
        assert config.nebula_vid_type == "FIXED_STRING(36)"


class TestConfigNebulaFromEnv:
    """测试 from_env 读取 NebulaGraph 环境变量。"""

    _env_keys = (
        "ONTOAGENT_GRAPH_BACKEND",
        "ONTOAGENT_NEBULA_HOST",
        "ONTOAGENT_NEBULA_PORT",
        "ONTOAGENT_NEBULA_USER",
        "ONTOAGENT_NEBULA_PASSWORD",
        "ONTOAGENT_NEBULA_SPACE",
        "ONTOAGENT_NEBULA_VID_TYPE",
    )

    def _stash_env(self) -> dict[str, str | None]:
        return {key: os.getenv(key) for key in self._env_keys}

    def _restore_env(self, stash: dict[str, str | None]) -> None:
        for key, value in stash.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_graph_backend_from_env(self) -> None:
        stash = self._stash_env()
        try:
            os.environ["ONTOAGENT_GRAPH_BACKEND"] = "nebula"
            config = OntoAgentConfig.from_env()
            assert config.graph_backend == "nebula"
        finally:
            self._restore_env(stash)

    def test_nebula_host_from_env(self) -> None:
        stash = self._stash_env()
        try:
            os.environ["ONTOAGENT_NEBULA_HOST"] = "10.0.0.1"
            config = OntoAgentConfig.from_env()
            assert config.nebula_host == "10.0.0.1"
        finally:
            self._restore_env(stash)

    def test_nebula_port_from_env(self) -> None:
        stash = self._stash_env()
        try:
            os.environ["ONTOAGENT_NEBULA_PORT"] = "10000"
            config = OntoAgentConfig.from_env()
            assert config.nebula_port == 10000
        finally:
            self._restore_env(stash)

    def test_nebula_user_from_env(self) -> None:
        stash = self._stash_env()
        try:
            os.environ["ONTOAGENT_NEBULA_USER"] = "admin"
            config = OntoAgentConfig.from_env()
            assert config.nebula_user == "admin"
        finally:
            self._restore_env(stash)

    def test_nebula_password_from_env(self) -> None:
        stash = self._stash_env()
        try:
            os.environ["ONTOAGENT_NEBULA_PASSWORD"] = "secret"
            config = OntoAgentConfig.from_env()
            assert config.nebula_password == "secret"
        finally:
            self._restore_env(stash)

    def test_nebula_space_from_env(self) -> None:
        stash = self._stash_env()
        try:
            os.environ["ONTOAGENT_NEBULA_SPACE"] = "my_space"
            config = OntoAgentConfig.from_env()
            assert config.nebula_space == "my_space"
        finally:
            self._restore_env(stash)
