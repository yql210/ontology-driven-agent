"""迁移执行器。"""

from __future__ import annotations

import fcntl
import logging
from pathlib import Path

from layerkg.exceptions import SchemaMigrationError
from layerkg.graph_store import GraphStore
from layerkg.migrations import MigrationBase
from layerkg.migrations.registry import MigrationRegistry
from layerkg.schema_version import (
    CURRENT_SCHEMA_VERSION,
    SchemaStatus,
    check_schema_version,
    get_current_db_version,
    register_schema_version,
)

logger = logging.getLogger(__name__)


class MigrationRunner:
    """迁移执行器。包含并发安全和失败恢复。"""

    def __init__(self, store: GraphStore, registry: MigrationRegistry, *, lock_dir: Path | None = None) -> None:
        self._store = store
        self._registry = registry
        self._lock_dir = lock_dir or Path.home() / ".layerkg"

    def _acquire_lock(self):
        """文件锁防止并发迁移。"""
        lock_path = self._lock_dir / "migrate.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(lock_path, "w")  # noqa: SIM115
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_file.close()
            raise SchemaMigrationError(
                "Another migration is in progress. Remove ~/.layerkg/migrate.lock if stale."
            )
        return lock_file

    def _release_lock(self, lock_file) -> None:
        """释放文件锁。"""
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()

    def run_pending(self) -> list[str]:
        """执行所有待执行的迁移。

        Returns:
            已执行的版本列表。

        Raises:
            SchemaMigrationError: 迁移失败或版本领先。
        """
        lock = self._acquire_lock()
        try:
            status = check_schema_version(self._store)
            if status == SchemaStatus.MATCH:
                logger.info("Schema version is up to date")
                return []
            if status == SchemaStatus.AHEAD:
                db_ver = get_current_db_version(self._store)
                raise SchemaMigrationError(
                    f"Database schema ({db_ver}) is ahead of code ({CURRENT_SCHEMA_VERSION}). "
                    f"Please update LayerKG."
                )

            current = get_current_db_version(self._store) or "0.0.0"
            target = CURRENT_SCHEMA_VERSION
            path = self._registry.get_migration_path(current, target)

            if not path:
                # 无迁移路径，只注册版本
                register_schema_version(self._store)
                return []

            applied: list[str] = []
            for migration in path:
                try:
                    migration.upgrade(self._store)
                    register_schema_version(self._store)
                    applied.append(migration.version_to)
                    logger.info(
                        "Applied migration %s → %s", migration.version_from, migration.version_to
                    )
                except Exception as e:
                    logger.error("Migration %s → %s failed: %s", migration.version_from, migration.version_to, e)
                    raise SchemaMigrationError(
                        f"Migration {migration.version_from} → {migration.version_to} failed: {e}. "
                        f"Previously applied: {applied}"
                    ) from e

            return applied
        finally:
            self._release_lock(lock)

    def rollback(self, to_version: str) -> list[str]:
        """回滚到指定版本。

        Args:
            to_version: 目标版本。

        Returns:
            已回滚的版本列表。
        """
        lock = self._acquire_lock()
        try:
            current = get_current_db_version(self._store) or "0.0.0"
            if current == to_version:
                return []

            # 构建反向迁移路径
            forward_path = self._registry.get_migration_path(to_version, current)
            reverse_path = list(reversed(forward_path))

            rolled_back: list[str] = []
            for migration in reverse_path:
                migration.downgrade(self._store)
                rolled_back.append(migration.version_from)
                logger.info(
                    "Rolled back migration %s → %s", migration.version_to, migration.version_from
                )

            # 更新版本节点到目标版本
            if to_version == "0.0.0":
                # 删除版本节点
                self._store.query("MATCH (sv:SchemaVersion) DETACH DELETE sv")
            else:
                register_schema_version(self._store)

            return rolled_back
        finally:
            self._release_lock(lock)
