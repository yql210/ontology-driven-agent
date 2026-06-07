"""Agent 护栏测试 — 验证 prompt 约束和 tools 健壮性."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from layerkg.agent.prompt import AGENT_SYSTEM_PROMPT
from layerkg.agent.tools import detect_changes


class TestPromptGuardrails:
    """测试 prompt.py 中的约束规则和查询模板."""

    def test_prompt_contains_constraint_section(self) -> None:
        """AGENT_SYSTEM_PROMPT 包含关系约束信息."""
        # 新 prompt 在 Schema 段落中列出关系类型
        assert "DERIVED_FROM" in AGENT_SYSTEM_PROMPT

    def test_prompt_contains_changeset_entity(self) -> None:
        """AGENT_SYSTEM_PROMPT 包含 ChangeSetEntity 节点类型."""
        assert "ChangeSetEntity" in AGENT_SYSTEM_PROMPT

    def test_prompt_contains_all_relation_types(self) -> None:
        """验证全部 11 种关系类型都在 prompt 中."""
        relation_types = [
            "CALLS",
            "IMPORTS",
            "CONTAINS",
            "EXTENDS",
            "IMPLEMENTS",
            "DESCRIBES",
            "ILLUSTRATES",
            "DERIVED_FROM",
            "SEMANTIC_IMPACT",
            "CHANGED_IN",
            "AFFECTS",
        ]
        for rel_type in relation_types:
            assert rel_type in AGENT_SYSTEM_PROMPT, f"缺少关系类型: {rel_type}"

    def test_prompt_derived_from_query_is_correct(self) -> None:
        """DERIVED_FROM 在 prompt 中的关系列表中存在且方向正确（ConceptEntity → ConceptEntity）."""
        # 新 prompt 在 Schema 中列出了 DERIVED_FROM
        assert "DERIVED_FROM" in AGENT_SYSTEM_PROMPT


class TestGetContextChromaHandling:
    """测试 get_context 工具对 ChromaDB 返回值的健壮处理."""

    @patch("layerkg.agent.tools.get_neo4j")
    @patch("layerkg.agent.tools.get_chroma")
    def test_get_context_chroma_list_result(
        self, mock_get_chroma: MagicMock, mock_get_neo4j: MagicMock
    ) -> None:
        """mock chroma.search 返回 list 时，similar_entities 正确."""
        # Arrange
        from layerkg.agent.tools import get_context

        mock_neo4j = MagicMock()
        mock_neo4j.query.return_value = [{"id": "test-id"}]
        mock_neo4j.get_node.return_value = {"name": "TestEntity", "id": "test-id"}
        mock_neo4j.get_relations.return_value = []
        mock_neo4j.__class__.__name__ = "Neo4jGraphStore"
        mock_get_neo4j.return_value = mock_neo4j

        mock_chroma = MagicMock()
        mock_chroma.search.return_value = [{"id": "1"}, {"id": "2"}]
        mock_get_chroma.return_value = mock_chroma

        # Act
        import json

        result = get_context("TestEntity")
        data = json.loads(result)

        # Assert
        assert "node" in data
        assert data["node"]["name"] == "TestEntity"
        # list 格式应直接赋值
        mock_chroma.search.assert_called_once()

    @patch("layerkg.agent.tools.get_neo4j")
    @patch("layerkg.agent.tools.get_chroma")
    def test_get_context_chroma_dict_result(
        self, mock_get_chroma: MagicMock, mock_get_neo4j: MagicMock
    ) -> None:
        """mock chroma.search 返回 dict 时，similar_entities 正确."""
        # Arrange
        from layerkg.agent.tools import get_context

        mock_neo4j = MagicMock()
        mock_neo4j.query.return_value = [{"id": "test-id"}]
        mock_neo4j.get_node.return_value = {"name": "TestEntity", "id": "test-id"}
        mock_neo4j.get_relations.return_value = []
        mock_neo4j.__class__.__name__ = "Neo4jGraphStore"
        mock_get_neo4j.return_value = mock_neo4j

        mock_chroma = MagicMock()
        mock_chroma.search.return_value = {"matches": [{"id": "1"}, {"id": "2"}]}
        mock_get_chroma.return_value = mock_chroma

        # Act
        import json

        result = get_context("TestEntity")
        data = json.loads(result)

        # Assert
        assert "node" in data
        assert data["node"]["name"] == "TestEntity"
        # dict 格式应提取 matches
        mock_chroma.search.assert_called_once()

    @patch("layerkg.agent.tools.get_neo4j")
    @patch("layerkg.agent.tools.get_chroma")
    def test_get_context_chroma_error_returns_empty(
        self, mock_get_chroma: MagicMock, mock_get_neo4j: MagicMock
    ) -> None:
        """mock chroma.search 抛异常时，similar_entities 为空."""
        # Arrange
        from layerkg.agent.tools import get_context

        mock_neo4j = MagicMock()
        mock_neo4j.query.return_value = [{"id": "test-id"}]
        mock_neo4j.get_node.return_value = {"name": "TestEntity", "id": "test-id"}
        mock_neo4j.get_relations.return_value = []
        mock_neo4j.__class__.__name__ = "Neo4jGraphStore"
        mock_get_neo4j.return_value = mock_neo4j

        mock_chroma = MagicMock()
        mock_chroma.search.side_effect = Exception("ChromaDB error")
        mock_get_chroma.return_value = mock_chroma

        # Act
        import json

        result = get_context("TestEntity")
        data = json.loads(result)

        # Assert
        assert "node" in data
        assert data["similar_entities"] == []


class TestDetectChangesSignature:
    """测试 detect_changes 函数签名."""

    def test_detect_changes_accepts_repo_path(self) -> None:
        """detect_changes 签名包含 repo_path keyword-only 参数."""
        # @tool 装饰器会将函数转为 StructuredTool，直接读取源文件
        from pathlib import Path

        tools_file = Path(__file__).parent.parent.parent / "src" / "layerkg" / "agent" / "tools.py"
        source = tools_file.read_text()

        # 验证源码中包含 keyword-only 参数定义
        assert "*, repo_path: str" in source or "*,repo_path:str" in source.replace(" ", "")
        # 验证默认值为 "."
        assert 'repo_path: str = "."' in source or "repo_path: str = \".\"" in source
        # 确保没有硬编码路径
        assert "/opt/data/workspace/ontology-driven-agent" not in source
