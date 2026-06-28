"""Function registry — decorator registration + lookup."""

from __future__ import annotations

_registry: dict[str, object] = {}
_meta: dict[str, dict[str, str]] = {}

# YAML danger level cache (populated by load_danger_levels_from_yaml)
_loaded_danger_levels: dict[str, str] = {}


def register_function(name: str, **meta):
    """Decorator: register a Function to the global registry.

    Optional metadata: danger_level ("read"|"read_sensitive"|"write"|"admin")
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


def get_function_meta(name: str) -> dict[str, str]:
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


def load_danger_levels_from_yaml() -> dict[str, str]:
    """Load function danger levels from config YAML file.

    Returns a dict with function_name → danger_level, plus "__default__" key.
    """
    from pathlib import Path

    import yaml

    yaml_path = Path(__file__).parent.parent.parent / "config" / "function_danger_levels.yaml"
    if not yaml_path.exists():
        return {}

    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    default = data.get("default", "read")
    levels = data.get("functions", {})

    _loaded_danger_levels.clear()
    _loaded_danger_levels["__default__"] = default
    _loaded_danger_levels.update(levels)
    return dict(_loaded_danger_levels)


def get_danger_level(func_name: str) -> str:
    """Get danger_level for a function.

    Priority: YAML > decorator parameter > default ("read").
    This allows YAML to override decorator-declared values.
    """
    if not _loaded_danger_levels:
        load_danger_levels_from_yaml()

    if func_name in _loaded_danger_levels:
        return _loaded_danger_levels[func_name]

    meta = _meta.get(func_name, {})
    if "danger_level" in meta:
        return meta["danger_level"]

    return _loaded_danger_levels.get("__default__", "read")
