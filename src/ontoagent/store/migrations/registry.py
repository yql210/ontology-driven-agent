"""迁移注册表。"""

from __future__ import annotations

import logging

from ontoagent.store.migrations import MigrationBase

logger = logging.getLogger(__name__)

# 按版本顺序维护的内置迁移列表。
# 添加新迁移时在此列表末尾追加。
_BUILTIN_MIGRATIONS: list[str] = [
    "1.1.0",
]


def _load_migration(version: str) -> MigrationBase:
    """按版本号加载内置迁移模块。

    Args:
        version: 版本号字符串 (e.g. "1.1.0")。

    Returns:
        对应迁移实例。
    """
    # 版本号到模块/类的映射
    if version == "1.1.0":
        from ontoagent.store.migrations.v1_1_0_add_business_entities import (
            DataAssetAndComplianceItemMigration,
        )

        return DataAssetAndComplianceItemMigration()
    raise ValueError(f"Unknown migration version: {version}")


class MigrationRegistry:
    """迁移注册表。按版本顺序维护所有迁移。"""

    def __init__(self) -> None:
        self._migrations: list[MigrationBase] = []
        self._load_builtin()

    def _load_builtin(self) -> None:
        """加载所有内置迁移。"""
        for version in _BUILTIN_MIGRATIONS:
            try:
                migration = _load_migration(version)
                self.register(migration)
            except ValueError:
                logger.warning("Skipping unknown migration version: %s", version)

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
