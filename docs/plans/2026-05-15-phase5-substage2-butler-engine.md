# Phase 5 Sub-stage 2: Butler Engine 主循环 + 知识更新 Handler

> **For Hermes:** Use claude-code skill print mode (`-p`) to implement this plan task-by-task. Follow TDD. Each task 2-5 min.

**Goal:** 实现 Butler Engine 主循环，将已有的 EventBus + Scheduler + ConsistencyGuard + SkillStore 基础设施串联起来，加上知识更新 Handler 和反思归纳机制，形成完整的闭环。

**Architecture:** Butler Engine 是一个事件驱动的长期运行 Agent。它监听代码变更事件 → 通过 Handler 执行增量知识更新 → 通过 ConsistencyGuard 审计 → 通过 SkillStore 沉淀模式。主循环基于 asyncio，事件源可以是 Git 变更检测或外部触发。

**Tech Stack:** Python 3.13+, asyncio, SQLite (audit/skills), Neo4j (knowledge graph), ChromaDB (vectors)

---

## 现有基础设施（Sub-stage 1 已完成）

| 组件 | 文件 | 功能 |
|------|------|------|
| EventBus | `butler/event_bus.py` | 异步 pub/sub，ButlerEvent 数据类 |
| Scheduler | `butler/scheduler.py` | Handler 分发+重试+并发控制，HandlerSpec/HandlerResult/HandlerStatus |
| ConsistencyGuard | `butler/consistency/guard.py` | SQLite 审计日志，log_operation/query/get_last_operation |
| SkillStore | `butler/skills/store.py` | SQLite 技能存储，CRUD + 三层架构(RULE/META/HARNESS) |
| IncrementalUpdater | `incremental_updater.py` | 四阶段流水线：变更检测→影响传播→选择性重生成→图验证 |
| GitChangeDetector | `change_detector.py` | Git diff + SHA256 变更检测 |
| Builder | `builder.py` | 全量构建（多语言） |

## 目标架构

```
ButlerEngine (主循环)
  ├── 监听事件源 (Git 变更 / 外部触发)
  ├── 分发到 Handler:
  │   ├── KnowledgeUpdateHandler — 增量知识更新
  │   └── ReflectionHandler — 反思归纳，沉淀技能
  ├── ConsistencyGuard — 审计所有操作
  └── SkillStore — 存储沉淀的技能模式
```

---

## Day 0: KnowledgeUpdateHandler

### Task 0-1: 创建 Handler 基类

**Objective:** 定义所有 Butler Handler 的通用接口。

**Files:**
- Create: `src/layerkg/butler/handlers/__init__.py`
- Create: `src/layerkg/butler/handlers/base.py`
- Test: `tests/unit/test_butler_handlers.py`

**接口设计:**
```python
# src/layerkg/butler/handlers/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from layerkg.butler.event_bus import ButlerEvent

if TYPE_CHECKING:
    from layerkg.butler.consistency.guard import ConsistencyGuard
    from layerkg.butler.skills.store import SkillStore
    from layerkg.config import LayerKGConfig
    from layerkg.graph_store import GraphStore

@dataclass
class HandlerContext:
    """Handler 执行上下文，注入所有依赖。"""
    config: LayerKGConfig
    guard: ConsistencyGuard | None = None
    skill_store: SkillStore | None = None
    _graph_store: GraphStore | None = field(default=None, init=False, repr=False)

    def get_graph_store(self) -> GraphStore:
        """Lazy-init GraphStore (Neo4j)。"""
        if self._graph_store is None:
            from layerkg.neo4j_store import Neo4jGraphStore
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
```

**验证:** `uv run pytest tests/unit/test_butler_handlers.py -v`

---

### Task 0-2: 实现 KnowledgeUpdateHandler

**Objective:** 处理代码变更事件，调用 IncrementalUpdater 执行增量知识更新。

**Files:**
- Create: `src/layerkg/butler/handlers/knowledge_update.py`
- Test: `tests/unit/test_butler_handlers.py` (追加测试)

**核心逻辑:**
```python
# 监听事件类型: "code.changed"
# 接收 payload: {"since": "HEAD~3", "repo_path": "/path/to/repo", "full_scan": false}
# 调用 IncrementalUpdater.update(since=...)
# 通过 ConsistencyGuard 记录更新操作
# 返回 UpdateReport
```

关键实现点:
1. 从 `ButlerEvent.payload` 提取 `since`, `repo_path`, `full_scan`
2. 创建 `IncrementalUpdater(config, repo_path)` 实例
3. 用 `asyncio.to_thread()` 包裹同步的 `updater.update(since=...)` 调用
4. 调用 `ctx.guard.log_operation()` 记录审计日志
5. 成功时返回 `HandlerResult(success=True, data=report.to_dict())`
6. 异常时返回 `HandlerResult(success=False, error=str(e))`
7. **错误传播**: 无论成功/失败，都通过 guard 审计日志记录

**验证:** `uv run pytest tests/unit/test_butler_handlers.py -v`

---

### Task 0-3: 实现 KnowledgeUpdateHandler 的全量构建 Handler

**Objective:** 处理全量构建事件，调用 Builder 执行全量知识图谱构建。

**Files:**
- Modify: `src/layerkg/butler/handlers/knowledge_update.py` (追加 `FullBuildHandler`)
- Test: `tests/unit/test_butler_handlers.py` (追加测试)

**核心逻辑:**
```python
class FullBuildHandler(BaseHandler):
    """处理全量构建事件。"""
    handler_id = "knowledge.full_build"
    event_types = ["build.full"]
    # payload: {"repo_path": "/path/to/repo"}
    # 调用 Builder 全量构建
```

**验证:** `uv run pytest tests/unit/test_butler_handlers.py -v`

**Commit:** `git add -A && git commit -m "feat(butler): Day 0 - KnowledgeUpdateHandler + FullBuildHandler"`

---

## Day 1: ReflectionHandler + ButlerEngine 骨架

### Task 1-1: 实现 ReflectionHandler

**Objective:** 分析重复事件模式，沉淀为技能规则存入 SkillStore。

**Files:**
- Create: `src/layerkg/butler/handlers/reflection.py`
- Test: `tests/unit/test_butler_reflection.py`

**核心逻辑:**
```python
class ReflectionHandler(BaseHandler):
    """反思归纳 Handler — 从重复模式中沉淀技能。"""
    handler_id = "butler.reflection"
    event_types = ["handler.completed"]  # 监听其他 Handler 的完成事件

    async def handle(self, event, ctx):
        # 1. 从事件中提取 pattern signature（精确匹配算法）
        # 2. 查询 SkillStore.search_by_pattern("signature", sig) 检查已有技能
        # 3. 如果未找到: 创建 candidate 技能（confidence=0.5）
        # 4. 如果找到: increment_hit_count，提升 confidence
        # 5. confidence >= 0.8 → status 升级为 "active"
```

**Pattern 匹配算法（精确匹配，不用相似度）:**
- Pattern signature = `f"{event.payload['original_event_type']}:{event.payload.get('file_extension', 'unknown')}"`
- 示例: `"code.changed:.py"`, `"build.full:.java"`
- 新 pattern → 创建 `SkillEntity(layer=RULE, status="candidate", confidence=0.5)`
- 已有 pattern + hit_count >= 3 → confidence = min(0.5 + hit_count * 0.1, 1.0)
- 已有 pattern + confidence >= 0.8 → status 升级为 "active"
- 阈值通过 `LayerKGConfig` 可配置（默认 candidate_confidence=0.5, active_confidence=0.8, promote_hits=3）

**handler.completed 事件 payload 规范:**
```python
{
    "original_event_type": "code.changed",     # 原始事件类型
    "handler_id": "knowledge.update",           # 处理的 Handler ID
    "success": True,                            # 是否成功
    "file_extension": ".py",                    # 涉及的文件后缀（从原始事件提取）
    "duration_ms": 123.4,                       # Handler 执行耗时
}
```

**handler.failed 事件 payload 规范:**
```python
{
    "original_event_type": "code.changed",
    "handler_id": "knowledge.update",
    "success": False,
    "error": "Connection refused",              # 错误信息
    "attempts": 2,                              # 重试次数
}
```

**ReflectionHandler 只监听 handler.completed（成功事件），不监听 handler.failed。**

**验证:** `uv run pytest tests/unit/test_butler_reflection.py -v`

---

### Task 1-2: 创建 ButlerEngine 骨架

**Objective:** 创建 ButlerEngine 主类，编排所有组件。

**Files:**
- Create: `src/layerkg/butler/engine.py`
- Test: `tests/unit/test_butler_engine.py`

**核心接口:**
```python
class ButlerEngine:
    """Butler 引擎 — 事件驱动的知识管理主循环。"""

    def __init__(self, config: LayerKGConfig):
        self._config = config
        self._bus = EventBus()
        self._scheduler = Scheduler()
        self._guard: ConsistencyGuard | None = None
        self._skill_store: SkillStore | None = None
        self._handlers: dict[str, BaseHandler] = {}
        self._ctx: HandlerContext | None = None
        self._running = False

    async def start(self) -> None:
        """启动 Butler：初始化组件 + 注册 Handler + 启动事件循环。"""

    async def stop(self) -> None:
        """停止 Butler：清理资源。"""

    async def submit_event(self, event: ButlerEvent) -> list[HandlerResult]:
        """提交事件到引擎，返回 Handler 执行结果。"""

    def register_handler(self, handler: BaseHandler) -> None:
        """注册一个 Handler。"""
```

start() 实现:
1. 创建 ConsistencyGuard（SQLite 路径: `config.data_dir / "butler_audit.db"`）
2. 创建 SkillStore（SQLite 路径: `config.data_dir / "butler_skills.db"`）
3. 创建 HandlerContext（注入 config + guard + skill_store）
4. 对每个已注册的 Handler，创建对应的 HandlerSpec 并注册到 Scheduler
5. 对每个 Handler 订阅的事件类型，通过 EventBus.subscribe 注册分发回调
6. 设置 `_running = True`

**验证:** `uv run pytest tests/unit/test_butler_engine.py -v`

**Commit:** `git add -A && git commit -m "feat(butler): Day 1 - ReflectionHandler + ButlerEngine skeleton"`

---

## Day 2: ButlerEngine 事件分发 + Git Watcher

### Task 2-1: 实现事件分发闭环

**Objective:** 将 EventBus → Scheduler → Handler → ConsistencyGuard 串联起来。

**Files:**
- Modify: `src/layerkg/butler/engine.py`
- Test: `tests/unit/test_butler_engine.py` (追加测试)

**核心逻辑:**
1. EventBus.subscribe 注册一个分发回调 `_dispatch_event(event)`
2. `_dispatch_event` 调用 `scheduler.dispatch(event)`
3. 对每个 HandlerResult:
   - 如果成功，发布 `handler.completed` 事件（给 ReflectionHandler 用）
   - 如果失败，发布 `handler.failed` 事件
4. 审计日志记录分发结果

关键: `_dispatch_event` 是 EventBus 的回调函数，它把事件转发给 Scheduler。Scheduler 再调用已注册的 Handler 的 `handler_fn`。`handler_fn` 是一个包装器，调用 `handler.handle(event, ctx)`。

```python
def _make_handler_fn(self, handler: BaseHandler):
    """包装 handler.handle 为 Scheduler 需要的 async callback。"""
    async def fn(event: ButlerEvent) -> HandlerResult:
        result = await handler.handle(event, self._ctx)
        return result  # 已经是 HandlerResult
    return fn
```

**事件分发后的后处理（在 _dispatch_event 中）:**
```python
async def _dispatch_event(self, event: ButlerEvent) -> None:
    results = await self._scheduler.dispatch(event)
    for result in results:
        # 无论成功/失败都发布通知事件
        if result.success:
            completion_event = ButlerEvent(
                event_type="handler.completed",
                payload={
                    "original_event_type": event.event_type,
                    "handler_id": result.handler_id,
                    "success": True,
                    "file_extension": self._extract_file_extension(event),
                    "duration_ms": 0,  # 可扩展
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
        await self._bus.publish(completion_event)
```

**验证:** `uv run pytest tests/unit/test_butler_engine.py -v`

---

### Task 2-2: 实现 GitWatcher 事件源

**Objective:** 定时检测 Git 变更，自动发布 `code.changed` 事件到 EventBus。

**Files:**
- Create: `src/layerkg/butler/watchers/git_watcher.py`
- Create: `src/layerkg/butler/watchers/__init__.py`
- Test: `tests/unit/test_butler_git_watcher.py`

**核心接口:**
```python
class GitWatcher:
    """Git 仓库变更监视器。"""

    def __init__(self, repo_path: Path, bus: EventBus, poll_interval: float = 30.0, initial_scan: bool = False):
        self._repo_path = repo_path
        self._bus = bus
        self._poll_interval = poll_interval
        self._initial_scan = initial_scan
        self._last_ref: str | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动定时轮询。如果 initial_scan=True，首次轮询时会发布事件。"""

    async def stop(self) -> None:
        """停止轮询。"""

    async def _poll(self) -> None:
        """一次轮询：获取当前 HEAD → 比较 → 发布事件。"""

    def trigger(self, since: str | None = None) -> None:
        """手动触发一次变更检测（用于测试和 CLI 调用）。"""
```

轮询逻辑:
1. 获取当前 HEAD commit hash（`git rev-parse HEAD`）
2. 如果 `_last_ref` 为 None:
   - `initial_scan=True`: 发布 `code.changed` 事件，payload `{"since": "", "full_scan": True, "repo_path": ...}`
   - `initial_scan=False`（默认）: 只记录 `_last_ref = HEAD`，跳过
3. 如果 HEAD != `_last_ref`，发布 `code.changed` 事件，payload 包含 `{"since": _last_ref}`
4. 更新 `_last_ref = HEAD`

**验证:** `uv run pytest tests/unit/test_butler_git_watcher.py -v`

**Commit:** `git add -A && git commit -m "feat(butler): Day 2 - event dispatch loop + GitWatcher"`

---

## Day 3: CLI 集成 + Engine 生命周期

### Task 3-1: 添加 `butler` CLI 命令

**Objective:** 在 CLI 中添加 `layerkg butler` 命令组，支持启动引擎和手动触发。

**Files:**
- Modify: `src/layerkg/cli.py`
- Test: `tests/unit/test_butler_cli.py`

**命令设计:**
```bash
# 启动 Butler Engine（长期运行，监控仓库变更）
layerkg butler serve --repo ./my-repo --poll-interval 30

# 手动触发一次增量更新
layerkg butler update --repo ./my-repo --since HEAD~3

# 手动触发全量构建
layerkg butler build --repo ./my-repo

# 查看引擎状态
layerkg butler status
```

**验证:** `uv run pytest tests/unit/test_butler_cli.py -v`

---

### Task 3-2: 完善 Engine 生命周期管理

**Objective:** 添加优雅停止、资源清理、状态查询。

**Files:**
- Modify: `src/layerkg/butler/engine.py`
- Modify: `src/layerkg/butler/__init__.py` (导出新组件)
- Test: `tests/unit/test_butler_engine.py` (追加测试)

**新增方法:**
```python
async def status(self) -> dict:
    """返回引擎状态摘要。"""
    return {
        "running": self._running,
        "handlers": {hid: h.event_types for hid, h in self._handlers.items()},
        "scheduler_status": {hid: {"total": s.total_invocations, "success": s.success_count, "failure": s.failure_count} for hid, s in self._scheduler.get_status().items()},
        "skill_counts": await self._skill_store.count_by_layer() if self._skill_store else {},
    }
```

stop() 实现（严格按顺序，防止事件丢失）:
1. 设置 `_running = False`
2. 停止 GitWatcher（取消轮询 task，等待完成）
3. 等待 EventBus 队列排空（`await asyncio.sleep(0.1)`，给 in-flight 事件处理时间）
4. 取消所有 EventBus 订阅（`unsubscribe` 所有 subscription_id）
5. 关闭 HandlerContext 中的 GraphStore 连接（如果有）
6. 清理审计日志和技能数据库连接

**验证:** `uv run pytest tests/unit/test_butler_engine.py -v`

**Commit:** `git add -A && git commit -m "feat(butler): Day 3 - CLI integration + engine lifecycle"`

---

## Day 4: 集成测试 + 边界测试

### Task 4-1: Handler 集成测试

**Objective:** 测试完整的事件流：事件发布 → Handler 执行 → 审计日志 → 技能沉淀。

**Files:**
- Modify: `tests/unit/test_butler_integration.py` (追加测试)
- Test: `tests/unit/test_butler_integration.py -v`

**测试场景:**
1. 发布 `code.changed` 事件 → KnowledgeUpdateHandler 被触发 → 审计日志记录
2. 发布 `build.full` 事件 → FullBuildHandler 被触发
3. 连续发布 3 次 `code.changed` 事件（同一 pattern） → ReflectionHandler 沉淀技能
4. 发布不存在 handler 的事件 → 无 handler 响应，不报错

---

### Task 4-2: Engine 启停 + GitWatcher 集成测试

**Objective:** 测试引擎生命周期和 GitWatcher 触发。

**Files:**
- Modify: `tests/unit/test_butler_integration.py` (追加测试)

**测试场景:**
1. Engine.start() → status() 返回 running=True
2. Engine.stop() → status() 返回 running=False，资源清理
3. GitWatcher.trigger() → EventBus 收到 `code.changed` 事件
4. async context manager (`async with engine:`) 自动 start/stop

---

### Task 4-3: 边界/异常测试

**Objective:** 覆盖异常路径。

**Files:**
- Create: `tests/unit/test_butler_boundary.py`

**测试场景:**
1. Handler 抛异常 → Scheduler 重试 → 审计日志记录失败
2. SkillStore 写入 confidence 越界 → ValueError
3. EventBus 在无 handler 时发布事件 → 不报错
4. Engine 重复 start() → 幂等
5. Engine 未 start 就 submit_event → 返回空结果
6. GitWatcher repo_path 不存在 → 优雅处理

**Commit:** `git add -A && git commit -m "test(butler): Day 4 - integration + boundary tests"`

---

## Day 5: 反思 + 质量门禁

### Task 5-1: 代码审查 + ruff

**Objective:** 自审所有新增代码，修复 lint 问题。

**Steps:**
1. `uv run ruff check src/layerkg/butler/ tests/unit/test_butler_* --fix`
2. `uv run ruff format src/layerkg/butler/ tests/unit/test_butler_*`
3. `uv run pytest tests/ -v` — 确保全部 950+ tests 通过
4. 审查所有新增文件的 docstring、类型注解、import

### Task 5-2: 提交 + 推送 + 更新思源笔记

**Steps:**
1. `git log --oneline -5` 确认 commit 历史
2. `git push origin main`
3. 更新思源笔记 "LayerKG 开发进展记录" 文档（Phase 5 sub-stage 2 完成记录）

---

## 风险表

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| IncrementalUpdater 是同步 API，ButlerEngine 是 async | 高 | 中 | KnowledgeUpdateHandler 中用 `asyncio.to_thread()` 包裹同步调用 |
| EventBus subscribe 在 `__init__` 时创建 asyncio.Task | 中 | 低 | 只在 `engine.start()` 中订阅，不在 Handler 构造函数中 |
| GitWatcher 需要 repo_path 是合法 Git 仓库 | 低 | 低 | `_poll()` 中 try/except，异常时跳过本次轮询 |
| Handler 测试需要 mock IncrementalUpdater/Builder | 中 | 低 | 在 conftest 中提供 fixture，mock 掉 Neo4j/ChromaDB 依赖 |

## 依赖关系

```
Day 0 (Handlers) → Day 1 (Engine + Reflection) → Day 2 (Dispatch + Watcher) → Day 3 (CLI) → Day 4 (Tests) → Day 5 (Review)
```

Day 0 和 Day 1 可以独立开发。Day 2 依赖 Day 1 的 Engine 骨架。Day 3 依赖 Day 2 的事件分发。Day 4 是全量验证。
