"""OntologyEngine 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from layerkg.ontology_engine import (
    ActionDef,
    ActionResolver,
    ActionResult,
    ApprovalManager,
    AuditLogger,
    FunctionDef,
    FunctionSelector,
    OntologyEngine,
)


# --- Fixtures ---


@pytest.fixture
def mock_graph_store() -> MagicMock:
    """创建 mock GraphStore。"""
    store = MagicMock()
    store.get_node.return_value = {
        "id": "test-entity-001",
        "name": "UserService.login",
        "labels": ["CodeEntity"],
        "entityType": "function",
        "lines": 150,
        "branches": 20,
    }
    store.query.return_value = []
    return store


@pytest.fixture
def yaml_path() -> Path:
    """返回 ontology_actions.yaml 的路径。"""
    return Path(__file__).parent.parent.parent / "src" / "layerkg" / "ontology_actions_legacy.yaml"


@pytest.fixture
def engine(mock_graph_store: MagicMock, yaml_path: Path) -> OntologyEngine:
    """创建已加载 YAML 的 OntologyEngine。"""
    eng = OntologyEngine(mock_graph_store)
    eng.load_from_yaml(yaml_path)
    return eng


# --- FunctionDef 测试 ---


class TestFunctionDef:
    def test_resolve_loads_callable(self) -> None:
        fn_def = FunctionDef(
            name="test_fn",
            description="test",
            implementation="layerkg.actions.code:extract_interface",
        )
        # extract_interface 存在，resolve 应该成功
        resolved = fn_def.resolve()
        assert callable(resolved)

    def test_callable_property_caches(self) -> None:
        fn_def = FunctionDef(
            name="test_fn",
            description="test",
            implementation="layerkg.actions.code:extract_interface",
        )
        c1 = fn_def.callable
        c2 = fn_def.callable
        assert c1 is c2


# --- load_from_yaml 测试 ---


class TestLoadFromYaml:
    def test_load_from_yaml_success(self, engine: OntologyEngine) -> None:
        """YAML 加载成功，code_entity 有 4 个 Action。"""
        actions = engine.get_actions("code_entity")
        assert len(actions) == 4
        action_names = {a.name for a in actions}
        assert "refactor" in action_names
        assert "document" in action_names
        assert "analyze_impact" in action_names

    def test_load_from_yaml_not_found(self, mock_graph_store: MagicMock) -> None:
        """文件不存在抛 FileNotFoundError。"""
        eng = OntologyEngine(mock_graph_store)
        with pytest.raises(FileNotFoundError):
            eng.load_from_yaml(Path("/nonexistent/path.yaml"))

    def test_load_from_yaml_empty_actions(self, mock_graph_store: MagicMock, tmp_path: Path) -> None:
        """空 YAML 不报错。"""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("{}")
        eng = OntologyEngine(mock_graph_store)
        eng.load_from_yaml(yaml_file)
        assert eng.get_actions("code_entity") == []


# --- get_actions 测试 ---


class TestGetActions:
    def test_get_actions_for_entity_type(self, engine: OntologyEngine) -> None:
        """获取 code_entity 的 Action 列表。"""
        actions = engine.get_actions("code_entity")
        assert len(actions) >= 1
        refactor = next(a for a in actions if a.name == "refactor")
        assert refactor.bind_to == "code_entity"
        assert not refactor.requires_approval

    def test_get_actions_unknown_type(self, engine: OntologyEngine) -> None:
        """未知实体类型返回空列表。"""
        actions = engine.get_actions("unknown_entity")
        assert actions == []


# --- get_functions 测试 ---


class TestGetFunctions:
    def test_get_functions_for_action(self, engine: OntologyEngine) -> None:
        """获取 refactor Action 下的 Function 列表。"""
        functions = engine.get_functions("code_entity", "refactor")
        assert len(functions) == 3
        fn_names = {f.name for f in functions}
        assert "split_large_function" in fn_names
        assert "extract_interface" in fn_names
        assert "reduce_complexity" in fn_names

    def test_get_functions_unknown_action(self, engine: OntologyEngine) -> None:
        """未知 Action 返回空列表。"""
        functions = engine.get_functions("code_entity", "nonexistent")
        assert functions == []


# --- ActionResolver 测试 ---


class TestActionResolver:
    def test_resolve_entity_type_from_labels(self, mock_graph_store: MagicMock) -> None:
        """从 Neo4j 标签解析实体类型。"""
        resolver = ActionResolver(mock_graph_store)
        entity_type = resolver.resolve_entity_type("test-entity-001")
        assert entity_type == "code_entity"

    def test_resolve_entity_type_from_entity_type_field(self, mock_graph_store: MagicMock) -> None:
        """从 entityType 字段降级解析。"""
        mock_graph_store.get_node.return_value = {
            "id": "test-002",
            "entityType": "ModuleEntity",
        }
        resolver = ActionResolver(mock_graph_store)
        entity_type = resolver.resolve_entity_type("test-002")
        assert entity_type == "moduleentity"

    def test_resolve_entity_type_not_found(self, mock_graph_store: MagicMock) -> None:
        """实体不存在抛 ValueError。"""
        mock_graph_store.get_node.return_value = None
        resolver = ActionResolver(mock_graph_store)
        with pytest.raises(ValueError, match="Entity not found"):
            resolver.resolve_entity_type("nonexistent")

    def test_resolve_action_success(self) -> None:
        """成功查找 Action。"""
        store = MagicMock()
        resolver = ActionResolver(store)
        action_def = ActionDef(name="refactor", description="test", bind_to="code_entity")
        registry = {"code_entity": {"refactor": action_def}}
        result = resolver.resolve_action("code_entity", "refactor", registry)
        assert result.name == "refactor"

    def test_resolve_action_not_found(self) -> None:
        """Action 未注册抛 ValueError。"""
        store = MagicMock()
        resolver = ActionResolver(store)
        registry: dict[str, dict[str, ActionDef]] = {}
        with pytest.raises(ValueError, match="not found"):
            resolver.resolve_action("code_entity", "refactor", registry)


# --- FunctionSelector 测试 ---


class TestFunctionSelector:
    def test_function_selector_rule_match_lines(self) -> None:
        """context.lines > 100 匹配 split_large_function。"""
        selector = FunctionSelector()
        functions = [
            FunctionDef(name="split_large_function", description="split", implementation="m:f"),
            FunctionDef(name="extract_interface", description="interface", implementation="m:f"),
        ]
        result = selector.select(functions, {"lines": 150, "branches": 20})
        assert result.name == "split_large_function"

    def test_function_selector_rule_match_reason(self) -> None:
        """context.reason 包含 'lines' 匹配 split_large_function。"""
        selector = FunctionSelector()
        functions = [
            FunctionDef(name="extract_interface", description="interface", implementation="m:f"),
            FunctionDef(name="split_large_function", description="split", implementation="m:f"),
        ]
        result = selector.select(functions, {"reason": "lines > 100"})
        assert result.name == "split_large_function"

    def test_function_selector_default_first(self) -> None:
        """无匹配规则时选第一个。"""
        selector = FunctionSelector()
        functions = [
            FunctionDef(name="extract_interface", description="interface", implementation="m:f"),
            FunctionDef(name="split_large_function", description="split", implementation="m:f"),
        ]
        result = selector.select(functions, {"some_key": "some_value"})
        assert result.name == "extract_interface"

    def test_function_selector_empty_list_raises(self) -> None:
        """空 Function 列表抛 ValueError。"""
        selector = FunctionSelector()
        with pytest.raises(ValueError, match="No functions"):
            selector.select([], {})

    def test_function_selector_by_function_name(self) -> None:
        """context 指定 function_name 直接匹配。"""
        selector = FunctionSelector()
        functions = [
            FunctionDef(name="extract_interface", description="interface", implementation="m:f"),
            FunctionDef(name="split_large_function", description="split", implementation="m:f"),
        ]
        result = selector.select(functions, {"function_name": "split_large_function"})
        assert result.name == "split_large_function"

    def test_function_selector_doc_type_api(self) -> None:
        """doc_type=api 选 generate_api_doc。"""
        selector = FunctionSelector()
        functions = [
            FunctionDef(name="generate_api_doc", description="api doc", implementation="m:f"),
            FunctionDef(name="annotate_complex_logic", description="annotate", implementation="m:f"),
        ]
        result = selector.select(functions, {"doc_type": "api"})
        assert result.name == "generate_api_doc"

    def test_function_selector_doc_type_comment(self) -> None:
        """doc_type=comment 选 annotate_complex_logic。"""
        selector = FunctionSelector()
        functions = [
            FunctionDef(name="generate_api_doc", description="api doc", implementation="m:f"),
            FunctionDef(name="annotate_complex_logic", description="annotate", implementation="m:f"),
        ]
        result = selector.select(functions, {"doc_type": "comment"})
        assert result.name == "annotate_complex_logic"

    def test_function_selector_trace_depth(self) -> None:
        """trace_depth 选 trace_call_chain。"""
        selector = FunctionSelector()
        functions = [
            FunctionDef(name="trace_call_chain", description="trace", implementation="m:f"),
            FunctionDef(name="find_dependent_modules", description="find", implementation="m:f"),
        ]
        result = selector.select(functions, {"trace_depth": 5})
        assert result.name == "trace_call_chain"

    def test_function_selector_method_list(self) -> None:
        """method_list 选 extract_interface。"""
        selector = FunctionSelector()
        functions = [
            FunctionDef(name="extract_interface", description="interface", implementation="m:f"),
            FunctionDef(name="split_large_function", description="split", implementation="m:f"),
        ]
        result = selector.select(functions, {"method_list": ["login", "logout"]})
        assert result.name == "extract_interface"


# --- AuditLogger 测试 ---


class TestAuditLogger:
    def test_audit_logger_records_entry(self) -> None:
        """审计日志记录执行条目。"""
        logger = AuditLogger()
        action_result = ActionResult(
            success=True,
            function_name="split_large_function",
            result={},
            side_effects=[],
            audit_id="",
        )
        audit_id = logger.log_execution(
            entity_id="test-001",
            action_name="refactor",
            function_name="split_large_function",
            context={"lines": 150},
            result=action_result,
        )
        assert audit_id.startswith("audit-")
        assert len(logger.logs) == 1
        assert logger.logs[0]["entity_id"] == "test-001"
        assert logger.logs[0]["action_name"] == "refactor"

    def test_audit_logger_multiple_entries(self) -> None:
        """多次执行生成多条日志。"""
        logger = AuditLogger()
        for i in range(3):
            result = ActionResult(
                success=True,
                function_name="test",
                result={},
                side_effects=[],
                audit_id="",
            )
            logger.log_execution(f"entity-{i}", "test", "test_fn", {}, result)
        assert len(logger.logs) == 3


# --- execute 测试 ---


class TestExecute:
    def test_execute_action_success(self, engine: OntologyEngine, mock_graph_store: MagicMock) -> None:
        """完整链路执行：resolve -> select -> execute -> audit。"""
        result = engine.execute(
            entity_id="test-entity-001",
            action_name="refactor",
            context={"lines": 150, "branches": 20},
        )
        assert result.success is True
        assert result.function_name == "split_large_function"
        assert result.audit_id.startswith("audit-")
        assert result.result["success"] is True
        assert result.result["entity_id"] == "test-entity-001"
        assert "analysis" in result.result

    def test_execute_action_not_found(self, engine: OntologyEngine, mock_graph_store: MagicMock) -> None:
        """未注册的 Action 抛 ValueError。"""
        with pytest.raises(ValueError, match="not found"):
            engine.execute(
                entity_id="test-entity-001",
                action_name="nonexistent_action",
                context={},
            )

    def test_execute_entity_not_found(self, engine: OntologyEngine, mock_graph_store: MagicMock) -> None:
        """实体不存在抛 ValueError。"""
        mock_graph_store.get_node.return_value = None
        with pytest.raises(ValueError, match="Entity not found"):
            engine.execute(
                entity_id="nonexistent",
                action_name="refactor",
                context={},
            )

    def test_execute_function_failure_recorded(self, engine: OntologyEngine, mock_graph_store: MagicMock) -> None:
        """Function 执行失败记录在 ActionResult 中。"""
        # context 不含 lines 且 node 也没有 -> lines=0 -> 触发 "不需要拆分"
        mock_graph_store.get_node.return_value = {
            "id": "test-002",
            "name": "small_func",
            "labels": ["CodeEntity"],
        }
        result = engine.execute(
            entity_id="test-002",
            action_name="refactor",
            context={"lines": 10, "branches": 2, "max_lines": 100},
        )
        assert result.success is False
        assert "error" in result.result
        assert result.audit_id.startswith("audit-")
