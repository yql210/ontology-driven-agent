"""Schema 迁移框架。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ontoagent.store.graph_store import GraphStore


def is_nebula(store: GraphStore) -> bool:
    """检测 store 是否为 NebulaGraphStore。"""
    return type(store).__name__ == "NebulaGraphStore"


def nebula_unique_index_stmt(label: str, prop: str = "id") -> str:
    """生成 NebulaGraph 的唯一索引 DDL（等价于 Neo4j 的 CONSTRAINT ... IS UNIQUE）。

    NebulaGraph 没有 Neo4j 风格的 CONSTRAINT，用 ``CREATE TAG INDEX`` 替代。
    """
    return f"CREATE TAG INDEX IF NOT EXISTS `uniq_{label}_{prop}` ON `{label}`(`{prop}`);"


def nebula_drop_index_stmt(label: str, prop: str = "id") -> str:
    """生成 NebulaGraph 的 DROP TAG INDEX DDL。"""
    return f"DROP TAG INDEX IF EXISTS `uniq_{label}_{prop}`;"


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
