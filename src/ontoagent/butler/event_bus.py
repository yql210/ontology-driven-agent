"""Event bus for async event handling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass
class ButlerEvent:
    """Event in the butler system."""

    event_id: str = field(default_factory=lambda: str(uuid4()))
    event_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class _Subscription:
    """Internal subscription data."""

    subscription_id: str
    event_type: str
    callback: callable[[ButlerEvent], Any]
    queue: asyncio.Queue[ButlerEvent]
    task: asyncio.Task[None]


class EventBus:
    """Async event bus for pub/sub."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, _Subscription] = {}

    def subscribe(
        self,
        event_type: str,
        callback: callable[[ButlerEvent], Any],
    ) -> str:
        """Subscribe to events."""
        subscription_id = uuid4().hex[:8]
        queue: asyncio.Queue[ButlerEvent] = asyncio.Queue()

        async def consumer_loop() -> None:
            while True:
                try:
                    event = await queue.get()
                    if event is None:
                        break
                    result = callback(event)
                    if asyncio.iscoroutine(result):
                        await result
                except asyncio.CancelledError:
                    break

        task = asyncio.create_task(consumer_loop())

        self._subscriptions[subscription_id] = _Subscription(
            subscription_id=subscription_id,
            event_type=event_type,
            callback=callback,
            queue=queue,
            task=task,
        )
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        """Unsubscribe from events."""
        sub = self._subscriptions.pop(subscription_id, None)
        if sub:
            sub.task.cancel()

    async def publish(self, event: ButlerEvent) -> None:
        """Publish an event."""
        for sub in list(self._subscriptions.values()):
            if sub.event_type == "*" or sub.event_type == event.event_type:
                await sub.queue.put(event)

    def publish_sync(self, event: ButlerEvent) -> None:
        """Publish an event from sync context."""
        try:
            asyncio.get_running_loop()
            # 已有运行中的循环，创建新线程
            import threading

            def run_in_thread() -> None:
                asyncio.run(self.publish(event))

            thread = threading.Thread(target=run_in_thread, daemon=True)
            thread.start()
        except RuntimeError:
            # 没有运行中的循环
            asyncio.run(self.publish(event))

    def get_queue_depth(self, subscription_id: str) -> int:
        """Get queue depth for a subscription."""
        sub = self._subscriptions.get(subscription_id)
        if sub:
            return sub.queue.qsize()
        return 0
