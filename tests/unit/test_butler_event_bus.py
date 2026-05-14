"""Unit tests for butler event_bus module."""

from __future__ import annotations

import asyncio
import threading

from layerkg.butler import ButlerEvent
from layerkg.butler.event_bus import EventBus


def test_butler_event_creation():
    """ButlerEvent 可正确创建，timestamp 自动生成为 ISO 8601。"""
    event = ButlerEvent(
        event_id="test-1",
        event_type="git_change",
        payload={"files": ["a.py"]},
        source="test",
    )
    assert event.event_id == "test-1"
    assert event.event_type == "git_change"
    assert event.payload == {"files": ["a.py"]}
    assert event.source == "test"
    # timestamp 是 ISO 8601 字符串
    assert "T" in event.timestamp
    assert (
        event.timestamp.endswith("+00:00")
        or "+" in event.timestamp
        or "Z" in event.timestamp
        or len(event.timestamp) > 15
    )


def test_butler_event_defaults():
    """event_id 和 timestamp 默认值自动生成。"""
    event = ButlerEvent(
        event_type="test",
        payload={},
        source="test",
    )
    assert isinstance(event.event_id, str)
    assert len(event.event_id) > 0
    assert isinstance(event.timestamp, str)
    assert "T" in event.timestamp


async def test_subscribe_unsubscribe():
    """subscribe 返回 subscription_id，unsubscribe 后不再收到事件。"""
    bus = EventBus()
    received = []

    sub_id = bus.subscribe("git_change", lambda e: received.append(e))
    assert isinstance(sub_id, str)

    event = ButlerEvent(event_type="git_change", payload={}, source="test")
    await bus.publish(event)
    await asyncio.sleep(0.05)
    assert len(received) == 1

    bus.unsubscribe(sub_id)
    await bus.publish(event)
    await asyncio.sleep(0.05)
    assert len(received) == 1  # 不再收到


async def test_publish_wildcard():
    """通配符 '*' 订阅者收到所有事件类型。"""
    bus = EventBus()
    received = []
    bus.subscribe("*", lambda e: received.append(e))

    await bus.publish(ButlerEvent(event_type="git_change", payload={}, source="test"))
    await bus.publish(ButlerEvent(event_type="agent_trace", payload={}, source="test"))
    await asyncio.sleep(0.05)
    assert len(received) == 2


async def test_publish_type_filter():
    """非通配符订阅者只收到匹配类型的事件。"""
    bus = EventBus()
    git_received = []
    bus.subscribe("git_change", lambda e: git_received.append(e))

    await bus.publish(ButlerEvent(event_type="git_change", payload={}, source="test"))
    await bus.publish(ButlerEvent(event_type="agent_trace", payload={}, source="test"))
    await asyncio.sleep(0.05)
    assert len(git_received) == 1


async def test_get_queue_depth():
    """get_queue_depth 返回订阅者队列中的积压数。"""
    bus = EventBus()

    # 用一个慢消费者测试
    async def slow_consumer(e):
        await asyncio.sleep(0.1)

    sub_id = bus.subscribe("git_change", slow_consumer)

    # 快速发布多个事件
    for i in range(5):
        await bus.publish(ButlerEvent(event_type="git_change", payload={"i": i}, source="test"))
    await asyncio.sleep(0.01)
    depth = bus.get_queue_depth(sub_id)
    assert depth > 0  # 消费者来不及消费


async def test_publish_sync():
    """publish_sync 同步发布事件，订阅者收到。"""
    bus = EventBus()
    received = []
    bus.subscribe("test", lambda e: received.append(e))

    # publish_sync 在没有运行事件循环时用 asyncio.run()
    # 在已有事件循环的测试中，用线程模拟同步调用
    def sync_publish():
        bus.publish_sync(ButlerEvent(event_type="test", payload={}, source="test"))

    t = threading.Thread(target=sync_publish)
    t.start()
    t.join()
    await asyncio.sleep(0.1)
    assert len(received) >= 1
