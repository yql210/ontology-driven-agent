"""Function registry — decorator registration + lookup."""

from __future__ import annotations

_registry: dict[str, object] = {}
_meta: dict[str, dict[str, str]] = {}


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
