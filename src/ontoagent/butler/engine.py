"""Butler Engine main orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ontoagent.butler.consistency.guard import ConsistencyGuard
from ontoagent.butler.event_bus import ButlerEvent, EventBus
from ontoagent.butler.handlers.base import BaseHandler, HandlerContext, HandlerResult
from ontoagent.butler.scheduler import HandlerSpec, Scheduler
from ontoagent.butler.skills.store import SkillStore

if TYPE_CHECKING:
    from ontoagent.config import OntoAgentConfig


class ButlerEngine:
    """Butler 引擎 — 事件驱动的知识管理主循环。

    支持异步上下文管理器::

        async with ButlerEngine(config) as engine:
            await engine.start()
            await engine.submit_event(event)
    """

    def __init__(self, config: OntoAgentConfig) -> None:
        """初始化 ButlerEngine。

        Args:
            config: OntoAgent 配置。
        """
        self._config = config
        self._bus = EventBus()
        self._scheduler = Scheduler()
        self._guard: ConsistencyGuard | None = None
        self._skill_store: SkillStore | None = None
        self._handlers: dict[str, BaseHandler] = {}
        self._ctx: HandlerContext | None = None
        self._running = False
        self._subscription_ids: list[str] = []

    def register_handler(self, handler: BaseHandler) -> None:
        """注册一个 Handler。

        Args:
            handler: 要注册的 Handler。
        """
        self._handlers[handler.handler_id] = handler

    async def start(self) -> None:
        """启动 Butler：初始化组件 + 注册 Handler + 启动事件循环。"""
        if self._running:
            return

        # 获取 data_dir 路径（优先使用配置，否则使用默认）
        data_dir = getattr(self._config, "data_dir", None) or Path.cwd() / ".ontoagent"
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)

        # 创建 ConsistencyGuard
        audit_db_path = data_dir / "butler_audit.db"
        self._guard = ConsistencyGuard(str(audit_db_path))

        # 创建 SkillStore
        skills_db_path = data_dir / "butler_skills.db"
        self._skill_store = SkillStore(str(skills_db_path), guard=self._guard)

        # 创建 HandlerContext
        self._ctx = HandlerContext(
            config=self._config,
            guard=self._guard,
            skill_store=self._skill_store,
        )

        # 注册所有 Handler 到 Scheduler
        for handler_id, handler in self._handlers.items():
            handler_fn = self._make_handler_fn(handler)
            spec = HandlerSpec(
                handler_id=handler_id,
                event_types=handler.event_types,
                handler_fn=handler_fn,
                retry_count=2,
                retry_delay=0.1,
                max_concurrency=10,
            )
            self._scheduler.register(spec)

        # 订阅所有 Handler 的事件类型
        subscribed_types: set[str] = set()
        for handler in self._handlers.values():
            for event_type in handler.event_types:
                if event_type not in subscribed_types:
                    sub_id = self._bus.subscribe(event_type, self._dispatch_event)
                    self._subscription_ids.append(sub_id)
                    subscribed_types.add(event_type)

        self._running = True

    async def stop(self) -> None:
        """停止 Butler：清理资源。"""
        if not self._running:
            return

        self._running = False

        # 取消所有 EventBus 订阅
        for sub_id in self._subscription_ids:
            self._bus.unsubscribe(sub_id)
        self._subscription_ids.clear()

        # 关闭 GraphStore 连接（如果有）
        if self._ctx and self._ctx._graph_store:
            self._ctx._graph_store.close()

        # 关闭 SkillStore（如果有 close 方法）
        if self._skill_store and hasattr(self._skill_store, "close"):
            close_fn = self._skill_store.close
            if asyncio.iscoroutinefunction(close_fn):
                await close_fn()
            else:
                close_fn()

        # 关闭 ConsistencyGuard（如果有 close 方法）
        if self._guard and hasattr(self._guard, "close"):
            close_fn = self._guard.close
            if asyncio.iscoroutinefunction(close_fn):
                await close_fn()
            else:
                close_fn()

        # 清理组件
        self._guard = None
        self._skill_store = None
        self._ctx = None

    async def __aenter__(self) -> ButlerEngine:
        """异步上下文管理器入口：启动引擎。"""
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """异步上下文管理器出口：停止引擎。"""
        await self.stop()

    async def submit_event(self, event: ButlerEvent) -> list[HandlerResult]:
        """提交事件到引擎，返回 Handler 执行结果。

        同时发布到 EventBus（通知其他订阅者）并分发到匹配的 Handler。
        发布 completion/failed 事件。

        Args:
            event: 要提交的事件。

        Returns:
            Handler 执行结果列表。
        """
        if not self._running:
            return []

        # 发布事件到 EventBus（通知外部订阅者）
        # _dispatch_event 会检查 event.source，跳过非 GitWatcher 来源的事件
        await self._bus.publish(event)

        # 直接通过 Scheduler 分发获取结果
        results = await self._scheduler.dispatch(event)

        # 发布 completion/failed 事件并级联 dispatch（触发 ReflectionHandler 等）
        for result in results:
            if result.success:
                completion_event = ButlerEvent(
                    event_type="handler.completed",
                    payload={
                        "original_event_type": event.event_type,
                        "handler_id": result.handler_id,
                        "success": True,
                        "file_extension": self._extract_file_extension(event),
                        "duration_ms": 0,
                    },
                    source="butler.engine",
                )
            else:
                completion_event = ButlerEvent(
                    event_type="handler.failed",
                    payload={
                        "original_event_type": event.event_type,
                        "handler_id": result.handler_id,
                        "success": False,
                        "error": result.error,
                        "attempts": result.attempts,
                    },
                    source="butler.engine",
                )
            # Publish to EventBus for external subscribers
            await self._bus.publish(completion_event)
            # Dispatch to Scheduler for cascading handlers (e.g. ReflectionHandler)
            await self._scheduler.dispatch(completion_event)
            # Note: cascading handler results are NOT published further to prevent infinite recursion

        return results

    async def status(self) -> dict[str, Any]:
        """返回引擎状态摘要。

        Returns:
            引擎状态字典。
        """
        scheduler_status = self._scheduler.get_status()

        handler_status: dict[str, dict[str, Any]] = {}
        for handler_id, status_info in scheduler_status.items():
            handler_status[handler_id] = {
                "total": status_info.total_invocations,
                "success": status_info.success_count,
                "failure": status_info.failure_count,
            }

        skill_counts: dict[str, int] = {}
        if self._skill_store:
            counts = await self._skill_store.count_by_layer()
            skill_counts = {layer.value: count for layer, count in counts.items()}

        return {
            "running": self._running,
            "handlers": {hid: h.event_types for hid, h in self._handlers.items()},
            "scheduler_status": handler_status,
            "skill_counts": skill_counts,
        }

    def _make_handler_fn(self, handler: BaseHandler) -> callable[[ButlerEvent], HandlerResult]:
        """包装 handler.handle 为 Scheduler 需要的 async callback。

        Args:
            handler: 要包装的 Handler。

        Returns:
            Async 函数，接收 ButlerEvent，返回 HandlerResult。
        """

        async def fn(event: ButlerEvent) -> HandlerResult:
            if self._ctx is None:
                return HandlerResult(success=False, error="HandlerContext not initialized")
            return await handler.handle(event, self._ctx)

        return fn

    async def _dispatch_event(self, event: ButlerEvent) -> None:
        """EventBus 回调 — 外部事件（如 GitWatcher）分发到 Scheduler。

        只处理 git_watcher 来源的事件。submit_event 路径已自己处理 dispatch。
        跳过 handler.completed/failed 事件（避免递归）。
        不进行级联 dispatch，避免无限递归。
        """
        if not self._running:
            return

        # 只处理 GitWatcher 来源的事件（serve 模式）
        # submit_event 来源的事件由 submit_event 自己 dispatch
        if not event.source.startswith("git_watcher"):
            return

        # 跳过内部产生的 completion/failed 事件，避免递归
        if event.event_type in ("handler.completed", "handler.failed"):
            return

        # 分发到 Scheduler（触发 KnowledgeUpdateHandler 等）
        results = await self._scheduler.dispatch(event)

        # 发布 completion/failed 事件到 EventBus（供外部订阅者监听）
        for result in results:
            if result.success:
                completion = ButlerEvent(
                    event_type="handler.completed",
                    payload={
                        "original_event_type": event.event_type,
                        "handler_id": result.handler_id,
                        "success": True,
                        "file_extension": self._extract_file_extension(event),
                        "duration_ms": 0,
                    },
                    source="butler.engine",
                )
            else:
                completion = ButlerEvent(
                    event_type="handler.failed",
                    payload={
                        "original_event_type": event.event_type,
                        "handler_id": result.handler_id,
                        "success": False,
                        "error": result.error,
                        "attempts": result.attempts,
                    },
                    source="butler.engine",
                )
            await self._bus.publish(completion)
            # 注意：不再级联 dispatch（避免 ReflectionHandler 触发循环）

    def _extract_file_extension(self, event: ButlerEvent) -> str:
        """从事件 payload 中提取文件扩展名。

        Args:
            event: ButlerEvent。

        Returns:
            文件扩展名（如 ".py"），如果无法提取则返回 "unknown"。
        """
        payload = event.payload

        # 优先使用 file_extension 字段
        if "file_extension" in payload:
            return str(payload["file_extension"])

        # 尝试从 file_path 提取
        file_path = payload.get("file_path", "")
        if file_path:
            path = Path(file_path)
            if path.suffix:
                return path.suffix

        # 尝试从 repo_path 提取（如果只有一个文件）
        repo_path = payload.get("repo_path", "")
        if repo_path and "." in Path(repo_path).name:
            path = Path(repo_path)
            if path.suffix:
                return path.suffix

        return "unknown"
