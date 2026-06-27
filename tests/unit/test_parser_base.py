from __future__ import annotations

import pytest

from layerkg.domain.schema import CodeEntity
from layerkg.parsing.parser.base import BaseParser, ExtractedRelation, ParseResult


def test_parse_result_creation_with_code_entities():
    """测试创建带 CodeEntity 列表的 ParseResult。"""
    entities = [
        CodeEntity(name="foo", entity_type="function", file_path="/path/to/file.py"),
        CodeEntity(name="Bar", entity_type="class", file_path="/path/to/file.py"),
    ]
    relations = [
        ExtractedRelation(
            source_name="Bar",
            source_type="class",
            target_name="foo",
            target_type="function",
            relation_type="contains",
            file_path="/path/to/file.py",
        )
    ]

    result = ParseResult(
        file_path="/path/to/file.py",
        entities=entities,
        relations=relations,
        language="python",
    )

    assert result.file_path == "/path/to/file.py"
    assert len(result.entities) == 2
    assert result.entities[0].name == "foo"
    assert result.entities[0].entity_type == "function"
    assert result.entities[1].name == "Bar"
    assert result.entities[1].entity_type == "class"
    assert len(result.relations) == 1
    assert result.relations[0].source_name == "Bar"
    assert result.relations[0].relation_type == "contains"
    assert result.language == "python"
    assert result.error is None


def test_parse_result_with_error():
    """测试带错误信息的 ParseResult。"""
    error_msg = "Syntax error at line 42"

    result = ParseResult(
        file_path="/broken/file.py",
        error=error_msg,
    )

    assert result.file_path == "/broken/file.py"
    assert result.error == error_msg
    assert result.entities == []
    assert result.relations == []


def test_parse_result_defaults():
    """测试 ParseResult 默认值为空列表。"""
    result = ParseResult(file_path="/test/file.py")

    assert result.file_path == "/test/file.py"
    assert result.entities == []
    assert result.relations == []
    assert result.language == "python"
    assert result.error is None


def test_base_parser_is_abstract():
    """测试 BaseParser 不能直接实例化。"""
    with pytest.raises(TypeError, match="abstract"):
        BaseParser()


def test_extracted_relation_creation():
    """测试 ExtractedRelation 创建。"""
    relation = ExtractedRelation(
        source_name="MyClass",
        source_type="class",
        target_name="BaseClass",
        target_type="class",
        relation_type="extends",
        file_path="/path/to/file.py",
    )

    assert relation.source_name == "MyClass"
    assert relation.source_type == "class"
    assert relation.target_name == "BaseClass"
    assert relation.target_type == "class"
    assert relation.relation_type == "extends"
    assert relation.file_path == "/path/to/file.py"
