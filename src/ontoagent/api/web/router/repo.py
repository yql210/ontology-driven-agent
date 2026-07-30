"""Repo API router — 多仓库注册与查询。

GET /repos  返回已注册仓库列表（查图中 RepositoryEntity 节点）。
POST /repos 写入 RepositoryEntity（仅注册，不触发构建）。
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from ontoagent.api.web.rate_limit import limiter
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


@router.get("/repos")
@limiter.limit("60/minute")
def list_repos(request: Request) -> dict:
    """列出所有已注册仓库。

    从 ``RepositoryEntity`` 节点读 ``id``/``name``/``url``/``status`` 四个字段。
    """
    store = request.app.state.graph_store
    repos = store.get_nodes_by_label("RepositoryEntity", ["id", "name", "url", "status"])
    return {"repos": repos}


@router.post("/repos", response_model=RepoResponse, status_code=201)
@limiter.limit("10/minute")
def register_repo(req: RepoRegisterRequest, request: Request) -> JSONResponse:
    """注册仓库（不触发构建）。

    将 ``RepositoryEntity`` 节点 MERGE 到图中，初始状态 ``pending``。
    """
    store = request.app.state.graph_store
    try:
        entity = RepositoryEntity(name=req.name, url=req.url, branch=req.branch, status="pending")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    props = {k: v for k, v in asdict(entity).items() if v is not None}
    store.merge_node("RepositoryEntity", props)

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
