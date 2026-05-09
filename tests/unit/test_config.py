from __future__ import annotations

import os

from layerkg.config import LayerKGConfig


def test_llm_model_default_value():
    """llm_model 默认值为 'qwen3.5:9b'。"""
    config = LayerKGConfig()
    assert config.llm_model == "qwen3.5:9b"


def test_llm_model_from_env():
    """from_env 读取 LAYERKG_LLM_MODEL 环境变量。"""
    original = os.getenv("LAYERKG_LLM_MODEL")
    try:
        os.environ["LAYERKG_LLM_MODEL"] = "custom-model"
        config = LayerKGConfig.from_env()
        assert config.llm_model == "custom-model"
    finally:
        if original is None:
            os.environ.pop("LAYERKG_LLM_MODEL", None)
        else:
            os.environ["LAYERKG_LLM_MODEL"] = original


def test_llm_model_explicit():
    """显式设置 llm_model。"""
    config = LayerKGConfig(llm_model="explicit-model")
    assert config.llm_model == "explicit-model"


def test_from_env_doc_extensions():
    """from_env 读取 LAYERKG_BUILD_DOC_EXTENSIONS 环境变量。"""
    original = os.getenv("LAYERKG_BUILD_DOC_EXTENSIONS")
    try:
        os.environ["LAYERKG_BUILD_DOC_EXTENSIONS"] = ".md,.rst,.txt"
        config = LayerKGConfig.from_env()
        assert config.build_doc_extensions == [".md", ".rst", ".txt"]
    finally:
        if original is None:
            os.environ.pop("LAYERKG_BUILD_DOC_EXTENSIONS", None)
        else:
            os.environ["LAYERKG_BUILD_DOC_EXTENSIONS"] = original


def test_from_env_skip_dirs():
    """from_env 读取 LAYERKG_BUILD_SKIP_DIRS 环境变量。"""
    original = os.getenv("LAYERKG_BUILD_SKIP_DIRS")
    try:
        os.environ["LAYERKG_BUILD_SKIP_DIRS"] = ".git,node_modules,dist"
        config = LayerKGConfig.from_env()
        assert config.build_skip_dirs == {".git", "node_modules", "dist"}
    finally:
        if original is None:
            os.environ.pop("LAYERKG_BUILD_SKIP_DIRS", None)
        else:
            os.environ["LAYERKG_BUILD_SKIP_DIRS"] = original


def test_from_env_doc_max_length():
    """from_env 读取 LAYERKG_BUILD_DOC_MAX_LENGTH 环境变量。"""
    original = os.getenv("LAYERKG_BUILD_DOC_MAX_LENGTH")
    try:
        os.environ["LAYERKG_BUILD_DOC_MAX_LENGTH"] = "5000"
        config = LayerKGConfig.from_env()
        assert config.build_doc_max_length == 5000
    finally:
        if original is None:
            os.environ.pop("LAYERKG_BUILD_DOC_MAX_LENGTH", None)
        else:
            os.environ["LAYERKG_BUILD_DOC_MAX_LENGTH"] = original


def test_from_env_defaults():
    """from_env 使用默认值（未设置环境变量时）。"""
    # 清除可能设置的环境变量
    originals = {}
    for key in ("LAYERKG_BUILD_DOC_EXTENSIONS", "LAYERKG_BUILD_SKIP_DIRS", "LAYERKG_BUILD_DOC_MAX_LENGTH"):
        originals[key] = os.getenv(key)
        if originals[key] is None:
            os.environ.pop(key, None)

    try:
        config = LayerKGConfig.from_env()
        assert config.build_doc_extensions == [".md", ".rst"]
        assert ".pytest_cache" in config.build_skip_dirs
        assert "venv" in config.build_skip_dirs
        assert config.build_doc_max_length == 2000
    finally:
        for key, val in originals.items():
            if val is not None:
                os.environ[key] = val
