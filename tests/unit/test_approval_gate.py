from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from ontoagent.domain.approval import (
    ApprovalContext,
    ApprovalDecision,
    DecisionLevel,
    PendingApproval,
    PolicyResult,
    generate_token,
)
from ontoagent.execution.constraints.approval_gate import ApprovalGate
from ontoagent.execution.constraints.guard_pipeline import ActionGuardPipeline
from ontoagent.execution.constraints.policies import (
    ActionApprovalPolicy,
    FunctionDangerPolicy,
    GuardResultPolicy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx() -> ApprovalContext:
    return ApprovalContext(intent_type="refactor", target="test_func", session_id="session123")


@pytest.fixture
def gate() -> ApprovalGate:
    return ApprovalGate()


# ---------------------------------------------------------------------------
# Test 1: 空策略链 → APPROVED
# ---------------------------------------------------------------------------


def test_empty_policy_chain_returns_approved(gate: ApprovalGate, ctx: ApprovalContext) -> None:
    decision = gate.check(ctx)
    assert decision.level == DecisionLevel.APPROVED
    assert decision.token == ""
    assert len(gate.audit_log) == 1
    assert gate.audit_log[0]["action"] == "approved"


# ---------------------------------------------------------------------------
# Test 2: DENIED 优先 → 短路返回
# ---------------------------------------------------------------------------


def test_denied_policy_short_circuits(gate: ApprovalGate, ctx: ApprovalContext) -> None:
    class DenyPolicy:
        @property
        def name(self) -> str:
            return "DenyPolicy"

        def evaluate(self, context, **kwargs):
            return PolicyResult(policy_name=self.name, level=DecisionLevel.DENIED, reason="always deny")

    class AllowPolicy:
        @property
        def name(self) -> str:
            return "AllowPolicy"

        def evaluate(self, context, **kwargs):
            return PolicyResult(policy_name=self.name, level=DecisionLevel.APPROVED, reason="always allow")

    gate.add_policy(DenyPolicy())
    gate.add_policy(AllowPolicy())  # This should never be reached due to short-circuit

    decision = gate.check(ctx)
    assert decision.level == DecisionLevel.DENIED
    assert decision.token == ""
    # Only the first (denied) policy should be in results due to short-circuit
    assert len(decision.results) == 1
    assert decision.results[0].policy_name == "DenyPolicy"


# ---------------------------------------------------------------------------
# Test 3: PENDING → 生成 token
# ---------------------------------------------------------------------------


def test_pending_generates_token(gate: ApprovalGate, ctx: ApprovalContext) -> None:
    class PendingPolicy:
        @property
        def name(self) -> str:
            return "PendingPolicy"

        def evaluate(self, context, **kwargs):
            return PolicyResult(policy_name=self.name, level=DecisionLevel.PENDING, reason="needs review")

    gate.add_policy(PendingPolicy())
    decision = gate.check(ctx)

    assert decision.level == DecisionLevel.PENDING
    assert decision.token != ""
    assert len(decision.token) == 12
    assert gate.pending_count() == 1


# ---------------------------------------------------------------------------
# Test 4: 有效 token → resolve 成功
# ---------------------------------------------------------------------------


def test_valid_token_resolve_success(gate: ApprovalGate, ctx: ApprovalContext) -> None:
    class PendingPolicy:
        @property
        def name(self) -> str:
            return "PendingPolicy"

        def evaluate(self, context, **kwargs):
            return PolicyResult(policy_name=self.name, level=DecisionLevel.PENDING, reason="needs review")

    gate.add_policy(PendingPolicy())
    decision = gate.check(ctx)

    assert decision.level == DecisionLevel.PENDING
    result = gate.resolve(decision.token, approved=True)
    assert result is not None
    assert result.intent_type == "refactor"
    assert result.target == "test_func"
    assert gate.pending_count() == 0


# ---------------------------------------------------------------------------
# Test 5: 过期 token → resolve 返回 None
# ---------------------------------------------------------------------------


def test_expired_token_resolve_returns_none(gate: ApprovalGate, ctx: ApprovalContext) -> None:
    token = generate_token("test", "target", "s1")
    # Manually insert an already-expired PendingApproval
    expired = PendingApproval(
        token=token,
        context=ctx,
        created_at=time.time() - 1000,  # Far in the past
        ttl=1,
    )
    gate._pending[token] = expired

    result = gate.resolve(token, approved=True)
    assert result is None
    assert gate.pending_count() == 0  # expired token is cleaned up


# ---------------------------------------------------------------------------
# Test 6: 拒绝 token → resolve 返回 None
# ---------------------------------------------------------------------------


def test_rejected_token_resolve_returns_none(gate: ApprovalGate, ctx: ApprovalContext) -> None:
    class PendingPolicy:
        @property
        def name(self) -> str:
            return "PendingPolicy"

        def evaluate(self, context, **kwargs):
            return PolicyResult(policy_name=self.name, level=DecisionLevel.PENDING, reason="needs review")

    gate.add_policy(PendingPolicy())
    decision = gate.check(ctx)

    result = gate.resolve(decision.token, approved=False)
    assert result is None
    assert gate.pending_count() == 0


# ---------------------------------------------------------------------------
# Test 7: 无效 token → resolve 返回 None
# ---------------------------------------------------------------------------


def test_invalid_token_resolve_returns_none(gate: ApprovalGate) -> None:
    result = gate.resolve("nonexistent", approved=True)
    assert result is None


# ---------------------------------------------------------------------------
# Test 8: 一次性 token → 第二次 resolve 返回 None
# ---------------------------------------------------------------------------


def test_token_one_time_use(gate: ApprovalGate, ctx: ApprovalContext) -> None:
    class PendingPolicy:
        @property
        def name(self) -> str:
            return "PendingPolicy"

        def evaluate(self, context, **kwargs):
            return PolicyResult(policy_name=self.name, level=DecisionLevel.PENDING, reason="needs review")

    gate.add_policy(PendingPolicy())
    decision = gate.check(ctx)

    # First resolve succeeds
    result1 = gate.resolve(decision.token, approved=True)
    assert result1 is not None

    # Second resolve returns None (already consumed)
    result2 = gate.resolve(decision.token, approved=True)
    assert result2 is None


# ---------------------------------------------------------------------------
# Test 9: GuardResultPolicy BLOCK → PENDING (on_block=require_approval)
# ---------------------------------------------------------------------------


def test_guard_result_policy_block_pending(ctx: ApprovalContext) -> None:
    pipeline = ActionGuardPipeline([])
    # Mock pipeline.check to return a block reason
    block_reason = "BLOCK: entity does not exist"
    pipeline.check = MagicMock(return_value=(block_reason, []))

    policy = GuardResultPolicy(pipeline, on_block="require_approval")
    config = MagicMock()
    graph_store = MagicMock()

    result = policy.evaluate(ctx, config=config, graph_store=graph_store)
    assert result.level == DecisionLevel.PENDING
    assert result.reason == block_reason
    assert result.details["guard_block"] == block_reason


# ---------------------------------------------------------------------------
# Test 10: GuardResultPolicy BLOCK → DENIED (on_block=auto_reject)
# ---------------------------------------------------------------------------


def test_guard_result_policy_block_denied(ctx: ApprovalContext) -> None:
    pipeline = ActionGuardPipeline([])
    block_reason = "BLOCK: entity does not exist"
    pipeline.check = MagicMock(return_value=(block_reason, []))

    policy = GuardResultPolicy(pipeline, on_block="auto_reject")
    config = MagicMock()
    graph_store = MagicMock()

    result = policy.evaluate(ctx, config=config, graph_store=graph_store)
    assert result.level == DecisionLevel.DENIED
    assert result.reason == block_reason


# ---------------------------------------------------------------------------
# Test 11: GuardResultPolicy WARN → PENDING (on_warn=require_approval)
# ---------------------------------------------------------------------------


def test_guard_result_policy_warn_pending(ctx: ApprovalContext) -> None:
    pipeline = ActionGuardPipeline([])
    # No block, but some warnings
    pipeline.check = MagicMock(return_value=(None, ["warning: low confidence"]))

    policy = GuardResultPolicy(pipeline, on_warn="require_approval")
    config = MagicMock()
    graph_store = MagicMock()

    result = policy.evaluate(ctx, config=config, graph_store=graph_store)
    assert result.level == DecisionLevel.PENDING
    assert "WARN" in result.reason
    assert result.details["warnings"] == ["warning: low confidence"]


# ---------------------------------------------------------------------------
# Test 12: ActionApprovalPolicy requires_approval=true → PENDING
# ---------------------------------------------------------------------------


def test_action_approval_policy_pending(ctx: ApprovalContext) -> None:
    policy = ActionApprovalPolicy()
    config = MagicMock()
    config.requires_approval = True

    result = policy.evaluate(ctx, config=config)
    assert result.level == DecisionLevel.PENDING
    assert "refactor" in result.reason


# ---------------------------------------------------------------------------
# Test 13: FunctionDangerPolicy write → PENDING
# ---------------------------------------------------------------------------


def test_function_danger_policy_write_pending(ctx: ApprovalContext) -> None:
    meta = {"delete_user": {"danger_level": "write", "description": "Deletes a user"}}
    policy = FunctionDangerPolicy(function_meta=meta)

    result = policy.evaluate(ctx, function_name="delete_user")
    assert result.level == DecisionLevel.PENDING
    assert "delete_user" in result.reason
    assert result.details["danger_level"] == "write"


# ---------------------------------------------------------------------------
# Test 14: FunctionDangerPolicy read → APPROVED
# ---------------------------------------------------------------------------


def test_function_danger_policy_read_approved(ctx: ApprovalContext) -> None:
    meta = {"list_users": {"danger_level": "read", "description": "Lists users"}}
    policy = FunctionDangerPolicy(function_meta=meta)

    result = policy.evaluate(ctx, function_name="list_users")
    assert result.level == DecisionLevel.APPROVED


# ---------------------------------------------------------------------------
# Test 15: cleanup_expired 清理过期令牌
# ---------------------------------------------------------------------------


def test_cleanup_expired_removes_stale_tokens(gate: ApprovalGate, ctx: ApprovalContext) -> None:
    # Insert multiple expired tokens
    for i in range(3):
        token = generate_token("test", f"target{i}", "s1")
        gate._pending[token] = PendingApproval(
            token=token,
            context=ctx,
            created_at=time.time() - 10000,
            ttl=1,
        )

    # Insert one valid token
    valid_token = generate_token("test", "valid_target", "s1")
    gate._pending[valid_token] = PendingApproval(token=valid_token, context=ctx)

    assert gate.pending_count() == 4
    cleaned = gate.cleanup_expired()
    assert cleaned == 3
    assert gate.pending_count() == 1
    assert valid_token in gate._pending


# ---------------------------------------------------------------------------
# Test 16: audit_log 记录审批决策
# ---------------------------------------------------------------------------


def test_audit_log_records_decisions(gate: ApprovalGate, ctx: ApprovalContext) -> None:
    class PendingPolicy:
        @property
        def name(self) -> str:
            return "PendingPolicy"

        def evaluate(self, context, **kwargs):
            return PolicyResult(policy_name=self.name, level=DecisionLevel.PENDING, reason="needs review")

    gate.add_policy(PendingPolicy())
    decision = gate.check(ctx)

    # Audit log should have one "pending" entry
    assert len(gate.audit_log) == 1
    pending_entry = gate.audit_log[0]
    assert pending_entry["action"] == "pending"
    assert pending_entry["intent_type"] == "refactor"
    assert pending_entry["target"] == "test_func"
    assert pending_entry["token"] == decision.token
    assert pending_entry["results"][0]["policy"] == "PendingPolicy"
    assert pending_entry["results"][0]["level"] == "pending"

    # Resolve: should add "resolved" entry
    gate.resolve(decision.token, approved=True)
    assert len(gate.audit_log) == 2
    resolved_entry = gate.audit_log[1]
    assert resolved_entry["action"] == "resolved"
    assert resolved_entry["approved"] is True


# ---------------------------------------------------------------------------
# Test 17: GuardResultPolicy no config → APPROVED
# ---------------------------------------------------------------------------


def test_guard_result_policy_no_config_approved(ctx: ApprovalContext) -> None:
    pipeline = ActionGuardPipeline([])
    policy = GuardResultPolicy(pipeline)
    result = policy.evaluate(ctx)
    assert result.level == DecisionLevel.APPROVED


# ---------------------------------------------------------------------------
# Test 18: ActionApprovalPolicy no config → APPROVED
# ---------------------------------------------------------------------------


def test_action_approval_policy_no_config_approved(ctx: ApprovalContext) -> None:
    policy = ActionApprovalPolicy()
    result = policy.evaluate(ctx)
    assert result.level == DecisionLevel.APPROVED


# ---------------------------------------------------------------------------
# Test 19: FunctionDangerPolicy no function name → APPROVED
# ---------------------------------------------------------------------------


def test_function_danger_policy_no_function_name_approved(ctx: ApprovalContext) -> None:
    policy = FunctionDangerPolicy()
    result = policy.evaluate(ctx)
    assert result.level == DecisionLevel.APPROVED


# ---------------------------------------------------------------------------
# Test 20: FunctionDangerPolicy admin → PENDING
# ---------------------------------------------------------------------------


def test_function_danger_policy_admin_pending(ctx: ApprovalContext) -> None:
    meta = {"drop_table": {"danger_level": "admin", "description": "Drops a table"}}
    policy = FunctionDangerPolicy(function_meta=meta)

    result = policy.evaluate(ctx, function_name="drop_table")
    assert result.level == DecisionLevel.PENDING


# ---------------------------------------------------------------------------
# Domain model tests
# ---------------------------------------------------------------------------


def test_pending_approval_is_expired() -> None:
    ctx = ApprovalContext(intent_type="test", target="foo")
    pa = PendingApproval(token="token123", context=ctx, ttl=1)
    # Should not be expired yet
    assert not pa.is_expired


def test_pending_approval_creates_expires_at() -> None:
    ctx = ApprovalContext(intent_type="test", target="foo")
    pa = PendingApproval(token="token123", context=ctx, ttl=300)
    assert pa.expires_at == pa.created_at + 300
    assert pa.resolved is False


def test_generate_token_uniqueness() -> None:
    token1 = generate_token("intent_a", "target_a", "s1")
    token2 = generate_token("intent_a", "target_a", "s1")
    # Tokens should be different (unique UUID per call)
    assert token1 != token2
    # Token length should be 12 (hex digest[:12])
    assert len(token1) == 12


def test_approval_decision_defaults() -> None:
    decision = ApprovalDecision(level=DecisionLevel.APPROVED)
    assert decision.token == ""
    assert decision.results == []
    assert decision.context is None
