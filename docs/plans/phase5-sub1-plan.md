# Phase 5 子阶段 1 实施计划

> 设计方案：`docs/plans/phase5-sub1-design.md` V2（审核 PASS）
> 预计：~35 个测试，4 个源文件，3 个 batch

---

## Batch 1：EventBus + Scheduler（Task 1-6）

### Task 1: 创建 butler 包结构 + ButlerEvent 数据类

**文件**: `src/layerkg/butler/__init__.py`, `src/layerkg/butler/event_bus.py`

**测试**: `tests/unit/test_butler_event_bus.py::test_butler_event_creation`

```python
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
    assert event.timestamp.endswith("+00:00") or "+" in event.timestamp or "Z" in event.timestamp or len(event.timestamp) > 15
```

**实现要点**:
- `ButlerEvent` 是 `@dataclass`
- `timestamp` 默认值 `field(default_factory=lambda: datetime.now(UTC).isoformat())`
- `event_id` 默认值 `field(default_factory=lambda: str(uuid4()))`
- `__init__.py` 导出 `ButlerEvent`

---

### Task 2: EventBus subscribe + unsubscribe

**文件**: `src/layerkg/butler/event_bus.py`

**测试**: `tests/unit/test_butler_event_bus.py::test_subscribe_unsubscribe`

```python
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
```

**实现要点**:
- `EventBus.__init__`: `self._subscriptions: dict[str, _Subscription] = {}`
- `_Subscription` 包含 `subscription_id`, `event_type`, `callback`, `queue`
- subscribe: 创建 `_Subscription`，启动 `_consumer_loop` 协程从 queue 取事件调用 callback
- unsubscribe: 取消 consumer task，删除 subscription
- 用 `uuid4().hex[:8]` 生成 subscription_id

---

### Task 3: EventBus publish + 通配符 + get_queue_depth

**文件**: `src/layerkg/butler/event_bus.py`

**测试**:
```python
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
```

**实现要点**:
- `publish`: 遍历 `_subscriptions`，匹配 event_type（支持 `"*"` 通配），放入 queue
- `get_queue_depth`: 返回 `subscription.queue.qsize()`
- consumer loop: `while True: event = await queue.get(); await callback(event)`

---

### Task 4: EventBus publish_sync

**文件**: `src/layerkg/butler/event_bus.py`

**测试**:
```python
async def test_publish_sync():
    """publish_sync 同步发布事件，订阅者收到。"""
    bus = EventBus()
    received = []
    bus.subscribe("test", lambda e: received.append(e))

    # publish_sync 在没有运行事件循环时用 asyncio.run()
    # 在已有事件循环的测试中，用线程模拟同步调用
    import threading
    def sync_publish():
        bus.publish_sync(ButlerEvent(event_type="test", payload={}, source="test"))
    t = threading.Thread(target=sync_publish)
    t.start()
    t.join()
    await asyncio.sleep(0.1)
    assert len(received) >= 1
```

**实现要点**:
- `publish_sync`: 尝试获取运行中的事件循环（`asyncio.get_running_loop()`）
- 如果没有运行中的循环：`asyncio.run(self.publish(event))`
- 如果有运行中的循环（不应发生在 sync 上下文中）：创建新线程运行

---

### Task 5: Scheduler register + dispatch（基本）

**文件**: `src/layerkg/butler/scheduler.py`

**测试**: `tests/unit/test_butler_scheduler.py`

```python
async def test_scheduler_dispatch():
    """Scheduler 将事件分发到匹配的 handler。"""
    scheduler = Scheduler()
    results = []

    async def mock_handler(event):
        results.append(event.event_id)
        return HandlerResult(handler_id="h1", success=True, result_data={"ok": True}, error=None)

    scheduler.register(HandlerSpec(
        handler_id="h1",
        event_types=["git_change"],
        handler_fn=mock_handler,
    ))

    event = ButlerEvent(event_type="git_change", payload={}, source="test")
    dispatch_results = await scheduler.dispatch(event)
    assert len(dispatch_results) == 1
    assert dispatch_results[0].success is True
    assert dispatch_results[0].attempts == 1
    assert results[0] == event.event_id

async def test_scheduler_type_filter():
    """Scheduler 不匹配的 event_type 不触发 handler。"""
    scheduler = Scheduler()
    called = []

    async def mock_handler(event):
        called.append(True)
        return HandlerResult(handler_id="h1", success=True, result_data={}, error=None)

    scheduler.register(HandlerSpec(
        handler_id="h1",
        event_types=["git_change"],
        handler_fn=mock_handler,
    ))

    event = ButlerEvent(event_type="agent_trace", payload={}, source="test")
    results = await scheduler.dispatch(event)
    assert len(results) == 0
    assert len(called) == 0
```

**实现要点**:
- `Scheduler.__init__`: `self._handlers: dict[str, HandlerSpec] = {}`, `self._semaphores: dict[str, asyncio.Semaphore] = {}`, `self._status: dict[str, HandlerStatus] = {}`
- `register`: 存储 HandlerSpec，创建 Semaphore，初始化 HandlerStatus
- `dispatch`: 遍历 handlers，匹配 event_type，用 semaphore 控制并发，执行 handler_fn

---

### Task 6: Scheduler retry + timeout + get_status

**文件**: `src/layerkg/butler/scheduler.py`

**测试**:
```python
async def test_scheduler_retry():
    """Handler 失败时自动重试，记录 attempts。"""
    scheduler = Scheduler()
    call_count = 0

    async def flaky_handler(event):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient error")
        return HandlerResult(handler_id="h1", success=True, result_data={}, error=None)

    scheduler.register(HandlerSpec(
        handler_id="h1",
        event_types=["test"],
        handler_fn=flaky_handler,
        retry_count=3,
        retry_delay=0.01,
    ))

    event = ButlerEvent(event_type="test", payload={}, source="test")
    results = await scheduler.dispatch(event)
    assert results[0].success is True
    assert results[0].attempts == 3
    assert call_count == 3

async def test_scheduler_timeout():
    """Handler 超时后标记失败。"""
    scheduler = Scheduler()

    async def slow_handler(event):
        await asyncio.sleep(10)

    scheduler.register(HandlerSpec(
        handler_id="h1",
        event_types=["test"],
        handler_fn=slow_handler,
        timeout=0.05,
        retry_count=0,
    ))

    event = ButlerEvent(event_type="test", payload={}, source="test")
    results = await scheduler.dispatch(event)
    assert results[0].success is False
    assert "timeout" in results[0].error.lower() or "Timeout" in results[0].error

async def test_scheduler_get_status():
    """get_status 返回各 handler 的运行统计。"""
    scheduler = Scheduler()

    async def handler(event):
        return HandlerResult(handler_id="h1", success=True, result_data={}, error=None)

    scheduler.register(HandlerSpec(
        handler_id="h1", event_types=["test"], handler_fn=handler,
    ))

    status = scheduler.get_status()
    assert "h1" in status
    assert status["h1"].total_invocations == 0

    event = ButlerEvent(event_type="test", payload={}, source="test")
    await scheduler.dispatch(event)
    status = scheduler.get_status()
    assert status["h1"].total_invocations == 1
```

**实现要点**:
- retry 循环：`for attempt in range(1, max(1, spec.retry_count) + 1): try: ... except: if attempt < spec.retry_count + 1: await asyncio.sleep(spec.retry_delay)`。当 `retry_count=0` 时循环只执行 1 次，不重试。
- timeout: `if spec.timeout: result = await asyncio.wait_for(spec.handler_fn(event), timeout=spec.timeout) else: result = await spec.handler_fn(event)`。`timeout=None` 时不限制。
- get_status: 返回 `dict[str, HandlerStatus]`

---

## Batch 2：ConsistencyGuard + SkillStore（Task 7-11）

### Task 7: ConsistencyGuard _init_db + log_operation

**文件**: `src/layerkg/butler/consistency/__init__.py`, `src/layerkg/butler/consistency/guard.py`

**测试**: `tests/unit/test_butler_consistency_guard.py`

```python
async def test_log_operation():
    """log_operation 写入审计日志并返回 entry_id。"""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    entry_id = await guard.log_operation(
        op="skill_create",
        target_type="skill",
        target_id="skill-123",
        before=None,
        after={"name": "test_skill", "layer": "rule"},
        operator="inductor",
    )
    assert isinstance(entry_id, str)
    assert len(entry_id) > 0
```

**实现要点**:
- `_get_db()`: 每次新建 `sqlite3.connect(self._db_path)` 并执行 `PRAGMA journal_mode=WAL`
- `_init_db()`: CREATE TABLE + CREATE INDEX
- `log_operation`: 生成 UUID，`asyncio.to_thread(self._write_log, ...)` 写入
- 写入字段：entry_id, operation, target_type, target_id, before_json(json.dumps), after_json, operator, timestamp

---

### Task 8: ConsistencyGuard query + get_last_operation

**文件**: `src/layerkg/butler/consistency/guard.py`

**测试**:
```python
async def test_query_by_target():
    """query 按 target_type 和 target_id 过滤。"""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    await guard.log_operation("create", "skill", "s1", None, {"v": 1}, "butler")
    await guard.log_operation("update", "skill", "s1", {"v": 1}, {"v": 2}, "butler")
    await guard.log_operation("create", "skill", "s2", None, {"v": 1}, "butler")

    entries = await guard.query(target_type="skill", target_id="s1")
    assert len(entries) == 2
    assert entries[0].operation == "create"

async def test_get_last_operation():
    """get_last_operation 返回最近的操作。"""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    await guard.log_operation("create", "skill", "s1", None, {"v": 1}, "butler")
    await guard.log_operation("update", "skill", "s1", {"v": 1}, {"v": 2}, "butler")

    last = await guard.get_last_operation("skill", "s1")
    assert last.operation == "update"
    assert json.loads(last.after) == {"v": 2}
```

**实现要点**:
- `query`: `asyncio.to_thread(self._query_db, ...)` 执行 SELECT，参数化查询
- `get_last_operation`: `ORDER BY rowid DESC LIMIT 1`

---

### Task 9: SkillStore _init_db + create + get

**文件**: `src/layerkg/butler/skills/__init__.py`, `src/layerkg/butler/skills/store.py`

**测试**: `tests/unit/test_butler_skill_store.py`

```python
async def test_skill_create_and_get():
    """create 写入技能，get 能读回。"""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    skill = SkillEntity(
        skill_id="s1",
        name="detect_import_cycle",
        layer=SkillLayer.RULE,
        pattern={"tool_sequence": ["graph_query", "search_code"]},
        action={"action_type": "query_refactor"},
        confidence=0.85,
        source="inductor",
    )
    sid = await store.create(skill)
    assert sid == "s1"

    fetched = await store.get("s1")
    assert fetched is not None
    assert fetched.name == "detect_import_cycle"
    assert fetched.layer == SkillLayer.RULE
    assert fetched.confidence == 0.85
    assert fetched.hit_count == 0
    assert fetched.status == "candidate"

async def test_skill_confidence_validation():
    """confidence 超出范围时 ValueError。"""
    with pytest.raises(ValueError):
        SkillEntity(
            skill_id="s1", name="bad", layer=SkillLayer.RULE,
            pattern={}, action={}, confidence=1.5, source="test",
        )
```

**实现要点**:
- `SkillEntity.__post_init__`: 校验 `0.0 <= confidence <= 1.0`
- `_init_db()`: CREATE TABLE skills + 3 个索引
- `create`: `asyncio.to_thread(self._write_skill, ...)` 写入，pattern/action 用 `json.dumps()`
- `get`: `asyncio.to_thread(self._read_skill, ...)` 读取，pattern/action 用 `json.loads()`
- SkillStore `__init__` 可选 `guard: ConsistencyGuard | None`，create 时如果有 guard 则记录审计

---

### Task 10: SkillStore update + delete + list_by_layer + count_by_layer

**文件**: `src/layerkg/butler/skills/store.py`

**测试**:
```python
async def test_skill_update():
    """update 修改指定字段。"""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    skill = SkillEntity(skill_id="s1", name="test", layer=SkillLayer.RULE,
                        pattern={}, action={}, confidence=0.5, source="test")
    await store.create(skill)

    ok = await store.update("s1", confidence=0.9, status="active")
    assert ok is True

    fetched = await store.get("s1")
    assert fetched.confidence == 0.9
    assert fetched.status == "active"

async def test_skill_soft_delete():
    """delete 是软删除，status → deprecated。"""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    skill = SkillEntity(skill_id="s1", name="test", layer=SkillLayer.RULE,
                        pattern={}, action={}, confidence=0.5, source="test")
    await store.create(skill)

    ok = await store.delete("s1")
    assert ok is True
    fetched = await store.get("s1")
    assert fetched.status == "deprecated"

async def test_list_by_layer():
    """list_by_layer 按层级和状态过滤。"""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    for i, layer in enumerate([SkillLayer.RULE, SkillLayer.RULE, SkillLayer.META]):
        s = SkillEntity(skill_id=f"s{i}", name=f"t{i}", layer=layer,
                        pattern={}, action={}, confidence=0.5, source="test",
                        status="active" if i < 2 else "candidate")
        await store.create(s)

    rules = await store.list_by_layer(SkillLayer.RULE, status="active")
    assert len(rules) == 2

async def test_count_by_layer():
    """count_by_layer 返回各层级计数。"""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    for i in range(3):
        s = SkillEntity(skill_id=f"s{i}", name=f"t{i}", layer=SkillLayer.RULE,
                        pattern={}, action={}, confidence=0.5, source="test")
        await store.create(s)
    s = SkillEntity(skill_id="s3", name="t3", layer=SkillLayer.META,
                    pattern={}, action={}, confidence=0.7, source="test")
    await store.create(s)

    counts = await store.count_by_layer()
    assert counts[SkillLayer.RULE] == 3
    assert counts[SkillLayer.META] == 1
```

**实现要点**:
- `update`: `UPDATE skills SET ... , updated_at = ? WHERE skill_id = ?`，动态构建 SET 子句
- `delete`: `UPDATE skills SET status = 'deprecated', updated_at = ? WHERE skill_id = ?`
- `list_by_layer`: `SELECT * FROM skills WHERE layer = ? AND status = ?`
- `count_by_layer`: `SELECT layer, COUNT(*) FROM skills GROUP BY layer`
- 所有方法异步：`asyncio.to_thread(self._db_xxx, ...)`

---

### Task 11: SkillStore search_by_pattern + get_candidates + increment_hit_count

**文件**: `src/layerkg/butler/skills/store.py`

**测试**:
```python
async def test_search_by_pattern():
    """search_by_pattern 用 json_extract 查询 pattern 字段。"""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    s1 = SkillEntity(skill_id="s1", name="t1", layer=SkillLayer.RULE,
                     pattern={"tool": "graph_query"}, action={}, confidence=0.5, source="test")
    s2 = SkillEntity(skill_id="s2", name="t2", layer=SkillLayer.RULE,
                     pattern={"tool": "search_code"}, action={}, confidence=0.5, source="test")
    await store.create(s1)
    await store.create(s2)

    results = await store.search_by_pattern("tool", "graph_query")
    assert len(results) == 1
    assert results[0].skill_id == "s1"

async def test_get_candidates():
    """get_candidates 返回 confidence >= min_confidence 且 status = candidate。"""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    for i, conf in enumerate([0.3, 0.6, 0.8]):
        s = SkillEntity(skill_id=f"s{i}", name=f"t{i}", layer=SkillLayer.RULE,
                        pattern={}, action={}, confidence=conf, source="test")
        await store.create(s)

    candidates = await store.get_candidates(min_confidence=0.5)
    assert len(candidates) == 2

async def test_increment_hit_count():
    """increment_hit_count 原子递增 hit_count。"""
    store = SkillStore(db_path=str(tmp_path / "skills.db"))
    s = SkillEntity(skill_id="s1", name="t1", layer=SkillLayer.RULE,
                    pattern={}, action={}, confidence=0.5, source="test")
    await store.create(s)

    await store.increment_hit_count("s1")
    await store.increment_hit_count("s1")
    fetched = await store.get("s1")
    assert fetched.hit_count == 2
```

**实现要点**:
- `search_by_pattern`: `SELECT * FROM skills WHERE json_extract(pattern_json, '$.' || ?) = ?`
- `get_candidates`: `SELECT * FROM skills WHERE status = 'candidate' AND confidence >= ?`
- `increment_hit_count`: `UPDATE skills SET hit_count = hit_count + 1 WHERE skill_id = ?`

---

## Batch 3：集成验证 + ruff 清理（Task 12-13）

### Task 12: EventBus + Scheduler + Guard 集成测试

**文件**: `tests/unit/test_butler_integration.py`

**测试**:
```python
async def test_eventbus_scheduler_integration():
    """EventBus publish → Scheduler dispatch → Handler 执行 → Guard 审计。"""
    bus = EventBus()
    scheduler = Scheduler()
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))

    handler_results = []

    async def tracked_handler(event):
        await guard.log_operation("handle", "event", event.event_id, None, {"status": "done"}, "test_handler")
        handler_results.append(event.event_id)
        return HandlerResult(handler_id="h1", success=True, result_data={}, error=None)

    scheduler.register(HandlerSpec(
        handler_id="h1", event_types=["git_change"], handler_fn=tracked_handler,
    ))
    bus.subscribe("*", scheduler.dispatch)

    event = ButlerEvent(event_type="git_change", payload={"files": ["a.py"]}, source="git")
    await bus.publish(event)
    await asyncio.sleep(0.1)

    assert len(handler_results) == 1
    entries = await guard.query(target_type="event", target_id=event.event_id)
    assert len(entries) == 1
    assert entries[0].operation == "handle"
```

**实现要点**:
- 纯测试文件，验证 EventBus → Scheduler → ConsistencyGuard 的端到端流程
- 无新源代码

---

### Task 13: SkillStore + Guard 集成测试 + ruff 清理

**文件**: `tests/unit/test_butler_skill_integration.py`

**测试**:
```python
async def test_skill_store_with_guard():
    """SkillStore 的写操作通过 Guard 记录审计日志。"""
    guard = ConsistencyGuard(db_path=str(tmp_path / "audit.db"))
    store = SkillStore(db_path=str(tmp_path / "skills.db"), guard=guard)

    skill = SkillEntity(skill_id="s1", name="test", layer=SkillLayer.RULE,
                        pattern={"k": "v"}, action={"a": 1}, confidence=0.7, source="inductor")
    await store.create(skill)

    # Guard 应记录了 create 操作
    entries = await guard.query(target_type="skill", target_id="s1")
    assert len(entries) == 1
    assert entries[0].operation == "skill_create"
    assert json.loads(entries[0].after)["name"] == "test"

    await store.update("s1", status="active")
    entries = await guard.query(target_type="skill", target_id="s1")
    assert len(entries) == 2

    await store.delete("s1")
    entries = await guard.query(target_type="skill", target_id="s1")
    assert len(entries) == 3
    last = entries[-1]
    assert last.operation == "skill_delete"
```

**实现要点**:
- SkillStore create/update/delete 内部：`if self._guard: await self._guard.log_operation(...)`
- ruff 清理：`ruff check src/layerkg/butler/ tests/unit/test_butler*.py` + `ruff format`
- 全量测试验证：`uv run pytest tests/ -v` 确保 910 + ~35 新测试全部通过

---

## 编码规范

```python
# 导入顺序
from __future__ import annotations  # 如需要
import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Callable
from uuid import uuid4

# Docstring: Google 风格
# 类型注解: 使用 Python 3.13 语法 (str | None, 不用 Optional)
# SQLite 连接: _get_db() 每次新建 + WAL
# 异步方法: asyncio.to_thread() 包装同步 SQLite 操作
```

## 验证清单

```bash
# Batch 完成后
uv run pytest tests/unit/test_butler_*.py -v          # 新测试全部通过
uv run pytest tests/ -v                                # 910 + ~35 全部通过
uv run ruff check src/layerkg/butler/ tests/unit/test_butler*.py
uv run ruff format --check src/layerkg/butler/ tests/unit/test_butler*.py
```
