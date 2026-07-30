"""仓库级访问控制（SQLite 持久化）。

权限级别（升序）：
- ``reader``：只读（query, graph view）
- ``writer``：读 + 写（build, update）
- ``admin``：读 + 写 + 删除 + 授权

实现约束：
- 使用 Python 标准库 ``sqlite3``，不引入新依赖。
- 表 ``repo_permissions`` 幂等创建（``CREATE TABLE IF NOT EXISTS``）。
- 主键 ``(user_id, repo_id)``：``grant`` 走 upsert，重复授权只更新 role。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


ROLE_READER = "reader"
ROLE_WRITER = "writer"
ROLE_ADMIN = "admin"

#: 角色到权重映射，用于权限比较（admin > writer > reader）。
ROLE_LEVELS: dict[str, int] = {
    ROLE_READER: 1,
    ROLE_WRITER: 2,
    ROLE_ADMIN: 3,
}

#: 操作到所需角色权重的映射。未知操作一律视为不通过。
ACTION_LEVELS: dict[str, int] = {
    "read": ROLE_LEVELS[ROLE_READER],
    "write": ROLE_LEVELS[ROLE_WRITER],
    "admin": ROLE_LEVELS[ROLE_ADMIN],
}


def _normalize_role(role: str) -> str:
    """规范化 role：去除空白并转小写，校验合法性。"""
    normalized = role.strip().lower()
    if normalized not in ROLE_LEVELS:
        msg = f"invalid role: {role!r}; expected one of {sorted(ROLE_LEVELS)}"
        raise ValueError(msg)
    return normalized


@dataclass
class RepoPermission:
    """用户对仓库的权限记录。

    Attributes:
        user_id: 用户标识（来自 ``X-User-ID`` header）。
        repo_id: 仓库标识（RepositoryEntity.id）。
        role: 权限角色，``admin`` / ``writer`` / ``reader``。
        granted_at: ISO 8601 授权时间戳。
    """

    user_id: str
    repo_id: str
    role: str
    granted_at: str = ""


class RepoAccessControl:
    """应用层仓库权限控制（SQLite 持久化）。

    权限级别：
    - admin: 读/写/删除/授权
    - writer: 读/写（build, update）
    - reader: 只读（query, graph view）
    """

    def __init__(self, db_path: str = "ontoagent_acl.db") -> None:
        """初始化 SQLite 连接并幂等建表。

        Args:
            db_path: SQLite 数据库文件路径，``:memory:`` 表示纯内存库（测试友好）。
        """
        self._db_path = db_path
        # ``check_same_thread=False``：FastAPI 在线程池中跑同步路由，可能跨线程访问。
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repo_permissions (
                user_id   TEXT NOT NULL,
                repo_id   TEXT NOT NULL,
                role      TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                PRIMARY KEY (user_id, repo_id)
            )
            """
        )
        # ``ON CONFLICT DO UPDATE`` 走 upsert，要求 PRIMARY KEY 上有唯一约束；显式声明 INDEX 作为冗余保障。
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_repo_permissions_pk ON repo_permissions(user_id, repo_id)"
        )
        self._conn.commit()

    def grant(self, user_id: str, repo_id: str, role: str) -> None:
        """授权；已存在则更新 role（upsert）。

        Args:
            user_id: 用户标识。
            repo_id: 仓库标识。
            role: ``admin`` / ``writer`` / ``reader``（大小写/空白不敏感）。

        Raises:
            ValueError: ``role`` 非法时。
        """
        if not user_id or not user_id.strip():
            msg = "user_id cannot be empty"
            raise ValueError(msg)
        if not repo_id or not repo_id.strip():
            msg = "repo_id cannot be empty"
            raise ValueError(msg)
        normalized = _normalize_role(role)
        granted_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT INTO repo_permissions (user_id, repo_id, role, granted_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, repo_id) DO UPDATE SET
                role = excluded.role,
                granted_at = excluded.granted_at
            """,
            (user_id, repo_id, normalized, granted_at),
        )
        self._conn.commit()
        logger.info("granted %s=%s on repo %s", user_id, normalized, repo_id)

    def revoke(self, user_id: str, repo_id: str) -> None:
        """撤销 ``user_id`` 对 ``repo_id`` 的所有权限。幂等（不存在时无操作）。"""
        self._conn.execute(
            "DELETE FROM repo_permissions WHERE user_id = ? AND repo_id = ?",
            (user_id, repo_id),
        )
        self._conn.commit()
        logger.info("revoked %s on repo %s", user_id, repo_id)

    def can_access(self, user_id: str, repo_id: str, action: str = "read") -> bool:
        """检查 ``user_id`` 是否有权对 ``repo_id`` 执行 ``action``。

        Args:
            action: ``read`` / ``write`` / ``admin``（默认 ``read``）。

        Returns:
            未知 action、未知用户、未知仓库一律 ``False``（默认拒绝）。
        """
        required = ACTION_LEVELS.get(action)
        if required is None:
            return False
        cur = self._conn.execute(
            "SELECT role FROM repo_permissions WHERE user_id = ? AND repo_id = ?",
            (user_id, repo_id),
        )
        row = cur.fetchone()
        if row is None:
            return False
        return ROLE_LEVELS.get(row["role"], 0) >= required

    def get_user_repos(self, user_id: str) -> list[dict]:
        """获取 ``user_id`` 可访问的所有仓库记录。

        Returns:
            每条记录包含 ``user_id`` / ``repo_id`` / ``role`` / ``granted_at``。
        """
        cur = self._conn.execute(
            "SELECT user_id, repo_id, role, granted_at FROM repo_permissions WHERE user_id = ?",
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_repo_users(self, repo_id: str) -> list[dict]:
        """获取 ``repo_id`` 的所有授权用户。

        Returns:
            每条记录包含 ``user_id`` / ``repo_id`` / ``role`` / ``granted_at``。
        """
        cur = self._conn.execute(
            "SELECT user_id, repo_id, role, granted_at FROM repo_permissions WHERE repo_id = ?",
            (repo_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def list_all(self) -> Iterator[RepoPermission]:
        """遍历所有权限记录（管理/调试用）。"""
        cur = self._conn.execute("SELECT user_id, repo_id, role, granted_at FROM repo_permissions ORDER BY granted_at")
        for row in cur.fetchall():
            yield RepoPermission(
                user_id=row["user_id"],
                repo_id=row["repo_id"],
                role=row["role"],
                granted_at=row["granted_at"],
            )

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        self._conn.close()

    def __enter__(self) -> RepoAccessControl:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
