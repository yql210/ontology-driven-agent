"""FastAPI 中间件 + 权限校验辅助函数。

设计要点：
- ``RepoAuthMiddleware`` 始终注入 ``request.state.user_id``（即便 ACL 关闭），
  方便路由统一从 ``request.state`` 取用户。
- 中间件**不**在 dispatch 中读取请求体（Starlette 限制：body 一旦被消费无法回放），
  具体的 ``repo_id`` 权限校验交给路由层通过 ``require_access`` 显式触发。
- ``ONTOAGENT_ACL_ENABLED != "true"`` 时，``require_access`` 立即返回，整体放行。
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from ontoagent.auth.acl import RepoAccessControl

if TYPE_CHECKING:
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

#: 开启拦截的 env 开关。默认空串 → 不拦截。
ACL_ENV_FLAG = "ONTOAGENT_ACL_ENABLED"

#: 健康检查、指标端点：不受 ACL 影响。
_EXEMPT_PATHS = {"/health", "/metrics"}


def is_acl_enabled() -> bool:
    """``ONTOAGENT_ACL_ENABLED=true`` 时返回 True。"""
    return os.getenv(ACL_ENV_FLAG, "").lower() == "true"


def get_user_id(request: Request) -> str:
    """获取当前请求的 ``user_id``（中间件已注入到 ``request.state``）。

    回退到 header，避免测试场景跳过中间件时取不到值。
    无 ``X-User-ID`` 时返回空串（匿名用户）。
    """
    user_id = getattr(request.state, "user_id", "")
    if not user_id:
        user_id = request.headers.get("X-User-ID", "")
    return user_id or ""


def get_acl(request: Request) -> RepoAccessControl:
    """从 ``app.state.acl`` 取 ACL 实例。"""
    acl = getattr(request.app.state, "acl", None)
    if acl is None:
        msg = "ACL not initialized on app.state"
        raise RuntimeError(msg)
    return acl


def require_access(request: Request, repo_id: str, action: str = "read") -> None:
    """校验当前用户对 ``repo_id`` 的 ``action`` 权限，失败抛 HTTPException。

    - ACL 关闭（``ONTOAGENT_ACL_ENABLED != "true"``）→ 立即返回。
    - ACL 开启但 ``X-User-ID`` 缺失 → 401。
    - 用户无对应权限 → 403。
    - ``repo_id`` 为空（如未注册的新仓库）→ 在 ACL 开启时返回 401/403，
      注册类端点（POST /repos）应先创建仓库再 grant admin，不走此函数。
    """
    if not is_acl_enabled():
        return
    if not repo_id:
        # 没有 repo_id 通常意味着匿名访问受保护资源
        raise HTTPException(status_code=400, detail="repo_id is required for access check")
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="X-User-ID header required when ACL is enabled")
    acl = get_acl(request)
    if not acl.can_access(user_id, repo_id, action):
        raise HTTPException(
            status_code=403,
            detail=f"user '{user_id}' has no '{action}' access to repo '{repo_id}'",
        )


class RepoAuthMiddleware(BaseHTTPMiddleware):
    """提取 ``X-User-ID`` 注入 ``request.state.user_id``。

    本中间件始终注入 ``user_id``，不做权限拦截；具体的 ``repo_id`` 校验交给路由层
    调用 ``require_access`` 完成（避免在中间件里提前消费请求体导致路由拿不到 body）。
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        user_id = request.headers.get("X-User-ID", "")
        request.state.user_id = user_id
        # 即便 ACL 关闭也注入 user_id；具体拦截由路由层负责
        if not is_acl_enabled() or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        if not user_id and request.url.path.startswith("/api/"):
            # 受保护 API 缺少 user_id → 让 require_access 在路由里抛 401，统一错误格式
            # 这里只放行，路由层负责具体 401/403
            return await call_next(request)
        return await call_next(request)
