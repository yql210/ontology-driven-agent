"""迁移注册表。"""

from __future__ import annotations

import logging

from layerkg.store.migrations import MigrationBase

logger = logging.getLogger(__name__)


class MigrationRegistry:
    """迁移注册表。按版本顺序维护所有迁移。"""

    def __init__(self) -> None:
        self._migrations: list[MigrationBase] = []

    def register(self, migration: MigrationBase) -> None:
        """注册一个迁移。按 version_to 排序。"""
        self._migrations.append(migration)
        self._migrations.sort(key=lambda m: m.version_to)
        logger.debug("Registered migration %s → %s", migration.version_from, migration.version_to)

    def get_migration_path(self, from_ver: str, to_ver: str) -> list[MigrationBase]:
        """获取从 from_ver 到 to_ver 的迁移路径。

        按版本顺序执行，前一步的 version_to == 后一步的 version_from。
        """
        path: list[MigrationBase] = []
        current = from_ver
        for migration in self._migrations:
            if current == migration.version_from and migration.version_to <= to_ver:
                path.append(migration)
                current = migration.version_to
        return path

    def get_latest_version(self) -> str:
        """获取最新迁移的目标版本。"""
        if not self._migrations:
            return "0.0.0"
        return self._migrations[-1].version_to

    @property
    def migrations(self) -> list[MigrationBase]:
        """返回所有已注册的迁移（只读副本）。"""
        return list(self._migrations)
