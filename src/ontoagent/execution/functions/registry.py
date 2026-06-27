"""Function registry — decorator registration + lookup."""

from __future__ import annotations

_registry: dict[str, object] = {}


def register_function(name: str):
    """Decorator: register a Function to the global registry."""

    def decorator(fn):
        if name in _registry:
            raise ValueError(f"Function '{name}' already registered")
        _registry[name] = fn
        return fn

    return decorator


def get_function(name: str):
    """Look up a registered Function by name."""
    return _registry.get(name)


def list_functions() -> list[str]:
    """List all registered Function names."""
    return sorted(_registry.keys())


def clear_registry() -> None:
    """Clear the registry (for testing)."""
    _registry.clear()
