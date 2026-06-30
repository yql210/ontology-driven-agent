"""Function registry — decorator registration + lookup.

Phase 3a: 单一来源为 ``pipeline/functions.yaml``。加载优先级：
    1. ``pipeline/functions.yaml``（danger_level + capabilities）— 最高
    2. 装饰器 ``register_function(name, danger_level=..., capabilities=...)``
    3. 旧版 ``config/function_danger_levels.yaml``（仅 danger_level）— 向后兼容
    4. 默认值 ``"read"``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

_logger = logging.getLogger(__name__)

_registry: dict[str, object] = {}
_meta: dict[str, dict[str, Any]] = {}

# Phase 3a: 来自 pipeline/functions.yaml 的完整配置缓存
_loaded_function_configs: dict[str, dict[str, Any]] = {}

# 旧版 danger_level 缓存（来自 config/function_danger_levels.yaml）
_loaded_danger_levels: dict[str, str] = {}


def register_function(name: str, **meta: Any):
    """Decorator: register a Function to the global registry.

    可选 metadata: ``danger_level`` / ``capabilities`` / ``description``。
    """

    def decorator(fn):
        if name in _registry:
            raise ValueError(f"Function '{name}' already registered")
        _registry[name] = fn
        _meta[name] = meta
        return fn

    return decorator


def get_function(name: str):
    """Look up a registered Function by name."""
    return _registry.get(name)


def get_function_meta(name: str) -> dict[str, Any]:
    """Get metadata for a registered function."""
    return _meta.get(name, {})


def list_functions() -> list[str]:
    """List all registered Function names."""
    return sorted(_registry.keys())


def clear_registry() -> None:
    """Clear the registry (for testing)."""
    _registry.clear()
    _meta.clear()
    _loaded_danger_levels.clear()
    _loaded_function_configs.clear()


# ---------------------------------------------------------------------------
# Phase 3a: pipeline/functions.yaml — 单一来源
# ---------------------------------------------------------------------------


def _functions_yaml_path() -> Path:
    return Path(__file__).parent.parent.parent / "pipeline" / "functions.yaml"


def load_functions_from_yaml() -> dict[str, dict[str, Any]]:
    """从 ``pipeline/functions.yaml`` 加载 Function 配置。

    Returns:
        ``function_name -> config`` 字典；额外包含 ``"__default__"`` 入口
        携带默认 ``danger_level``。capabilities 为字符串列表
        （如 ``"CodeEntity:UPDATE"``），延迟解析为 ``(resource, op)`` 二元组。
    """
    yaml_path = _functions_yaml_path()
    if not yaml_path.exists():
        return {}

    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        _logger.warning("Failed to parse %s: %s", yaml_path, exc)
        return {}

    default_danger = data.get("default_danger_level", "read")

    configs: dict[str, dict[str, Any]] = {"__default__": {"danger_level": default_danger}}

    for entry in data.get("functions", []) or []:
        name = entry.get("name")
        if not name:
            continue
        configs[name] = {
            "danger_level": entry.get("danger_level", default_danger),
            "capabilities": list(entry.get("capabilities", []) or []),
            "description": entry.get("description", ""),
            "impl": entry.get("impl", ""),
        }

    _loaded_function_configs.clear()
    _loaded_function_configs.update(configs)
    return dict(_loaded_function_configs)


def get_capabilities(func_name: str) -> list[str]:
    """获取 Function 的能力声明（字符串列表）。

    优先级：``pipeline/functions.yaml`` > 装饰器 ``capabilities`` 元数据 > ``[]``。
    """
    if not _loaded_function_configs:
        load_functions_from_yaml()

    cfg = _loaded_function_configs.get(func_name)
    if cfg and cfg.get("capabilities"):
        return list(cfg["capabilities"])

    meta = _meta.get(func_name, {})
    if "capabilities" in meta:
        caps = meta["capabilities"]
        return list(caps) if isinstance(caps, list) else []

    return []


# ---------------------------------------------------------------------------
# Legacy: config/function_danger_levels.yaml（向后兼容）
# ---------------------------------------------------------------------------


def _legacy_danger_yaml_path() -> Path:
    return Path(__file__).parent.parent.parent / "config" / "function_danger_levels.yaml"


def load_danger_levels_from_yaml() -> dict[str, str]:
    """从旧版 ``config/function_danger_levels.yaml`` 加载 danger_level。

    保留用于向后兼容；新代码应使用 ``pipeline/functions.yaml``。
    """
    yaml_path = _legacy_danger_yaml_path()
    if not yaml_path.exists():
        return {}

    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    default = data.get("default", "read")
    levels = data.get("functions", {}) or {}

    _loaded_danger_levels.clear()
    _loaded_danger_levels["__default__"] = default
    _loaded_danger_levels.update(levels)
    return dict(_loaded_danger_levels)


def get_danger_level(func_name: str) -> str:
    """获取 Function 的 danger_level。

    优先级：
        1. ``pipeline/functions.yaml``
        2. 装饰器 ``register_function(..., danger_level=...)``
        3. 旧版 ``config/function_danger_levels.yaml``
        4. 默认 ``"read"``
    """
    if not _loaded_function_configs:
        load_functions_from_yaml()

    cfg = _loaded_function_configs.get(func_name)
    if cfg and cfg.get("danger_level"):
        return cfg["danger_level"]

    meta = _meta.get(func_name, {})
    if "danger_level" in meta:
        return meta["danger_level"]

    if not _loaded_danger_levels:
        load_danger_levels_from_yaml()
    if func_name in _loaded_danger_levels:
        return _loaded_danger_levels[func_name]

    return _loaded_danger_levels.get("__default__", "read")
