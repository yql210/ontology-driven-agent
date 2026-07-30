# Phase 1 — Task 7: Web API (build router + repo router)

## 新建文件

### 1. src/ontoagent/api/web/router/build.py

异步构建端点：

```python
from pydantic import BaseModel
from fastapi import APIRouter
from starlette.requests import Request

router = APIRouter(tags=["build"])

class BuildRequest(BaseModel):
    repo_url: str          # Git URL 或本地路径
    branch: str = "main"
    repo_id: str = ""      # 仓库标识，空则从 URL 推导
    token: str | None = None  # 私有仓库 token
    skip_semantic: bool = False
    skip_clustering: bool = False
    clear: bool = False

class BuildResponse(BaseModel):
    task_id: str
    status: str  # "accepted"

class BuildStatusResponse(BaseModel):
    task_id: str
    status: str  # pending/cloning/building/success/failed
    repo_id: str
    message: str = ""
    result: dict | None = None
```

实现：
- POST /build: 接收 BuildRequest → 返回 202 + task_id
  - 如果 repo_url 是远程 URL → GitCloneService.clone() 到临时目录
  - 如果是本地路径 → 直接用
  - 推导 repo_id: 从 URL 提取仓库名（去掉 .git 后缀），或用传入的 repo_id
  - 后台 asyncio.create_task 执行 OntoAgentBuilder.build(repo_path, repo_id=repo_id, ...)
  - 用内存 dict 存 task 状态 (task_id → BuildStatusResponse)
- GET /build/status/{task_id}: 返回构建状态

### 2. src/ontoagent/api/web/router/repo.py

仓库管理端点：

```python
router = APIRouter(tags=["repo"])

@router.get("/repos")
async def list_repos(request: Request):
    """列出所有仓库（查图中 RepositoryEntity 节点）。"""
    store = request.app.state.graph_store
    repos = store.get_nodes_by_label("RepositoryEntity", ["id", "name", "url", "status", "builtAt"])
    return {"repos": repos}

@router.post("/repos")
async def register_repo(request: Request):
    """手动注册一个仓库（不构建）。"""
    # 接收 {name, url, branch} → 写入 RepositoryEntity 节点
```

### 3. 更新 src/ontoagent/api/web/app.py

注册新 router:
```python
from ontoagent.api.web.router import build as build_router
from ontoagent.api.web.router import repo as repo_router
app.include_router(build_router.router, prefix="/api")
app.include_router(repo_router.router, prefix="/api")
```

加 app.state.build_tasks = {} 用于存异步任务状态。

## 新建测试

### tests/unit/web/test_build_router.py

- test_build_returns_202_with_task_id: POST /build 返回 202 + task_id
- test_build_status_returns_pending: GET /build/status/{task_id} 返回 pending
- test_build_status_404_for_unknown_task: 不存在的 task_id 返回 404
- test_build_with_local_path: 本地路径直接构建（不 clone）

### tests/unit/web/test_repo_router.py

- test_list_repos: GET /repos 返回仓库列表
- test_register_repo: POST /repos 注册仓库

## 约束

- 异步构建用 asyncio.create_task，不要阻塞主线程
- task 状态存在 app.state.build_tasks dict 中（内存级，重启丢失）
- build router 不需要真正的 Git clone（mock 或本地路径即可），GitCloneService 的集成留给真实使用
- 测试用 mock graph_store
- ruff check + format 通过
- 验收: uv run pytest tests/unit/web/test_build_router.py tests/unit/web/test_repo_router.py -v --tb=short
