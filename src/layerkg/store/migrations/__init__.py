"""Schema 迁移框架。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from layerkg.store.graph_store import GraphStore


class MigrationBase(ABC):
    """迁移基类。

    所有迁移必须实现 upgrade 和 downgrade。
    upgrade 必须幂等（可安全重复执行）。
    downgrade 应尽可能恢复到迁移前状态。
    """

    version_from: str = "0.0.0"
    version_to: str = "0.0.0"
    description: str = ""

    @abstractmethod
    def upgrade(self, store: GraphStore) -> None:
        """执行升级迁移。"""

    @abstractmethod
    def downgrade(self, store: GraphStore) -> None:
        """执行回滚迁移。"""
