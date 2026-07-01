"""Capability Extractor 单元测试 — Phase 1 TDD RED."""

from __future__ import annotations

import pytest

from ontoagent.domain.schema import CapabilityEntity, CodeEntity
from ontoagent.parsing.extractor.capability_extractor import (
    CapabilityExtractor,
    _extract_http_path_prefix,
    _parse_type_annotation,
)


# ── 辅助函数 ──────────────────────────────────────────────────────


def _make_code_entity(
    name: str,
    *,
    entity_type: str = "function",
    entry_category: str | None = None,
    docstring: str | None = None,
    parameters: str | None = None,
    file_path: str | None = None,
    source: str | None = None,
    decorators: list[str] | None = None,
) -> CodeEntity:
    """构造测试用 CodeEntity。"""
    entity = CodeEntity(name=name, entity_type=entity_type)
    entity.entry_category = entry_category
    entity.docstring = docstring
    entity.parameters = parameters
    entity.file_path = file_path
    entity.source = source
    # Decorators 暂时通过 source 传递（后续 pipelined 从 parser 获取）
    return entity


# ── 单元测试：类型注解解析 ────────────────────────────────────────


@pytest.mark.unit
class TestParseTypeAnnotation:
    def test_simple_type(self) -> None:
        assert _parse_type_annotation("str") == "str"

    def test_optional_type(self) -> None:
        assert _parse_type_annotation("str | None") == "str"
        assert _parse_type_annotation("Optional[str]") == "str"

    def test_list_type(self) -> None:
        assert _parse_type_annotation("list[str]") == "list[str]"

    def test_complex_type(self) -> None:
        assert _parse_type_annotation("dict[str, int]") == "dict[str, int]"

    def test_empty(self) -> None:
        assert _parse_type_annotation("") == "any"
        assert _parse_type_annotation(None) == "any"


# ── 单元测试：HTTP 路径前缀提取 ──────────────────────────────────


@pytest.mark.unit
class TestExtractHttpPathPrefix:
    def test_standard_api_path(self) -> None:
        prefix = _extract_http_path_prefix("/api/credit/validate")
        assert prefix == "credit"

    def test_single_segment(self) -> None:
        prefix = _extract_http_path_prefix("/orders")
        assert prefix == "orders"

    def test_root_path(self) -> None:
        prefix = _extract_http_path_prefix("/")
        assert prefix == "root"

    def test_no_path(self) -> None:
        prefix = _extract_http_path_prefix("")
        assert prefix == "unknown"

    def test_none(self) -> None:
        prefix = _extract_http_path_prefix(None)
        assert prefix == "unknown"


# ── CapabilityExtractor 集成测试 ─────────────────────────────────


@pytest.mark.unit
class TestCapabilityExtractor:
    def test_http_api_function_produces_capability(self) -> None:
        """HTTP API 入口函数 → CapabilityEntity。"""
        entity = _make_code_entity(
            name="validate_credit_card",
            entry_category="http_api",
            docstring="校验信用卡号的有效性和额度。",
            parameters='["card_number: str", "amount: int"]',
            file_path="/app/api/credit.py",
        )
        extractor = CapabilityExtractor()
        result = extractor.extract(entity)

        assert result is not None
        assert isinstance(result, CapabilityEntity)
        assert result.business_domain == "credit"  # 从路径 /api/credit.py 提取
        assert "card_number" in result.input_contract
        assert "amount" in result.input_contract

    def test_rpc_service_function_produces_capability(self) -> None:
        """RPC 服务入口 → CapabilityEntity。"""
        entity = _make_code_entity(
            name="create_order",
            entry_category="rpc_service",
            docstring="创建新订单。",
            parameters='["payload: dict"]',
            file_path="/app/services/order_service.py",
        )
        extractor = CapabilityExtractor()
        result = extractor.extract(entity)

        assert result is not None
        assert "order" in result.business_domain  # order_service → order

    def test_non_entry_function_returns_none(self) -> None:
        """非入口函数 → None。"""
        entity = _make_code_entity(
            name="helper_func",
            entry_category=None,
        )
        extractor = CapabilityExtractor()
        result = extractor.extract(entity)

        assert result is None

    def test_function_without_docstring_still_extracts(self) -> None:
        """无 docstring 时仍产出 Capability，用函数名做 description。"""
        entity = _make_code_entity(
            name="process_payment",
            entry_category="http_api",
            docstring=None,
            parameters="[]",
            file_path="/app/api/payment.py",
        )
        extractor = CapabilityExtractor()
        result = extractor.extract(entity)

        assert result is not None
        assert result.name == "process_payment"
        assert result.description  # 有兜底描述

    def test_function_with_return_annotation_has_output_contract(self) -> None:
        """有返回类型标注 → output_contract。"""
        entity = _make_code_entity(
            name="get_status",
            entry_category="http_api",
            docstring="查询状态。",
            parameters="[]",
            file_path="/app/api/status.py",
            source="def get_status() -> dict[str, bool]:",
        )
        extractor = CapabilityExtractor()
        result = extractor.extract(entity)

        assert result is not None
        assert len(result.output_contract) > 0  # 有返回类型标注

    def test_multiple_functions_same_domain(self) -> None:
        """同一 domain 下的多个函数各自产出独立 Capability。"""
        e1 = _make_code_entity(
            name="create_order",
            entry_category="http_api",
            docstring="创建订单",
            parameters="[]",
            file_path="/app/api/order.py",
        )
        e2 = _make_code_entity(
            name="cancel_order",
            entry_category="http_api",
            docstring="取消订单",
            parameters="[]",
            file_path="/app/api/order.py",
        )
        extractor = CapabilityExtractor()
        r1 = extractor.extract(e1)
        r2 = extractor.extract(e2)

        assert r1 is not None
        assert r2 is not None
        assert r1.business_domain == r2.business_domain == "order"
        assert r1.name != r2.name
