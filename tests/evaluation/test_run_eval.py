"""run_eval.py 评分逻辑测试"""

from __future__ import annotations

from tests.evaluation.run_eval import (
    calculate_answer_match,
    calculate_tool_match,
)


def test_tool_match_exact():
    """测试工具精确匹配"""
    expected = ["semantic_search", "graph_query"]
    actual = ["semantic_search", "graph_query", "get_context"]
    assert calculate_tool_match(expected, actual) == 1.0


def test_tool_match_exact_only():
    """测试工具精确匹配（没有额外工具）"""
    expected = ["semantic_search"]
    actual = ["semantic_search"]
    assert calculate_tool_match(expected, actual) == 1.0


def test_tool_match_partial():
    """测试工具部分匹配（实际缺少预期工具）"""
    expected = ["semantic_search", "graph_query"]
    actual = ["semantic_search"]
    assert calculate_tool_match(expected, actual) == 0.5


def test_answer_match_exact():
    """测试精确答案匹配"""
    expected = {"type": "exact", "value": "54"}
    actual = "知识图谱中有 54 个 ConceptEntity"
    assert calculate_answer_match(expected, actual) == 1.0


def test_answer_match_exact_fail():
    """测试精确答案不匹配"""
    expected = {"type": "exact", "value": "100"}
    actual = "知识图谱中有 54 个 ConceptEntity"
    assert calculate_answer_match(expected, actual) == 0.0


def test_answer_match_contains():
    """测试包含匹配"""
    expected = {"type": "contains", "value": "aligner.py"}
    actual = "ConceptAligner 类定义在 aligner.py 文件中"
    assert calculate_answer_match(expected, actual) == 1.0


def test_answer_match_contains_fail():
    """测试包含不匹配"""
    expected = {"type": "contains", "value": "xyz.py"}
    actual = "ConceptAligner 类定义在 aligner.py 文件中"
    assert calculate_answer_match(expected, actual) == 0.0


def test_answer_match_list_full():
    """测试列表完全匹配"""
    expected = {"type": "list", "value": ["Pipeline", "Cache", "Strategy"]}
    actual = "使用了 Pipeline Pattern 和 Cache Pattern，还有 Strategy Pattern"
    score = calculate_answer_match(expected, actual)
    assert score == 1.0


def test_answer_match_list_partial():
    """测试列表部分匹配"""
    expected = {"type": "list", "value": ["Pipeline", "Cache", "Strategy"]}
    actual = "使用了 Pipeline Pattern"
    score = calculate_answer_match(expected, actual)
    assert score == 1.0 / 3.0


def test_answer_match_fuzzy():
    """测试模糊匹配"""
    expected = {"type": "fuzzy", "keywords": ["Pipeline", "Cache", "Strategy"]}
    actual = "设计中包含了 Pipeline 模式，但没有 Cache"
    score = calculate_answer_match(expected, actual)
    assert score == 2.0 / 3.0


def test_answer_match_unknown_type():
    """测试未知答案类型"""
    expected = {"type": "unknown", "value": "test"}
    actual = "any answer"
    assert calculate_answer_match(expected, actual) == 0.0
