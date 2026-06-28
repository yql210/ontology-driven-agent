from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class DecisionLevel(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    DENIED = "denied"


@dataclass
class PolicyResult:
    """单个策略的评估结果"""

    policy_name: str
    level: DecisionLevel
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalDecision:
    """审批门的总决策"""

    level: DecisionLevel
    token: str = ""
    results: list[PolicyResult] = field(default_factory=list)
    context: ApprovalContext | None = None


@dataclass
class ApprovalContext:
    """审批上下文 — ApprovalGate 的输入"""

    intent_type: str
    target: str
    params: dict[str, Any] = field(default_factory=dict)
    entity: dict[str, Any] = field(default_factory=dict)
    guard_checks: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""


@dataclass
class PendingApproval:
    """待审批记录"""

    token: str
    context: ApprovalContext
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    ttl: int = 600  # 秒
    resolved: bool = False

    def __post_init__(self) -> None:
        if self.expires_at == 0.0:
            self.expires_at = self.created_at + self.ttl

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


def generate_token(intent_type: str, target: str, session_id: str = "") -> str:
    """生成唯一审批令牌，绑定 intent_type + target + session_id"""
    raw = f"{intent_type}:{target}:{session_id}:{uuid.uuid4()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]
