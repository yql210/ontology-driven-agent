"""Alert Action Function 单元测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from layerkg.actions.alert import (
    analyze_by_call_chain,
    analyze_by_log_pattern,
    create_ticket,
    find_last_stable,
)


# --- Fixtures ---


@pytest.fixture
def mock_graph_store() -> MagicMock:
    """创建 mock GraphStore，返回一个 AlertEntity 节点。"""
    store = MagicMock()
    store.get_node.return_value = {
        "id": "alert-001",
        "name": "ErrorSpike",
        "labels": ["AlertEntity"],
        "alert_type": "error_spike",
        "severity": "HIGH",
    }
    return store


# --- analyze_by_log_pattern 测试 ---


class TestAnalyzeByLogPattern:
    def test_analyze_by_log_pattern_basic(self, mock_graph_store: MagicMock) -> None:
        """基本分析：从关联日志中提取模式。"""
        mock_graph_store.query.return_value = [
            {"pattern": "TimeoutError", "message": "Connection timed out", "level": "ERROR"},
            {"pattern": "TimeoutError", "message": "Read timed out", "level": "ERROR"},
            {"pattern": "AuthError", "message": "Invalid token", "level": "WARN"},
        ]
        result = analyze_by_log_pattern(
            entity_id="alert-001",
            context={},
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["entity_id"] == "alert-001"
        assert result["side_effects"] == []
        assert len(result["patterns"]) == 2
        assert result["patterns"][0]["pattern"] == "TimeoutError"
        assert result["patterns"][0]["count"] == 2
        assert "TimeoutError" in result["root_cause_suggestion"]

    def test_analyze_by_log_pattern_entity_not_found(self, mock_graph_store: MagicMock) -> None:
        """实体不存在抛 ValueError。"""
        mock_graph_store.get_node.return_value = None
        with pytest.raises(ValueError, match="Entity not found"):
            analyze_by_log_pattern("nonexistent", {}, mock_graph_store)


# --- analyze_by_call_chain 测试 ---


class TestAnalyzeByCallChain:
    def test_analyze_by_call_chain_basic(self, mock_graph_store: MagicMock) -> None:
        """基本分析：追踪服务调用链。"""
        mock_graph_store.query.side_effect = [
            # 第一次 query: 查找关联服务
            [{"name": "UserService.login", "service": "user-service"}],
            # 第二次 query: 追踪调用链
            [{"callee_name": "validate_token"}, {"callee_name": "check_permission"}],
        ]
        result = analyze_by_call_chain(
            entity_id="alert-001",
            context={"depth": 3},
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["side_effects"] == []
        assert len(result["call_chain"]) == 1
        assert result["call_chain"][0]["code"] == "UserService.login"
        assert result["call_chain"][0]["downstream"] == ["validate_token", "check_permission"]

    def test_analyze_by_call_chain_empty(self, mock_graph_store: MagicMock) -> None:
        """无关联服务时返回空调用链。"""
        mock_graph_store.query.return_value = []
        result = analyze_by_call_chain(
            entity_id="alert-001",
            context={},
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["call_chain"] == []


# --- find_last_stable 测试 ---


class TestFindLastStable:
    def test_find_last_stable_basic(self, mock_graph_store: MagicMock) -> None:
        """返回最近的变更集，rollback_target 为倒数第二个。"""
        mock_graph_store.query.return_value = [
            {"hash": "abc123", "msg": "broke things", "at": "2024-01-02"},
            {"hash": "def456", "msg": "stable version", "at": "2024-01-01"},
        ]
        result = find_last_stable(
            entity_id="alert-001",
            context={},
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["side_effects"] == []
        assert len(result["recent_changes"]) == 2
        assert result["rollback_target"] is not None
        assert result["rollback_target"]["hash"] == "def456"

    def test_find_last_stable_no_changesets(self, mock_graph_store: MagicMock) -> None:
        """无关联变更集时返回空列表和 None rollback_target。"""
        mock_graph_store.query.return_value = []
        result = find_last_stable(
            entity_id="alert-001",
            context={},
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["recent_changes"] == []
        assert result["rollback_target"] is None


# --- create_ticket 测试 ---


class TestCreateTicket:
    def test_create_ticket_stub(self, mock_graph_store: MagicMock) -> None:
        """创建工单空壳返回成功。"""
        result = create_ticket(
            entity_id="alert-001",
            context={},
            graph_store=mock_graph_store,
        )
        assert result["success"] is True
        assert result["entity_id"] == "alert-001"
        assert "ticket_url" in result
        assert "ticket_created" in result["side_effects"]
        assert "stub" in result["message"].lower()
