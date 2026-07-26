"""测试 prompt.py 按后端动态切换查询语言指南。

V4 Phase 2: graph_query 工具的查询语言按 graph_backend 切换：
- neo4j → Cypher 提示
- nebula → nGQL 提示（含 Tag 前缀规则）
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from types import ModuleType

import pytest


def _reload_prompt_with_backend(backend: str) -> ModuleType:
    """以指定 graph_backend 重新加载 prompt 模块，并保持 env 直到调用方清理。

    prompt.py 在 import 时读取 config 构建 AGENT_SYSTEM_PROMPT，
    helper 函数运行时再次读取 env，因此 reload 后必须保持 env 设置。
    """
    os.environ["ONTOAGENT_GRAPH_BACKEND"] = backend
    import ontoagent.agent.prompt as prompt_mod

    importlib.reload(prompt_mod)
    return prompt_mod


def _clear_backend_env(original: str | None) -> None:
    """还原 ONTOAGENT_GRAPH_BACKEND 环境变量。"""
    if original is None:
        os.environ.pop("ONTOAGENT_GRAPH_BACKEND", None)
    else:
        os.environ["ONTOAGENT_GRAPH_BACKEND"] = original


@pytest.fixture
def restore_prompt() -> Callable[[], None]:
    """测试结束后恢复 prompt 模块默认状态，避免污染后续测试。"""
    original = os.environ.get("ONTOAGENT_GRAPH_BACKEND")

    def _restore() -> None:
        _clear_backend_env(original)
        import ontoagent.agent.prompt as prompt_mod

        importlib.reload(prompt_mod)

    return _restore


def test_nebula_backend_prompt_contains_ngql_rules(restore_prompt: Callable[[], None]) -> None:
    """nebula 后端的 prompt 应包含 nGQL 关键规则。"""
    try:
        mod = _reload_prompt_with_backend("nebula")
        prompt_text = mod.AGENT_SYSTEM_PROMPT

        assert "nGQL" in prompt_text
        assert "NebulaGraph" in prompt_text
        assert "Tag 前缀" in prompt_text
        assert "tags(n)" in prompt_text
    finally:
        restore_prompt()


def test_neo4j_backend_prompt_contains_cypher_rules(restore_prompt: Callable[[], None]) -> None:
    """neo4j 后端的 prompt 应包含 Cypher 提示。"""
    try:
        mod = _reload_prompt_with_backend("neo4j")
        prompt_text = mod.AGENT_SYSTEM_PROMPT

        assert "Cypher" in prompt_text
        assert "Neo4j" in prompt_text
        assert "Tag 前缀" not in prompt_text
    finally:
        restore_prompt()


def test_prompt_includes_tag_prefix_rule(restore_prompt: Callable[[], None]) -> None:
    """nGQL prompt 必须包含 Tag 前缀属性访问规则（避免 LLM 写错）。"""
    try:
        mod = _reload_prompt_with_backend("nebula")
        prompt_text = mod.AGENT_SYSTEM_PROMPT

        assert "Tag 前缀" in prompt_text
        assert "n.CodeEntity.name" in prompt_text
    finally:
        restore_prompt()


def test_prompt_includes_schema_section(restore_prompt: Callable[[], None]) -> None:
    """两种后端的 prompt 都应保留通用 Schema 段（9 实体）。"""
    try:
        for backend in ("neo4j", "nebula"):
            mod = _reload_prompt_with_backend(backend)
            prompt_text = mod.AGENT_SYSTEM_PROMPT

            assert "CodeEntity" in prompt_text
            assert "CALLS" in prompt_text
            assert "Schema" in prompt_text
    finally:
        restore_prompt()


def test_query_lang_name_helper_returns_expected(restore_prompt: Callable[[], None]) -> None:
    """_get_query_lang_name() 应返回正确的人类可读名称。"""
    try:
        mod = _reload_prompt_with_backend("nebula")
        assert mod._get_query_lang_name() == "nGQL"

        mod = _reload_prompt_with_backend("neo4j")
        assert mod._get_query_lang_name() == "Cypher"
    finally:
        restore_prompt()


def test_load_graph_query_guide_returns_nonempty(restore_prompt: Callable[[], None]) -> None:
    """_load_graph_query_guide() 必须返回非空字符串。"""
    try:
        mod = _reload_prompt_with_backend("nebula")
        guide = mod._load_graph_query_guide()
        assert isinstance(guide, str)
        assert len(guide) > 100
    finally:
        restore_prompt()
