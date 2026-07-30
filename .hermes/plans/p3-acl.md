# Phase 3 — 权限隔离 ACL

## Task 1: ACL 模型 (src/ontoagent/auth/acl.py)

新建 `src/ontoagent/auth/__init__.py` 和 `src/ontoagent/auth/acl.py`:

```python
@dataclass
class RepoPermission:
    """用户对仓库的权限。"""
    user_id: str
    repo_id: str
    role: str  # "admin" | "writer" | "reader"
    
class RepoAccessControl:
    """应用层仓库权限控制（SQLite 持久化）。
    
    权限级别：
    - admin: 读/写/删除/授权
    - writer: 读/写（build, update）
    - reader: 只读（query, graph view）
    """
    
    def __init__(self, db_path: str = "ontoagent_acl.db"):
        """初始化 SQLite 连接，自动建表。"""
        
    def grant(self, user_id: str, repo_id: str, role: str) -> None:
        """授权。如果已存在则更新 role。"""
        
    def revoke(self, user_id: str, repo_id: str) -> None:
        """撤销权限。"""
        
    def can_access(self, user_id: str, repo_id: str, action: str = "read") -> bool:
        """检查用户是否有权限。action: read/write/admin。"""
        
    def get_user_repos(self, user_id: str) -> list[dict]:
        """获取用户可访问的所有仓库。"""
        
    def get_repo_users(self, repo_id: str) -> list[dict]:
        """获取仓库的所有授权用户。"""
```

## Task 2: Web 中间件 (src/ontoagent/auth/middleware.py)

```python
class RepoAuthMiddleware:
    """FastAPI 中间件：从请求中提取 user_id，注入 repo_id 过滤。
    
    - POST /api/build: 检查 user_id 对 repo_id 有 write 权限
    - GET /api/repos: 只返回 user_id 有权限的仓库
    - GET /api/graph: 自动注入 repoId 过滤（如果 user 只有单个 repo 权限）
    """
```

简化版：通过 X-User-ID header 传 user_id（无真实认证系统时用 API Key 映射）。

## Task 3: API 集成

更新 web/router/repo.py 和 web/router/build.py：
- 所有端点从 request headers 获取 user_id
- GET /repos 只返回用户有权限的仓库
- POST /build 检查用户对 repo_id 的 write 权限
- 新增 GET /api/repos/{repo_id}/permissions 和 POST /api/repos/{repo_id}/permissions

## Task 4: 测试

tests/unit/test_acl.py:
- test_grant_and_can_access
- test_revoke_removes_access  
- test_admin_can_write
- test_reader_cannot_write
- test_get_user_repos
- test_get_repo_users
- test_unknown_user_has_no_access
- test_default_admin_role

## 约束

- SQLite 用 Python 标准库 sqlite3，不引入新依赖
- ACL 表幂等创建（CREATE TABLE IF NOT EXISTS）
- 中间件是可选的（不设置 ONTOAGENT_ACL_ENABLED 时不拦截）
- ruff check + format 通过
- 验收: uv run pytest tests/unit/test_acl.py -v --tb=short
