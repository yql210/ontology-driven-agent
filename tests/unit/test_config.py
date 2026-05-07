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
