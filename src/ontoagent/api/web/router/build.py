"""Build API router — 异步触发仓库构建。

设计要点：
- POST /build 返回 202 + task_id，构建在后台 ``asyncio.create_task`` 中执行。
- task 状态保存在 ``app.state.build_tasks`` dict（内存级，重启丢失）。
- ``repo_url`` 若为本地路径（以 ``/`` 或 ``./`` 开头）直接用；否则调用 GitCloneService。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from ontoagent.api.web.rate_limit import limiter
from ontoagent.config import OntoAgentConfig
from ontoagent.service.git_clone import GitCloneError, GitCloneService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["build"])


class BuildRequest(BaseModel):
    """构建请求。

    Attributes:
        repo_url: Git URL 或本地路径（以 ``/`` 或 ``./`` 开头视为本地）。
        branch: 待克隆分支（仅远程 URL 生效），默认 ``main``。
        repo_id: 仓库标识，空则从 URL 推导。
        token: 私有仓库访问令牌（注入到 https URL 的 userinfo）。
        skip_semantic: 跳过语义提取阶段。
        skip_clustering: 跳过模块聚类阶段。
        clear: 构建前清空图库。
    """

    repo_url: str
    branch: str = "main"
    repo_id: str = ""
    token: str | None = None
    skip_semantic: bool = False
    skip_clustering: bool = False
    clear: bool = False

    @field_validator("repo_url")
    @classmethod
    def repo_url_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            msg = "repo_url cannot be empty"
            raise ValueError(msg)
        return v.strip()


class BuildResponse(BaseModel):
    """POST /build 同步响应。"""

    task_id: str
    status: str  # "accepted"


class BuildStatusResponse(BaseModel):
    """后台构建任务状态。"""

    task_id: str
    status: str  # pending/cloning/building/success/failed
    repo_id: str
    message: str = ""
    result: dict | None = None


def _is_local_path(repo_url: str) -> bool:
    """判断 ``repo_url`` 是否为本地路径（以 ``/`` 或 ``./`` 开头）。"""
    return repo_url.startswith("/") or repo_url.startswith("./")


def _derive_repo_id(repo_url: str) -> str:
    """从 URL 推导 repo_id：取最后一段路径，去掉 ``.git`` 后缀。"""
    cleaned = repo_url.rstrip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    name = cleaned.rsplit("/", 1)[-1]
    return name or "default"


def _set_status(request: Request, task_id: str, status: BuildStatusResponse) -> None:
    """更新 task 状态到 ``app.state.build_tasks``。"""
    request.app.state.build_tasks[task_id] = status


async def _run_build(
    request: Request,
    task_id: str,
    repo_url: str,
    branch: str,
    repo_id: str,
    token: str | None,
    skip_semantic: bool,
    skip_clustering: bool,
    clear: bool,
) -> None:
    """后台构建任务：clone（如果需要）→ builder.build → 更新状态。

    任何异常都被捕获并写入 ``status=failed``，不会向上抛出（后台任务不能让异常冒泡）。
    """
    app_state = request.app.state
    current: BuildStatusResponse | None = app_state.build_tasks.get(task_id)
    if current is None:
        logger.error("build task %s vanished before run", task_id)
        return

    try:
        if _is_local_path(repo_url):
            repo_path = Path(repo_url)
            if not repo_path.exists():
                raise FileNotFoundError(f"local repo path not found: {repo_url}")
        else:
            _set_status(
                request,
                task_id,
                BuildStatusResponse(task_id=task_id, status="cloning", repo_id=repo_id),
            )
            config = OntoAgentConfig.from_env()
            clone_service = GitCloneService(config)
            repo_path = await clone_service.clone(repo_url, branch=branch, token=token)

        _set_status(
            request,
            task_id,
            BuildStatusResponse(task_id=task_id, status="building", repo_id=repo_id),
        )

        # 延迟导入避免在模块加载时拉起整条 builder 依赖链
        from ontoagent.pipeline.builder import OntoAgentBuilder

        config = OntoAgentConfig.from_env()
        builder = OntoAgentBuilder(config)
        # builder.build 是同步长任务，丢到线程池避免阻塞事件循环
        result = await asyncio.to_thread(
            builder.build,
            repo_path,
            repo_id=repo_id,
            skip_semantic=skip_semantic,
            skip_clustering=skip_clustering,
            clear=clear,
        )

        _set_status(
            request,
            task_id,
            BuildStatusResponse(
                task_id=task_id,
                status="success",
                repo_id=repo_id,
                result=result.to_dict(),
            ),
        )
    except Exception as e:
        logger.exception("build task %s failed", task_id)
        _set_status(
            request,
            task_id,
            BuildStatusResponse(
                task_id=task_id,
                status="failed",
                repo_id=repo_id,
                message=f"{type(e).__name__}: {e}",
            ),
        )


@router.post("/build", response_model=BuildResponse, status_code=202)
@limiter.limit("10/minute")
async def start_build(req: BuildRequest, request: Request) -> JSONResponse:
    """接收构建请求 → 返回 202 + task_id。

    远程 URL 走 GitCloneService，本地路径（以 ``/`` 或 ``./`` 开头）直接用。
    """
    repo_id = req.repo_id.strip() or _derive_repo_id(req.repo_url)
    task_id = uuid4().hex

    initial = BuildStatusResponse(task_id=task_id, status="pending", repo_id=repo_id)
    _set_status(request, task_id, initial)

    # 校验 URL：远程 URL 立即走 GitCloneService._validate_url 以早失败
    if not _is_local_path(req.repo_url):
        try:
            config = OntoAgentConfig.from_env()
            GitCloneService(config)._validate_url(req.repo_url)
        except GitCloneError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # 创建后台任务；引用 task 防止被 GC（Python 文档要求）
    task = asyncio.create_task(
        _run_build(
            request,
            task_id,
            req.repo_url,
            req.branch,
            repo_id,
            req.token,
            req.skip_semantic,
            req.skip_clustering,
            req.clear,
        )
    )
    # 保存 task 引用避免被垃圾回收（与状态 dict 分开存放，避免把 Task 序列化）
    if not hasattr(request.app.state, "build_asyncio_tasks"):
        request.app.state.build_asyncio_tasks = {}
    request.app.state.build_asyncio_tasks[task_id] = task

    return JSONResponse(
        status_code=202,
        content=BuildResponse(task_id=task_id, status="accepted").model_dump(),
    )


@router.get("/build/status/{task_id}", response_model=BuildStatusResponse)
@limiter.limit("60/minute")
def get_build_status(task_id: str, request: Request) -> BuildStatusResponse:
    """查询构建任务状态。

    Args:
        task_id: POST /build 返回的 task_id。

    Raises:
        HTTPException 404: task_id 不存在。
    """
    status = request.app.state.build_tasks.get(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return status
