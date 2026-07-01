"""Capability Extractor — 从代码实体逆向业务能力契约。

Phase 1: 规则驱动，从 API 入口函数的签名 + docstring + 路径信息提取。
不做 LLM 调用，纯静态分析。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ontoagent.domain.schema import CapabilityEntity, CodeEntity

__all__ = ["CapabilityExtractor", "_extract_http_path_prefix", "_parse_type_annotation"]

# API 入口类别：这些 entry_category 的函数视为业务能力
_CAPABILITY_ENTRY_CATEGORIES: frozenset[str] = frozenset({"http_api", "rpc_service"})

# 用于匹配返回类型标注的正则
_RETURN_TYPE_RE = re.compile(r"->\s*(.+?)(?:\s*:|$)")


def _extract_http_path_prefix(path_or_file: str | None) -> str:
    """从 HTTP 路径或文件路径提取业务域前缀。

    如 ``/api/credit/validate`` → ``"credit"``，
    ``/orders`` → ``"orders"``，
    ``/app/api/payment.py`` → ``"payment"``。

    Args:
        path_or_file: URL 路径或文件路径。若为空或 None 返回 ``"unknown"``。

    Returns:
        业务域字符串。
    """
    if not path_or_file:
        return "unknown"

    # 若是文件路径，提取文件名（不含扩展名）
    if path_or_file.endswith(".py"):
        return Path(path_or_file).stem

    # 若是 URL 路径，提取第二段（跳过 /api 前缀）
    segments = [s for s in path_or_file.strip("/").split("/") if s]
    if not segments:
        return "root"
    if segments[0] == "api":
        return segments[1] if len(segments) > 1 else "api"
    return segments[0]


def _parse_type_annotation(raw: str | None) -> str:
    """清理 Python 类型注解，移除 Optional/Union 噪音。

    ``Optional[str]`` → ``"str"``、
    ``str | None`` → ``"str"``、
    ``None`` → ``"any"``。

    Args:
        raw: 原始类型注解字符串。

    Returns:
        清理后的类型名。
    """
    if not raw:
        return "any"

    # strip Optional[...] 包装
    cleaned = re.sub(r"Optional\[(.*?)\]", r"\1", raw)
    # strip | None
    cleaned = re.sub(r"\s*\|\s*None", "", cleaned)
    # strip quotes
    cleaned = cleaned.strip().strip("'\"")
    return cleaned or "any"


def _extract_param_contract(parameters_json: str | None) -> dict[str, str]:
    """从 CodeEntity.parameters JSON 提取输入契约。

    格式: ``["card_number: str", "amount: int"]``。

    Args:
        parameters_json: JSON 字符串（可为 None）。

    Returns:
        ``{参数名: 类型}`` 字典。
    """
    if not parameters_json:
        return {}

    try:
        params = json.loads(parameters_json)
    except (json.JSONDecodeError, TypeError):
        return {}

    contract: dict[str, str] = {}
    for param in params:
        if not isinstance(param, str):
            continue
        if ":" in param:
            name, type_ = param.split(":", 1)
            contract[name.strip()] = _parse_type_annotation(type_.strip())
        else:
            contract[param.strip()] = "any"
    return contract


def _extract_return_type(source: str | None) -> str:
    """从函数源代码提取返回类型标注。

    Args:
        source: 源代码字符串。

    Returns:
        返回类型名，无标注时返回 ``"any"``。
    """
    if not source:
        return "any"
    m = _RETURN_TYPE_RE.search(source)
    if not m:
        return "any"
    return _parse_type_annotation(m.group(1))


def _extract_keywords(name: str, docstring: str | None) -> list[str]:
    """从函数名和 docstring 提取关键词。

    Args:
        name: 函数名。
        docstring: 文档字符串。

    Returns:
        关键词列表。
    """
    keywords: list[str] = []
    # 从函数名按 _ 和下划线拆分
    parts = name.replace("_", " ").split()
    keywords.extend(parts)

    if docstring:
        # 取 docstring 前 3 个有意义的词
        words = [w for w in docstring.split() if len(w) > 1 and not w.startswith("@")]
        keywords.extend(words[:3])

    # 去重保持顺序
    seen: set[str] = set()
    result: list[str] = []
    for kw in keywords:
        if kw.lower() not in seen:
            result.append(kw.lower())
            seen.add(kw.lower())
    return result[:10]


def _extract_description(docstring: str | None, name: str) -> str:
    """从 docstring 提取能力描述，兜底为函数名。

    Args:
        docstring: 文档字符串。
        name: 函数名。

    Returns:
        描述文本。
    """
    if docstring:
        line = docstring.strip().split("\n")[0].strip()
        if line:
            return line
    return f"业务能力: {name}"


def _extract_domain(file_path: str | None, name: str) -> str:
    """从文件路径提取业务域，兜底从函数名推断。

    Args:
        file_path: 文件路径。
        name: 函数名。

    Returns:
        业务域字符串。
    """
    domain = _extract_http_path_prefix(file_path)
    if domain == "unknown":
        # 从函数名推断：如 validate_credit_card → credit
        parts = name.split("_")
        if len(parts) >= 2:
            domain = parts[1] if parts[0] in ("validate", "create", "get", "update", "delete", "process") else parts[0]
    return domain


class CapabilityExtractor:
    """从 CodeEntity 逆向 CapabilityEntity。

    仅处理 entry_category 为 http_api / rpc_service 的实体。
    """

    def extract(self, entity: CodeEntity) -> CapabilityEntity | None:
        """从单个 CodeEntity 提取 CapabilityEntity。

        Args:
            entity: 代码实体。

        Returns:
            CapabilityEntity，若非入口函数则返回 None。
        """
        if entity.entry_category not in _CAPABILITY_ENTRY_CATEGORIES:
            return None

        name = entity.name
        input_contract = _extract_param_contract(entity.parameters)
        output_contract: dict[str, str] = {}

        # 若有返回类型标注，加入 output_contract
        return_type = _extract_return_type(entity.source)
        if return_type and return_type != "any":
            output_contract[name + "_result"] = return_type

        domain = _extract_domain(entity.file_path, name)
        description = _extract_description(entity.docstring, name)
        keywords = _extract_keywords(name, entity.docstring)
        realized_by = [entity.id] if entity.id else []

        return CapabilityEntity(
            name=name,
            business_domain=domain,
            description=description,
            input_contract=input_contract,
            output_contract=output_contract,
            keywords=keywords,
            realized_by=realized_by,
        )
