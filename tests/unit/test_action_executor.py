"""Tests for ActionExecutor — intent routing, criteria checking, function chain execution."""

from __future__ import annotations

import textwrap
from typing import Any

import pytest

from layerkg.execution.action_executor import ActionExecutor
from layerkg.execution.action_types import FunctionResult
from layerkg.execution.functions.registry import clear_registry, register_function


class MockGraphStore:
    """Mock graph store with configurable entities."""

    def __init__(self, entities: dict[str, dict[str, Any]]) -> None:
        self._entities = entities

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        return self._entities.get(node_id)

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = params or {}
        name = params.get("name", "")
        entity_id = params.get("entity_id", "")

        # Exact name match
        if name:
            for eid, ent in self._entities.items():
                if ent.get("name") == name:
                    return [
                        {
                            "id": eid,
                            "name": ent["name"],
                            "lines": ent.get("lines", 0),
                            "branches": ent.get("branches", 0),
                            "entityType": ent.get("entityType", ""),
                            "labels": ent.get("labels", []),
                        }
                    ]
            # Fuzzy match
            for eid, ent in self._entities.items():
                if name in ent.get("name", ""):
                    return [
                        {
                            "id": eid,
                            "name": ent["name"],
                            "lines": ent.get("lines", 0),
                            "branches": ent.get("branches", 0),
                            "entityType": ent.get("entityType", ""),
                            "labels": ent.get("labels", []),
                        }
                    ]
            return []

        # Entity ID match (for trace_call_chain)
        if entity_id:
            node = self._entities.get(entity_id)
            if node:
                return [
                    {
                        "id": entity_id,
                        "name": node.get("name", ""),
                        "entity_type": node.get("entityType", ""),
                    }
                ]
            return []

        return []


ENTITIES = {
    "ent-001": {
        "name": "Cache",
        "lines": 320,
        "branches": 15,
        "entityType": "function",
        "labels": ["CodeEntity"],
    },
    "ent-002": {
        "name": "Small",
        "lines": 50,
        "branches": 3,
        "entityType": "function",
        "labels": ["CodeEntity"],
    },
}


def _make_yaml(tmp_path, content: str | None = None):
    """Create a temporary YAML config file."""
    if content is None:
        content = textwrap.dedent("""\
            actions:
              refactor:
                intent_type: refactor
                trigger_hint: "重构"
                bind_to: code_entity
                submission_criteria:
                  - "entity exists"
                  - "entity.lines > 100"
                functions:
                  - check_refactor_eligibility
                requires_approval: false

              document:
                intent_type: document
                trigger_hint: "写文档"
                bind_to: code_entity
                submission_criteria:
                  - "entity exists"
                functions:
                  - generate_api_doc
                requires_approval: false

              multi_fn:
                intent_type: multi_fn
                trigger_hint: "多步骤"
                bind_to: code_entity
                submission_criteria:
                  - "entity exists"
                functions:
                  - _test_fn_a
                  - _test_fn_b
                requires_approval: false

              fail_chain:
                intent_type: fail_chain
                trigger_hint: "会失败"
                bind_to: code_entity
                submission_criteria:
                  - "entity exists"
                functions:
                  - _test_fn_ok
                  - _test_fn_fail
                requires_approval: false
        """)
    yaml_file = tmp_path / "actions.yaml"
    yaml_file.write_text(content, encoding="utf-8")
    return yaml_file


@pytest.fixture(autouse=True)
def _register_test_functions():
    """Register test-only functions for each test, clean up after."""
    clear_registry()
    # Re-register builtins explicitly (module-level register_all guards against re-import)
    from layerkg.execution.functions.builtin import register_all

    register_all()

    # Test helper functions
    @register_function("_test_fn_a")
    def _fn_a(ctx) -> FunctionResult:
        return FunctionResult(success=True, data={"step": "a"})

    @register_function("_test_fn_b")
    def _fn_b(ctx) -> FunctionResult:
        return FunctionResult(success=True, data={"step": "b"})

    @register_function("_test_fn_ok")
    def _fn_ok(ctx) -> FunctionResult:
        return FunctionResult(success=True, data={"step": "ok"})

    @register_function("_test_fn_fail")
    def _fn_fail(ctx) -> FunctionResult:
        return FunctionResult(success=False, error="intentional failure")

    yield
    clear_registry()


class TestActionExecutorRefactor:
    def test_execute_refactor_success(self, tmp_path):
        """Entity with lines=320 passes criteria > 100, function executes."""
        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path))

        result = executor.execute("refactor", {"target": "Cache"})

        assert result.success is True
        assert result.action_name == "refactor"
        assert len(result.results) == 1
        assert result.results[0].success is True

    def test_execute_refactor_criteria_fail(self, tmp_path):
        """Entity with lines=50 does NOT satisfy entity.lines > 100."""
        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path))

        result = executor.execute("refactor", {"target": "Small"})

        assert result.success is False
        assert "不满足条件" in result.error or "<=" in result.error


class TestActionExecutorRouting:
    def test_execute_unknown_intent(self, tmp_path):
        """Unknown intent_type returns error."""
        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path))

        result = executor.execute("nonexistent", {"target": "Cache"})

        assert result.success is False
        assert "未知操作类型" in result.error

    def test_execute_entity_not_found(self, tmp_path):
        """Target entity not found in graph returns error."""
        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path))

        result = executor.execute("refactor", {"target": "DoesNotExist"})

        assert result.success is False
        assert "未找到实体" in result.error


class TestActionExecutorFunctionChain:
    def test_execute_function_chain_success(self, tmp_path):
        """Multi-function chain executes all functions in order."""
        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path))

        result = executor.execute("multi_fn", {"target": "Cache"})

        assert result.success is True
        assert len(result.results) == 2
        assert result.results[0].data["step"] == "a"
        assert result.results[1].data["step"] == "b"

    def test_execute_function_chain_stops_on_failure(self, tmp_path):
        """Function chain stops when a function fails."""
        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path))

        result = executor.execute("fail_chain", {"target": "Cache"})

        assert result.success is False
        assert len(result.results) == 2
        assert result.results[0].success is True
        assert result.results[1].success is False
        assert "intentional failure" in result.error


class TestActionExecutorDocument:
    def test_execute_document_success(self, tmp_path):
        """Document action with entity found executes generate_api_doc."""
        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path))

        result = executor.execute("document", {"target": "Cache"})

        assert result.success is True
        assert result.action_name == "document"
        assert len(result.results) == 1


class TestActionExecutorFunctionRunner:
    """Tests for optional FunctionRunner injection into ActionExecutor."""

    def test_execute_with_function_runner(self, tmp_path):
        """When FunctionRunner is injected, it is used instead of ctx.call_function."""
        from unittest.mock import MagicMock

        from layerkg.execution.action_types import FunctionResult
        from layerkg.execution.function_runner import FunctionRunner

        runner = MagicMock(spec=FunctionRunner)
        runner.run.return_value = FunctionResult(success=True, data={"via": "runner"})

        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path), function_runner=runner)

        result = executor.execute("refactor", {"target": "Cache"})

        assert result.success is True
        runner.run.assert_called_once()
        assert runner.run.call_args[0][0] == "check_refactor_eligibility"
        assert result.results[0].data == {"via": "runner"}

    def test_execute_without_function_runner_fallback(self, tmp_path):
        """Without FunctionRunner, the legacy ctx.call_function path is used."""
        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path))

        result = executor.execute("multi_fn", {"target": "Cache"})

        assert result.success is True
        assert len(result.results) == 2
        assert result.results[0].data["step"] == "a"
        assert result.results[1].data["step"] == "b"

    def test_execute_with_function_runner_failure(self, tmp_path):
        """When FunctionRunner returns failure, action result reflects it."""
        from unittest.mock import MagicMock

        from layerkg.execution.action_types import FunctionResult
        from layerkg.execution.function_runner import FunctionRunner

        runner = MagicMock(spec=FunctionRunner)
        runner.run.return_value = FunctionResult(success=False, error="runner failed")

        store = MockGraphStore(ENTITIES)
        executor = ActionExecutor(store, yaml_path=_make_yaml(tmp_path), function_runner=runner)

        result = executor.execute("refactor", {"target": "Cache"})

        assert result.success is False
        assert "runner failed" in result.error
