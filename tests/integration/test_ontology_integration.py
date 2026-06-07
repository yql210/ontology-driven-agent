"""OntologyEngine 集成测试 — 验证完整生命周期。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from layerkg.ontology_engine import OntologyEngine


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
        "params": ["username", "password"],
        "return_type": "Token",
        "docstring": "用户登录",
    }
    store.query.return_value = [
        {"id": "func-a", "name": "validate", "entity_type": "function"},
    ]
    return store


@pytest.fixture
def yaml_path() -> Path:
    """返回 ontology_actions.yaml 路径。"""
    return Path(__file__).parent.parent.parent / "src" / "layerkg" / "ontology_actions.yaml"


@pytest.fixture
def engine(mock_graph_store: MagicMock, yaml_path: Path) -> OntologyEngine:
    """创建并加载 YAML 的 OntologyEngine。"""
    eng = OntologyEngine(mock_graph_store)
    eng.load_from_yaml(yaml_path)
    return eng


# --- 集成测试 ---


class TestFullLifecycle:
    def test_full_lifecycle(self, engine: OntologyEngine) -> None:
        """完整生命周期：load_yaml -> get_actions -> execute -> audit_log 有记录。"""
        # 验证 YAML 加载成功
        actions = engine.get_actions("code_entity")
        assert len(actions) >= 3

        # 执行 refactor action
        result = engine.execute(
            entity_id="test-entity-001",
            action_name="refactor",
            context={"lines": 150, "branches": 20},
        )
        assert result.success is True
        assert result.function_name == "split_large_function"
        assert result.audit_id.startswith("audit-")

        # 审计日志有记录
        logs = engine.audit_logger.logs
        assert len(logs) >= 1
        assert logs[0]["entity_id"] == "test-entity-001"
        assert logs[0]["action_name"] == "refactor"
        assert logs[0]["success"] is True

    def test_document_action_lifecycle(self, engine: OntologyEngine) -> None:
        """document action 全流程。"""
        result = engine.execute(
            entity_id="test-entity-001",
            action_name="document",
            context={"doc_type": "api"},
        )
        assert result.success is True
        assert result.function_name == "generate_api_doc"
        assert "doc_markdown" in result.result["analysis"]

    def test_analyze_impact_lifecycle(self, engine: OntologyEngine) -> None:
        """analyze_impact action 全流程。"""
        result = engine.execute(
            entity_id="test-entity-001",
            action_name="analyze_impact",
            context={"trace_depth": 3},
        )
        assert result.success is True
        assert result.function_name == "trace_call_chain"
        assert "call_tree" in result.result["analysis"]


class TestApprovalLifecycle:
    def test_approval_lifecycle(self, engine: OntologyEngine) -> None:
        """审批生命周期：execute high-risk -> pending -> approve -> execute_approved。"""
        # Step 1: 执行高风险 action，返回 pending
        result = engine.execute(
            entity_id="test-entity-001",
            action_name="delete",
            context={},
        )
        assert result.success is False
        assert "pending_approval" in result.side_effects
        approval_id = result.result["approval_id"]

        # Step 2: 验证 pending 状态
        assert engine.approval_manager.check_approval(approval_id) == "pending"
        pending = engine.approval_manager.list_pending()
        assert len(pending) == 1

        # Step 3: 审批通过
        engine.approval_manager.approve(approval_id, "admin")
        assert engine.approval_manager.check_approval(approval_id) == "approved"
        assert engine.approval_manager.list_pending() == []

        # Step 4: 执行已审批的 Function
        approved_result = engine.execute_approved(approval_id)
        # find_dependent_modules 仍是空壳，会 NotImplementedError
        assert approved_result.success is False
        assert "error" in approved_result.result

        # Step 5: 审计日志有两条记录（pending + approved execution）
        logs = engine.audit_logger.logs
        assert len(logs) == 2
        assert logs[0]["success"] is False  # pending
        assert logs[1]["success"] is False  # NotImplementedError
