from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ontoagent.butler.event_bus import ButlerEvent

if TYPE_CHECKING:
    from ontoagent.butler.consistency.guard import ConsistencyGuard
    from ontoagent.butler.skills.store import SkillStore
    from ontoagent.config import OntoAgentConfig
    from ontoagent.store.graph_store import GraphStore


@dataclass
class HandlerContext:
    """Handler 执行上下文，注入所有依赖。"""

    config: OntoAgentConfig
    guard: ConsistencyGuard | None = None
    skill_store: SkillStore | None = None
    _graph_store: GraphStore | None = field(default=None, init=False, repr=False)

    def get_graph_store(self) -> GraphStore:
        """Lazy-init GraphStore (Neo4j)。"""
        if self._graph_store is None:
            from ontoagent.store.neo4j_store import Neo4jGraphStore

            self._graph_store = Neo4jGraphStore(
                uri=self.config.neo4j_uri,
                user=self.config.neo4j_user,
                password=self.config.neo4j_password,
            )
        return self._graph_store


@dataclass
class HandlerResult:
    """Handler 返回的标准化结果。"""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class BaseHandler(ABC):
    """所有 Butler Handler 的基类。"""

    @property
    @abstractmethod
    def handler_id(self) -> str:
        """唯一标识符。"""

    @property
    @abstractmethod
    def event_types(self) -> list[str]:
        """订阅的事件类型列表。"""

    @abstractmethod
    async def handle(self, event: ButlerEvent, ctx: HandlerContext) -> HandlerResult:
        """处理事件，返回标准化结果。"""
