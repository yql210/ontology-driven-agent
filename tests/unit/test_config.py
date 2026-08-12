from __future__ import annotations

import os
from pathlib import Path

from ontoagent.config import OntoAgentConfig, _load_dotenv


def test_llm_model_default_value():
    """llm_model 默认值为 'qwen3.5:9b'。"""
    config = OntoAgentConfig()
    assert config.llm_model == "qwen3.5:9b"


def test_llm_model_from_env():
    """from_env 读取 ONTOAGENT_LLM_MODEL 环境变量。"""
    original = os.getenv("ONTOAGENT_LLM_MODEL")
    try:
        os.environ["ONTOAGENT_LLM_MODEL"] = "custom-model"
        config = OntoAgentConfig.from_env()
        assert config.llm_model == "custom-model"
    finally:
        if original is None:
            os.environ.pop("ONTOAGENT_LLM_MODEL", None)
        else:
            os.environ["ONTOAGENT_LLM_MODEL"] = original


def test_llm_model_explicit():
    """显式设置 llm_model。"""
    config = OntoAgentConfig(llm_model="explicit-model")
    assert config.llm_model == "explicit-model"


def test_from_env_doc_extensions():
    """from_env 读取 ONTOAGENT_BUILD_DOC_EXTENSIONS 环境变量。"""
    original = os.getenv("ONTOAGENT_BUILD_DOC_EXTENSIONS")
    try:
        os.environ["ONTOAGENT_BUILD_DOC_EXTENSIONS"] = ".md,.rst,.txt"
        config = OntoAgentConfig.from_env()
        assert config.build_doc_extensions == [".md", ".rst", ".txt"]
    finally:
        if original is None:
            os.environ.pop("ONTOAGENT_BUILD_DOC_EXTENSIONS", None)
        else:
            os.environ["ONTOAGENT_BUILD_DOC_EXTENSIONS"] = original


def test_from_env_skip_dirs():
    """from_env 读取 ONTOAGENT_BUILD_SKIP_DIRS 环境变量。"""
    original = os.getenv("ONTOAGENT_BUILD_SKIP_DIRS")
    try:
        os.environ["ONTOAGENT_BUILD_SKIP_DIRS"] = ".git,node_modules,dist"
        config = OntoAgentConfig.from_env()
        assert config.build_skip_dirs == {".git", "node_modules", "dist"}
    finally:
        if original is None:
            os.environ.pop("ONTOAGENT_BUILD_SKIP_DIRS", None)
        else:
            os.environ["ONTOAGENT_BUILD_SKIP_DIRS"] = original


def test_from_env_doc_max_length():
    """from_env 读取 ONTOAGENT_BUILD_DOC_MAX_LENGTH 环境变量。"""
    original = os.getenv("ONTOAGENT_BUILD_DOC_MAX_LENGTH")
    try:
        os.environ["ONTOAGENT_BUILD_DOC_MAX_LENGTH"] = "5000"
        config = OntoAgentConfig.from_env()
        assert config.build_doc_max_length == 5000
    finally:
        if original is None:
            os.environ.pop("ONTOAGENT_BUILD_DOC_MAX_LENGTH", None)
        else:
            os.environ["ONTOAGENT_BUILD_DOC_MAX_LENGTH"] = original


def test_from_env_defaults():
    """from_env 使用默认值（未设置环境变量时）。"""
    # 清除可能设置的环境变量
    originals = {}
    for key in ("ONTOAGENT_BUILD_DOC_EXTENSIONS", "ONTOAGENT_BUILD_SKIP_DIRS", "ONTOAGENT_BUILD_DOC_MAX_LENGTH"):
        originals[key] = os.getenv(key)
        if originals[key] is None:
            os.environ.pop(key, None)

    try:
        config = OntoAgentConfig.from_env()
        assert config.build_doc_extensions == [".md", ".rst"]
        assert ".pytest_cache" in config.build_skip_dirs
        assert "venv" in config.build_skip_dirs
        assert config.build_doc_max_length == 2000
    finally:
        for key, val in originals.items():
            if val is not None:
                os.environ[key] = val


def test_from_env_agent_llm_extra_body(monkeypatch):
    """from_env 解析 ONTOAGENT_AGENT_LLM_EXTRA_BODY JSON。"""
    monkeypatch.setenv("ONTOAGENT_AGENT_LLM_EXTRA_BODY", '{"thinking":true,"thinking_budget":1024}')
    config = OntoAgentConfig.from_env()
    assert config.agent_llm_extra_body == {"thinking": True, "thinking_budget": 1024}


def test_from_env_agent_llm_extra_body_invalid_json(monkeypatch):
    """非法 JSON 不抛异常，agent_llm_extra_body 为 None。"""
    monkeypatch.setenv("ONTOAGENT_AGENT_LLM_EXTRA_BODY", "not-json")
    config = OntoAgentConfig.from_env()
    assert config.agent_llm_extra_body is None


def test_from_env_agent_llm_extra_body_unset(monkeypatch):
    """未设置时 agent_llm_extra_body 为 None。"""
    monkeypatch.delenv("ONTOAGENT_AGENT_LLM_EXTRA_BODY", raising=False)
    config = OntoAgentConfig.from_env()
    assert config.agent_llm_extra_body is None


class TestLoadDotenv:
    """测试 _load_dotenv 剥离行内注释。"""

    def test_strips_inline_comment(self, tmp_path: Path, monkeypatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value # comment\n")
        monkeypatch.delenv("KEY", raising=False)
        _load_dotenv(env_file)
        assert os.environ.get("KEY") == "value"

    def test_keeps_fragment_without_space(self, tmp_path: Path, monkeypatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value#fragment\n")
        monkeypatch.delenv("KEY", raising=False)
        _load_dotenv(env_file)
        assert os.environ.get("KEY") == "value#fragment"

    def test_strips_whitespace(self, tmp_path: Path, monkeypatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY= value \n")
        monkeypatch.delenv("KEY", raising=False)
        _load_dotenv(env_file)
        assert os.environ.get("KEY") == "value"

    def test_comment_after_fragment(self, tmp_path: Path, monkeypatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=ab#cd # 注释\n")
        monkeypatch.delenv("KEY", raising=False)
        _load_dotenv(env_file)
        assert os.environ.get("KEY") == "ab#cd"

    def test_skips_full_line_comment_and_blank(self, tmp_path: Path, monkeypatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\n\nKEY=value\n")
        monkeypatch.delenv("KEY", raising=False)
        _load_dotenv(env_file)
        assert os.environ.get("KEY") == "value"

    def test_does_not_overwrite_existing_env(self, tmp_path: Path, monkeypatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING=new_value\n")
        monkeypatch.setenv("EXISTING", "old_value")
        _load_dotenv(env_file)
        assert os.environ["EXISTING"] == "old_value"
