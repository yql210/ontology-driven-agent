from __future__ import annotations

import pytest

from ontoagent.domain.shapes import PathExpression

# =============================================================================
# PathExpression.parse() 单元测试
# =============================================================================


@pytest.mark.unit
def test_parse_single_hop():
    """单跳路径：'PROCESSES_DATA -> DataAsset' 解析出 1 个 rel token。"""
    expr = PathExpression.parse("PROCESSES_DATA -> DataAsset")

    assert len(expr.tokens) == 1
    token = expr.tokens[0]
    assert token.kind == "rel"
    assert token.value == "PROCESSES_DATA"
    assert token.quantifier == ""
    assert token.reverse is False
    assert expr.target_label == "DataAsset"


@pytest.mark.unit
def test_parse_variable():
    """量词 '+': 'CALLS+ -> CodeEntity' 解析出 quantifier='+'。"""
    expr = PathExpression.parse("CALLS+ -> CodeEntity")

    assert len(expr.tokens) == 1
    token = expr.tokens[0]
    assert token.kind == "rel"
    assert token.value == "CALLS"
    assert token.quantifier == "+"
    assert token.reverse is False
    assert expr.target_label == "CodeEntity"


@pytest.mark.unit
def test_parse_reverse():
    """反向 '^': '^CALLS -> CodeEntity' 解析出 reverse=True。"""
    expr = PathExpression.parse("^CALLS -> CodeEntity")

    assert len(expr.tokens) == 1
    token = expr.tokens[0]
    assert token.kind == "rel"
    assert token.value == "CALLS"
    assert token.reverse is True
    assert expr.target_label == "CodeEntity"


@pytest.mark.unit
def test_parse_self():
    """零跳 SELF: 'SELF' 解析为单 token 且 is_self()=True。"""
    expr = PathExpression.parse("SELF")

    assert len(expr.tokens) == 1
    token = expr.tokens[0]
    assert token.kind == "self"
    assert token.value == "SELF"
    assert expr.is_self() is True


@pytest.mark.unit
def test_parse_sequence():
    """多跳序列: 'CALLS / IMPLEMENTS -> CodeEntity' 解析出 2 个 rel token。"""
    expr = PathExpression.parse("CALLS / IMPLEMENTS -> CodeEntity")

    assert len(expr.tokens) == 2
    assert expr.target_label == "CodeEntity"

    first = expr.tokens[0]
    assert first.kind == "rel"
    assert first.value == "CALLS"
    assert first.quantifier == ""
    assert first.reverse is False

    second = expr.tokens[1]
    assert second.kind == "rel"
    assert second.value == "IMPLEMENTS"
    assert second.quantifier == ""
    assert second.reverse is False
