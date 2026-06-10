from __future__ import annotations

from abc import ABC, abstractmethod


class Connector(ABC):
    """外部系统接入抽象接口。"""

    @abstractmethod
    def fetch(self, params: dict) -> list[dict]:
        """从外部系统拉取数据。"""

    @abstractmethod
    def sync(self, graph_store: object, params: dict | None = None) -> int:
        """批量同步外部数据到图谱，返回同步条数。"""

    @abstractmethod
    def push(self, data: dict) -> bool:
        """推送数据到外部系统。"""

    @abstractmethod
    def health_check(self) -> bool:
        """健康检查。"""


class ConnectorRegistry:
    """Connector 注册表。"""

    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}

    def register(self, name: str, connector: Connector) -> None:
        if name in self._connectors:
            raise ValueError(f"Connector '{name}' already registered")
        self._connectors[name] = connector

    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)

    def list_connectors(self) -> list[str]:
        return sorted(self._connectors.keys())

    def clear(self) -> None:
        self._connectors.clear()
