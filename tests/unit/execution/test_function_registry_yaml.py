"""Tests for pipeline/functions.yaml loading and capabilities lookup (Phase 3a)."""

from __future__ import annotations

from ontoagent.execution.action_types import ActionContext, FunctionResult
from ontoagent.execution.functions.registry import (
    clear_registry,
    get_capabilities,
    get_danger_level,
    load_functions_from_yaml,
    register_function,
)

# 12 functions actually registered in the codebase
EXPECTED_FUNCTIONS = {
    # general.py
    "query_entity",
    "update_entity",
    "create_entity",
    "create_relation",
    "check_condition",
    "send_notification",
    # builtin.py
    "check_refactor_eligibility",
    "trace_call_chain",
    "generate_api_doc",
    "extract_interface",
    # other modules
    "check_compliance",
    "trace_business_impact",
}

# Expected capability mapping: function_name -> set of "Resource:OP" strings
EXPECTED_CAPABILITIES = {
    "query_entity": {"CodeEntity:READ"},
    "update_entity": {"CodeEntity:UPDATE"},
    "create_entity": {"CodeEntity:CREATE"},
    "create_relation": {"CodeEntity:UPDATE"},
    "check_condition": {"CodeEntity:READ"},
    "send_notification": {"AlertEntity:CREATE"},
    "check_refactor_eligibility": {"CodeEntity:READ"},
    "trace_call_chain": {"CodeEntity:READ"},
    "generate_api_doc": {"CodeEntity:READ", "DocEntity:CREATE"},
    "extract_interface": {"CodeEntity:READ"},
    "check_compliance": {"CodeEntity:READ", "DataAsset:READ", "ComplianceItem:READ"},
    "trace_business_impact": {"CodeEntity:READ", "ServiceEntity:READ"},
}


def _setup():
    """Reset registry caches before each test."""
    clear_registry()


def test_load_functions_from_yaml_returns_all_functions():
    _setup()
    configs = load_functions_from_yaml()
    for name in EXPECTED_FUNCTIONS:
        assert name in configs, f"Function '{name}' missing from functions.yaml"
        entry = configs[name]
        assert "danger_level" in entry, f"'{name}' missing danger_level"
        assert "capabilities" in entry, f"'{name}' missing capabilities"
        assert isinstance(entry["capabilities"], list)
    _setup()


def test_get_capabilities_returns_yaml_values():
    _setup()
    for name, expected in EXPECTED_CAPABILITIES.items():
        caps = set(get_capabilities(name))
        assert caps == expected, f"{name}: expected {expected}, got {caps}"
    _setup()


def test_get_capabilities_unknown_function_returns_empty():
    _setup()
    assert get_capabilities("nonexistent_function") == []
    _setup()


def test_get_capabilities_falls_back_to_decorator_meta():
    """When YAML has no entry, fall back to decorator-declared capabilities."""
    _setup()

    @register_function("decorator_only_fn", capabilities=["CodeEntity:DELETE"])
    def my_fn(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True)

    # Bypass YAML cache by simulating a fresh process: clear caches but keep meta
    from ontoagent.execution.functions import registry as reg_mod

    reg_mod._loaded_function_configs.clear()
    # Patch loader to simulate YAML without this function
    original_loader = reg_mod.load_functions_from_yaml
    reg_mod._loaded_function_configs = {"__default__": {"danger_level": "read"}}

    try:
        caps = get_capabilities("decorator_only_fn")
        assert caps == ["CodeEntity:DELETE"]
    finally:
        reg_mod._loaded_function_configs.clear()
        # restore by re-invoking real loader
        original_loader()
    _setup()


def test_get_danger_level_priority_yaml_over_decorator():
    """YAML value should win over decorator metadata."""
    _setup()

    @register_function("priority_test_fn", danger_level="admin")
    def my_fn(ctx: ActionContext) -> FunctionResult:
        return FunctionResult(success=True)

    # Function not in YAML -> should fall back to decorator value
    from ontoagent.execution.functions import registry as reg_mod

    reg_mod._loaded_function_configs.clear()
    reg_mod._loaded_function_configs = {"__default__": {"danger_level": "read"}}

    try:
        assert get_danger_level("priority_test_fn") == "admin"
    finally:
        reg_mod._loaded_function_configs.clear()
        load_functions_from_yaml()
    _setup()


def test_get_danger_level_yaml_overrides_legacy():
    """For functions in functions.yaml, YAML value should be authoritative."""
    _setup()
    load_functions_from_yaml()
    # query_entity is "read" in both YAMLs; verify YAML lookup works
    assert get_danger_level("query_entity") == "read"
    # create_entity is "admin" in YAML
    assert get_danger_level("create_entity") == "admin"
    _setup()


def test_capability_strings_well_formed():
    """Every capability string must be 'EntityLabel:OP'."""
    _setup()
    valid_resources = {
        "CodeEntity",
        "ConceptEntity",
        "DocEntity",
        "ResourceEntity",
        "ModuleEntity",
        "ChangeSetEntity",
        "LogEntity",
        "AlertEntity",
        "ServiceEntity",
        "DataAsset",
        "ComplianceItem",
    }
    valid_ops = {"CREATE", "READ", "UPDATE", "DELETE", "EXECUTE", "EXPORT"}
    configs = load_functions_from_yaml()
    for name, cfg in configs.items():
        if name == "__default__":
            continue
        for cap in cfg["capabilities"]:
            assert ":" in cap, f"{name}: malformed capability '{cap}' (no colon)"
            resource, op = cap.split(":", 1)
            assert resource in valid_resources, f"{name}: unknown resource '{resource}' in '{cap}'"
            assert op in valid_ops, f"{name}: unknown operation '{op}' in '{cap}'"
    _setup()
