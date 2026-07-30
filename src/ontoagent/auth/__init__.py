"""应用层权限控制（基于 SQLite 的 ACL）。

- ``RepoAccessControl``：仓库级权限存储与校验（admin/writer/reader）。
- ``RepoAuthMiddleware``：从 ``X-User-ID`` header 提取用户，注入 ``request.state.user_id``。
- ``is_acl_enabled``：``ONTOAGENT_ACL_ENABLED=true`` 时启用拦截，默认放行。

中间件始终注入 ``user_id``；权限校验由路由层显式调用 ``require_access`` 完成，
避免在中间件里重复读取请求体。
"""

from ontoagent.auth.acl import RepoAccessControl, RepoPermission
from ontoagent.auth.middleware import RepoAuthMiddleware, get_user_id, is_acl_enabled, require_access

__all__ = [
    "RepoAccessControl",
    "RepoPermission",
    "RepoAuthMiddleware",
    "get_user_id",
    "is_acl_enabled",
    "require_access",
]
