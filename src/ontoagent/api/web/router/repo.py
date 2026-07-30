"""Repo API router — 多仓库注册、查询与权限管理。

端点概览：
- GET  /repos                          列出当前用户可访问的仓库（ACL 关闭时返回全部）
- POST /repos                          注册仓库并自动授予创建者 admin 权限
- GET  /repos/{repo_id}/permissions    列出仓库的所有授权用户（仅 admin/启用 ACL 时强制）
- POST /repos/{repo_id}/permissions    授予/更新用户权限（仅 admin 可调用）

权限模型详见 :class:`ontoagent.auth.acl.RepoAccessControl`。
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from ontoagent.api.web.rate_limit import limiter
from ontoagent.auth import get_user_id, is_acl_enabled, require_access
from ontoagent.domain.schema import RepositoryEntity

logger = logging.getLogger(__name__)

router = APIRouter(tags=["repo"])


class RepoRegisterRequest(BaseModel):
    """仓库注册请求。"""

    name: str
    url: str = ""
    branch: str = "main"

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            msg = "name cannot be empty"
            raise ValueError(msg)
        return v.strip()


class RepoResponse(BaseModel):
    """POST /repos 同步响应。"""

    id: str
    name: str
    url: str
    branch: str
    status: str


class PermissionRequest(BaseModel):
    """POST /repos/{repo_id}/permissions 请求体。"""

    user_id: str
    role: str  # admin | writer | reader

    @field_validator("user_id")
    @classmethod
    def user_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            msg = "user_id cannot be empty"
            raise ValueError(msg)
        return v.strip()

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: str) -> str:
        allowed = {"admin", "writer", "reader"}
        normalized = (v or "").strip().lower()
        if normalized not in allowed:
            msg = f"role must be one of {sorted(allowed)}, got {v!r}"
            raise ValueError(msg)
        return normalized


class PermissionResponse(BaseModel):
    """单条权限记录响应。"""

    user_id: str
    repo_id: str
    role: str
    granted_at: str


@router.get("/repos")
@limiter.limit("60/minute")
def list_repos(request: Request) -> dict:
    """列出当前用户可访问的仓库。

    - ACL 关闭：返回图中所有 ``RepositoryEntity``（旧行为）。
    - ACL 开启：先从 ACL 取用户可访问的 ``repo_id`` 集合，再与图中节点做交集；
      匿名用户（无 ``X-User-ID``）在 ACL 开启时返回空列表。
    """
    store = request.app.state.graph_store
    all_repos = store.get_nodes_by_label("RepositoryEntity", ["id", "name", "url", "status"])

    if not is_acl_enabled():
        return {"repos": all_repos}

    user_id = get_user_id(request)
    if not user_id:
        return {"repos": []}
    acl = request.app.state.acl
    allowed_ids = {p["repo_id"] for p in acl.get_user_repos(user_id)}
    return {"repos": [r for r in all_repos if r.get("id") in allowed_ids]}


@router.post("/repos", response_model=RepoResponse, status_code=201)
@limiter.limit("10/minute")
def register_repo(req: RepoRegisterRequest, request: Request) -> JSONResponse:
    """注册仓库（不触发构建），并把创建者设为 admin。

    - ACL 开启时：``X-User-ID`` 缺失 → 401。
    - ACL 关闭时：不写 ACL 记录（旧流程保持不变）。
    """
    store = request.app.state.graph_store
    try:
        entity = RepositoryEntity(name=req.name, url=req.url, branch=req.branch, status="pending")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    props = {k: v for k, v in asdict(entity).items() if v is not None}
    store.merge_node("RepositoryEntity", props)

    if is_acl_enabled():
        user_id = get_user_id(request)
        if not user_id:
            raise HTTPException(status_code=401, detail="X-User-ID header required when ACL is enabled")
        request.app.state.acl.grant(user_id, entity.id, "admin")
        logger.info("auto-granted admin on repo %s to user %s", entity.id, user_id)

    logger.info("registered repo %s (id=%s)", req.name, entity.id)
    return JSONResponse(
        status_code=201,
        content=RepoResponse(
            id=entity.id,
            name=entity.name,
            url=entity.url,
            branch=entity.branch,
            status=entity.status,
        ).model_dump(),
    )


@router.get("/repos/{repo_id}/permissions")
@limiter.limit("60/minute")
def list_permissions(repo_id: str, request: Request) -> dict:
    """列出仓库的所有授权用户。

    - ACL 开启：仅 ``admin`` 角色可查看（其他角色 403）。
    - ACL 关闭：直接返回所有记录（便于运维排查）。
    """
    if is_acl_enabled():
        require_access(request, repo_id, action="admin")
    records = request.app.state.acl.get_repo_users(repo_id)
    return {"permissions": records}


@router.post("/repos/{repo_id}/permissions", response_model=PermissionResponse, status_code=201)
@limiter.limit("10/minute")
def grant_permission(repo_id: str, req: PermissionRequest, request: Request) -> JSONResponse:
    """授予/更新某用户对该仓库的权限。

    - ACL 开启：调用者必须是该仓库的 ``admin``。
    - ACL 关闭：允许直接调用（运维批量配置）。
    """
    if is_acl_enabled():
        require_access(request, repo_id, action="admin")

    acl = request.app.state.acl
    acl.grant(req.user_id, repo_id, req.role)
    records = acl.get_repo_users(repo_id)
    record = next((r for r in records if r["user_id"] == req.user_id), None)
    if record is None:  # pragma: no cover — 立即写入后必然存在
        raise HTTPException(status_code=500, detail="grant succeeded but record not found")
    return JSONResponse(
        status_code=201,
        content=PermissionResponse(
            user_id=record["user_id"],
            repo_id=record["repo_id"],
            role=record["role"],
            granted_at=record["granted_at"],
        ).model_dump(),
    )


@router.delete("/repos/{repo_id}/permissions/{user_id}", status_code=204)
@limiter.limit("10/minute")
def revoke_permission(repo_id: str, user_id: str, request: Request) -> JSONResponse:
    """撤销某用户的权限。

    - ACL 开启：调用者必须是该仓库的 ``admin``。
    - 不能撤销自己的 admin（避免锁死；至少保留一个 admin）。
    """
    if is_acl_enabled():
        require_access(request, repo_id, action="admin")
        caller = get_user_id(request)
        if caller == user_id:
            raise HTTPException(status_code=400, detail="cannot revoke your own admin permission")
    request.app.state.acl.revoke(user_id, repo_id)
    return JSONResponse(status_code=204, content=None)
