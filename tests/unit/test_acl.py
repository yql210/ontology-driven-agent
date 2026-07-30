"""RepoAccessControl 单元测试。

运行：``uv run pytest tests/unit/test_acl.py -v --tb=short``

覆盖场景：
- 授权 / 撤销 / upsert 行为
- 角色层级（admin > writer > reader）
- 列表查询 / 未知用户拒绝 / 默认 admin 全权通过
- 数据持久化（重开实例仍可读到记录）
"""

from __future__ import annotations

import pytest

from ontoagent.auth.acl import RepoAccessControl

pytestmark = pytest.mark.unit


@pytest.fixture
def acl() -> RepoAccessControl:
    """每个测试一个全新的内存 SQLite 实例。"""
    return RepoAccessControl(":memory:")


def test_grant_and_can_access(acl: RepoAccessControl) -> None:
    """授予 reader 后，read 通过、write 拒绝。"""
    # Arrange
    acl.grant("alice", "repo-1", "reader")

    # Act & Assert
    assert acl.can_access("alice", "repo-1", "read") is True
    assert acl.can_access("alice", "repo-1", "write") is False
    # 默认 action 是 read
    assert acl.can_access("alice", "repo-1") is True


def test_revoke_removes_access(acl: RepoAccessControl) -> None:
    """revoke 后立刻失去任何权限，且操作幂等。"""
    # Arrange
    acl.grant("bob", "repo-2", "writer")
    assert acl.can_access("bob", "repo-2", "write") is True

    # Act
    acl.revoke("bob", "repo-2")

    # Assert
    assert acl.can_access("bob", "repo-2", "read") is False
    assert acl.can_access("bob", "repo-2", "write") is False
    # 再次 revoke 不报错（幂等）
    acl.revoke("bob", "repo-2")


def test_admin_can_write(acl: RepoAccessControl) -> None:
    """admin 角色隐含 write 权限。"""
    acl.grant("carol", "repo-3", "admin")
    assert acl.can_access("carol", "repo-3", "write") is True
    assert acl.can_access("carol", "repo-3", "admin") is True
    assert acl.can_access("carol", "repo-3", "read") is True


def test_reader_cannot_write(acl: RepoAccessControl) -> None:
    """reader 显式拒绝 write/admin。"""
    acl.grant("dave", "repo-4", "reader")
    assert acl.can_access("dave", "repo-4", "read") is True
    assert acl.can_access("dave", "repo-4", "write") is False
    assert acl.can_access("dave", "repo-4", "admin") is False


def test_get_user_repos(acl: RepoAccessControl) -> None:
    """get_user_repos 返回用户的所有授权记录。"""
    # Arrange
    acl.grant("eve", "repo-a", "reader")
    acl.grant("eve", "repo-b", "writer")
    acl.grant("eve", "repo-c", "admin")
    acl.grant("mallory", "repo-x", "admin")  # 其他用户不应出现在 eve 的列表

    # Act
    records = acl.get_user_repos("eve")

    # Assert
    assert len(records) == 3
    repo_ids = {r["repo_id"] for r in records}
    assert repo_ids == {"repo-a", "repo-b", "repo-c"}
    # 每条记录字段完整
    for r in records:
        assert r["user_id"] == "eve"
        assert r["role"] in {"reader", "writer", "admin"}
        assert r["granted_at"]


def test_get_repo_users(acl: RepoAccessControl) -> None:
    """get_repo_users 返回仓库的所有授权用户。"""
    # Arrange
    acl.grant("alice", "shared", "admin")
    acl.grant("bob", "shared", "writer")
    acl.grant("carol", "shared", "reader")
    acl.grant("alice", "other-repo", "admin")  # 不应出现在 shared 的列表

    # Act
    records = acl.get_repo_users("shared")

    # Assert
    assert len(records) == 3
    user_ids = {r["user_id"] for r in records}
    assert user_ids == {"alice", "bob", "carol"}


def test_unknown_user_has_no_access(acl: RepoAccessControl) -> None:
    """未授权用户对所有操作都返回 False（默认拒绝）。"""
    # 不 grant 任何记录
    assert acl.can_access("ghost", "repo-1", "read") is False
    assert acl.can_access("ghost", "repo-1", "write") is False
    assert acl.can_access("ghost", "repo-1", "admin") is False
    # 未知 action 也拒绝
    assert acl.can_access("ghost", "repo-1", "delete") is False
    # 用户已授权但仓库不存在
    acl.grant("alice", "repo-1", "admin")
    assert acl.can_access("alice", "repo-unknown", "read") is False


def test_default_admin_role(acl: RepoAccessControl) -> None:
    """admin 是默认全权角色：read/write/admin 全部通过，无需分别 grant。"""
    # 一个用户被 grant 一次 admin → 三种 action 全通过
    acl.grant("root", "system", "admin")
    for action in ("read", "write", "admin"):
        assert acl.can_access("root", "system", action) is True, f"admin should pass action={action}"


def test_grant_upsert_updates_role(acl: RepoAccessControl) -> None:
    """同一 (user, repo) 二次 grant 应更新 role 而非插入新记录。"""
    # Arrange
    acl.grant("frank", "repo-5", "reader")
    assert acl.can_access("frank", "repo-5", "write") is False

    # Act：升级为 writer
    acl.grant("frank", "repo-5", "writer")

    # Assert：仍是 1 条记录，role 已更新
    records = acl.get_repo_users("repo-5")
    assert len(records) == 1
    assert records[0]["role"] == "writer"
    assert acl.can_access("frank", "repo-5", "write") is True


def test_grant_invalid_role_raises(acl: RepoAccessControl) -> None:
    """非法 role 必须抛 ValueError。"""
    with pytest.raises(ValueError, match="invalid role"):
        acl.grant("user", "repo", "superuser")
    # role 大小写不敏感：ADMIN 合法
    acl.grant("user", "repo", "ADMIN")
    assert acl.can_access("user", "repo", "admin") is True


def test_grant_empty_user_or_repo_raises(acl: RepoAccessControl) -> None:
    """空 user_id / repo_id 抛 ValueError（防止匿名脏数据）。"""
    with pytest.raises(ValueError, match="user_id cannot be empty"):
        acl.grant("", "repo", "admin")
    with pytest.raises(ValueError, match="repo_id cannot be empty"):
        acl.grant("user", "", "admin")


def test_persistence_across_reopen(tmp_path) -> None:
    """同一文件路径的两个实例共享数据（SQLite 持久化生效）。"""
    # Arrange
    db_path = str(tmp_path / "acl-test.db")
    acl1 = RepoAccessControl(db_path)
    acl1.grant("alice", "repo-1", "admin")
    acl1.close()

    # Act：重新打开同一文件
    acl2 = RepoAccessControl(db_path)

    # Assert：之前 grant 的记录依然可读
    assert acl2.can_access("alice", "repo-1", "admin") is True
    assert len(acl2.get_repo_users("repo-1")) == 1
    acl2.close()
