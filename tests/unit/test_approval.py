"""ApprovalManager 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from layerkg.ontology_engine import (
    ActionDef,
    ActionResult,
    ApprovalManager,
    ApprovalRequest,
    FunctionDef,
    OntologyEngine,
)


# --- Fixtures ---


@pytest.fixture
def approval_manager() -> ApprovalManager:
    """创建 ApprovalManager 实例。"""
    return ApprovalManager()


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
    return Path(__file__).parent.parent.parent / "src" / "layerkg" / "ontology_actions.yaml"


@pytest.fixture
def engine(mock_graph_store: MagicMock, yaml_path: Path) -> OntologyEngine:
    """创建已加载 YAML 的 OntologyEngine。"""
    eng = OntologyEngine(mock_graph_store)
    eng.load_from_yaml(yaml_path)
    return eng


@pytest.fixture
def sample_action() -> ActionDef:
    """创建测试用的 ActionDef。"""
    return ActionDef(
        name="delete",
        description="删除实体",
        bind_to="code_entity",
        requires_approval=True,
        functions=[
            FunctionDef(name="find_dependent_modules", description="查找依赖", implementation="layerkg.actions.code:find_dependent_modules"),
        ],
    )


# --- ApprovalManager 测试 ---


class TestApprovalManager:
    def test_request_approval(self, approval_manager: ApprovalManager, sample_action: ActionDef) -> None:
        """发起审批请求返回 approval_id。"""
        approval_id = approval_manager.request_approval(
            action=sample_action,
            entity_id="entity-001",
            function_name="find_dependent_modules",
            context={"reason": "cleanup"},
        )
        assert approval_id.startswith("approval-")
        status = approval_manager.check_approval(approval_id)
        assert status == "pending"

    def test_approve_request(self, approval_manager: ApprovalManager, sample_action: ActionDef) -> None:
        """审批通过。"""
        approval_id = approval_manager.request_approval(
            action=sample_action,
            entity_id="entity-001",
            function_name="find_dependent_modules",
            context={},
        )
        approval_manager.approve(approval_id, "admin")
        assert approval_manager.check_approval(approval_id) == "approved"

        req = approval_manager.get_request(approval_id)
        assert req is not None
        assert req.approver == "admin"
        assert req.resolved_at is not None

    def test_reject_request(self, approval_manager: ApprovalManager, sample_action: ActionDef) -> None:
        """审批拒绝。"""
        approval_id = approval_manager.request_approval(
            action=sample_action,
            entity_id="entity-001",
            function_name="find_dependent_modules",
            context={},
        )
        approval_manager.reject(approval_id, "admin", "too risky")
        assert approval_manager.check_approval(approval_id) == "rejected"

        req = approval_manager.get_request(approval_id)
        assert req is not None
        assert req.approver == "admin"

    def test_list_pending(self, approval_manager: ApprovalManager, sample_action: ActionDef) -> None:
        """列出待审批请求。"""
        id1 = approval_manager.request_approval(sample_action, "e1", "fn1", {})
        id2 = approval_manager.request_approval(sample_action, "e2", "fn2", {})
        approval_manager.approve(id1, "admin")

        pending = approval_manager.list_pending()
        assert len(pending) == 1
        assert pending[0].id == id2

    def test_approve_nonexistent_raises(self, approval_manager: ApprovalManager) -> None:
        """审批不存在的请求抛 ValueError。"""
        with pytest.raises(ValueError, match="not found"):
            approval_manager.approve("approval-nonexistent", "admin")

    def test_approve_already_resolved_raises(self, approval_manager: ApprovalManager, sample_action: ActionDef) -> None:
        """审批已处理的请求抛 ValueError。"""
        approval_id = approval_manager.request_approval(sample_action, "e1", "fn1", {})
        approval_manager.approve(approval_id, "admin")
        with pytest.raises(ValueError, match="Cannot approve"):
            approval_manager.approve(approval_id, "admin2")

    def test_reject_already_resolved_raises(self, approval_manager: ApprovalManager, sample_action: ActionDef) -> None:
        """拒绝已处理的请求抛 ValueError。"""
        approval_id = approval_manager.request_approval(sample_action, "e1", "fn1", {})
        approval_manager.reject(approval_id, "admin")
        with pytest.raises(ValueError, match="Cannot reject"):
            approval_manager.reject(approval_id, "admin2")

    def test_check_approval_not_found(self, approval_manager: ApprovalManager) -> None:
        """查询不存在的审批请求抛 ValueError。"""
        with pytest.raises(ValueError, match="not found"):
            approval_manager.check_approval("approval-nonexistent")


# --- Engine 审批集成测试 ---


class TestExecuteWithApproval:
    def test_execute_with_approval_required(self, engine: OntologyEngine) -> None:
        """requires_approval=True 时返回 pending。"""
        result = engine.execute(
            entity_id="test-entity-001",
            action_name="delete",
            context={},
        )
        assert result.success is False
        assert "pending_approval" in result.side_effects
        assert result.result["status"] == "pending"
        assert result.result["approval_id"].startswith("approval-")

    def test_execute_approved_after_approval(self, engine: OntologyEngine, mock_graph_store: MagicMock) -> None:
        """审批通过后 execute_approved 能继续执行。"""
        # 先触发挂起
        result = engine.execute(
            entity_id="test-entity-001",
            action_name="delete",
            context={},
        )
        approval_id = result.result["approval_id"]

        # 审批通过
        engine.approval_manager.approve(approval_id, "admin")

        # 执行已审批的 Function
        # find_dependent_modules 仍是空壳，会抛 NotImplementedError
        # 但 engine 应该捕获异常并返回 success=False
        approved_result = engine.execute_approved(approval_id)
        assert approved_result.success is False
        assert "error" in approved_result.result
        assert approved_result.audit_id.startswith("audit-")

    def test_execute_approved_not_approved_raises(self, engine: OntologyEngine) -> None:
        """未审批直接执行抛 ValueError。"""
        result = engine.execute(
            entity_id="test-entity-001",
            action_name="delete",
            context={},
        )
        approval_id = result.result["approval_id"]
        # 没有调用 approve
        with pytest.raises(ValueError, match="expected 'approved'"):
            engine.execute_approved(approval_id)

    def test_execute_no_approval_runs_directly(self, engine: OntologyEngine) -> None:
        """requires_approval=False 时直接执行。"""
        result = engine.execute(
            entity_id="test-entity-001",
            action_name="refactor",
            context={"lines": 150, "branches": 20},
        )
        assert result.success is True
        assert "pending_approval" not in result.side_effects
