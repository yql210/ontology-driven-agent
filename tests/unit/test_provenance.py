"""数据溯源与可信度工具测试。"""

from __future__ import annotations

import pytest

from ontoagent.domain.exceptions import SchemaValidationError
from ontoagent.domain.provenance import (
    PROVENANCE_SOURCES,
    add_provenance,
    clamp_confidence,
    validate_confidence,
    validate_provenance_source,
)


class TestValidateConfidence:
    """置信度校验测试。"""

    def test_validate_confidence_valid_boundary_low(self) -> None:
        """0.0 通过校验。"""
        validate_confidence(0.0)  # 不抛异常

    def test_validate_confidence_valid_boundary_high(self) -> None:
        """1.0 通过校验。"""
        validate_confidence(1.0)  # 不抛异常

    def test_validate_confidence_valid_mid(self) -> None:
        """0.5 通过校验。"""
        validate_confidence(0.5)  # 不抛异常

    def test_validate_confidence_invalid_negative(self) -> None:
        """-0.1 抛 SchemaValidationError。"""
        with pytest.raises(SchemaValidationError, match=r"confidence must be a float in \[0.0, 1.0\]"):
            validate_confidence(-0.1)

    def test_validate_confidence_invalid_overflow(self) -> None:
        """1.5 抛 SchemaValidationError。"""
        with pytest.raises(SchemaValidationError, match=r"confidence must be a float in \[0.0, 1.0\]"):
            validate_confidence(1.5)

    def test_validate_confidence_invalid_type(self) -> None:
        """字符串 'high' 抛 SchemaValidationError。"""
        with pytest.raises(SchemaValidationError, match=r"confidence must be a float in \[0.0, 1.0\]"):
            validate_confidence("high")  # type: ignore


class TestValidateProvenanceSource:
    """来源校验测试。"""

    def test_validate_provenance_source_valid_ast_parser(self) -> None:
        """ast_parser 通过校验。"""
        validate_provenance_source("ast_parser")  # 不抛异常

    def test_validate_provenance_source_valid_llm_extraction(self) -> None:
        """llm_extraction 通过校验。"""
        validate_provenance_source("llm_extraction")  # 不抛异常

    def test_validate_provenance_source_valid_clustering(self) -> None:
        """clustering 通过校验。"""
        validate_provenance_source("clustering")  # 不抛异常

    def test_validate_provenance_source_valid_manual(self) -> None:
        """manual 通过校验。"""
        validate_provenance_source("manual")  # 不抛异常

    def test_validate_provenance_source_valid_imported(self) -> None:
        """imported 通过校验。"""
        validate_provenance_source("imported")  # 不抛异常

    def test_validate_provenance_source_invalid(self) -> None:
        """unknown 抛 SchemaValidationError。"""
        with pytest.raises(SchemaValidationError, match="provenance_source must be one of"):
            validate_provenance_source("unknown")


class TestAddProvenance:
    """添加溯源字段测试。"""

    def test_add_provenance_default_values(self) -> None:
        """默认值：source=ast_parser, confidence=1.0, extracted_at 非空。"""
        result = add_provenance()

        assert result["provenance_source"] == "ast_parser"
        assert result["confidence"] == 1.0
        assert result["extracted_at"]
        assert isinstance(result["extracted_at"], str)

    def test_add_provenance_custom_source_confidence(self) -> None:
        """自定义 source 和 confidence。"""
        result = add_provenance(source="llm_extraction", confidence=0.85)

        assert result["provenance_source"] == "llm_extraction"
        assert result["confidence"] == 0.85
        assert result["extracted_at"]

    def test_add_provenance_none_input(self) -> None:
        """properties=None 返回新 dict。"""
        result = add_provenance(properties=None)

        assert isinstance(result, dict)
        assert result["provenance_source"] == "ast_parser"

    def test_add_provenance_preserves_existing(self) -> None:
        """已有属性保留。"""
        original = {"name": "foo", "value": 42}
        result = add_provenance(properties=original, source="manual", confidence=0.9)

        assert result["name"] == "foo"
        assert result["value"] == 42
        assert result["provenance_source"] == "manual"
        assert result["confidence"] == 0.9
        # 原始 dict 不被修改
        assert "provenance_source" not in original

    def test_add_provenance_custom_timestamp(self) -> None:
        """传入 extracted_at 使用传入值。"""
        custom_time = "2024-01-01T00:00:00+00:00"
        result = add_provenance(extracted_at=custom_time)

        assert result["extracted_at"] == custom_time

    def test_add_provenance_full_properties(self) -> None:
        """完整参数测试。"""
        props = {"id": "entity-1", "name": "test"}
        result = add_provenance(
            properties=props,
            source="clustering",
            confidence=0.75,
            extracted_at="2024-05-19T12:00:00Z",
        )

        assert result["id"] == "entity-1"
        assert result["name"] == "test"
        assert result["provenance_source"] == "clustering"
        assert result["confidence"] == 0.75
        assert result["extracted_at"] == "2024-05-19T12:00:00Z"


class TestClampConfidence:
    """置信度 clamp 测试。"""

    def test_clamp_confidence_normal(self) -> None:
        """0.5 → 0.5。"""
        assert clamp_confidence(0.5) == 0.5

    def test_clamp_confidence_overflow(self) -> None:
        """1.5 → 1.0。"""
        assert clamp_confidence(1.5) == 1.0

    def test_clamp_confidence_underflow(self) -> None:
        """-0.5 → 0.0。"""
        assert clamp_confidence(-0.5) == 0.0

    def test_clamp_confidence_none_uses_default(self) -> None:
        """None → 0.8（默认值）。"""
        assert clamp_confidence(None) == 0.8

    def test_clamp_confidence_none_custom_default(self) -> None:
        """None → 0.5（自定义默认值）。"""
        assert clamp_confidence(None, default=0.5) == 0.5

    def test_clamp_confidence_boundary_low(self) -> None:
        """0.0 → 0.0。"""
        assert clamp_confidence(0.0) == 0.0

    def test_clamp_confidence_boundary_high(self) -> None:
        """1.0 → 1.0。"""
        assert clamp_confidence(1.0) == 1.0


class TestProvenanceSources:
    """PROVENANCE_SOURCES 常量测试。"""

    def test_provenance_sources_not_empty(self) -> None:
        """PROVENANCE_SOURCES 非空。"""
        assert len(PROVENANCE_SOURCES) == 5

    def test_provenance_sources_contains_expected(self) -> None:
        """包含所有预期来源。"""
        expected = {"ast_parser", "llm_extraction", "clustering", "manual", "imported"}
        assert expected == PROVENANCE_SOURCES

    def test_provenance_sources_is_frozenset(self) -> None:
        """PROVENANCE_SOURCES 是 frozenset（不可变）。"""
        assert isinstance(PROVENANCE_SOURCES, frozenset)
