# Phase 5 子阶段 1 设计方案：Butler 基础设施

> V2 — 修复 V1 审核中的 3 个 P0 + 8 个 P1 问题

## 问题背景

Phase 5 Butler 是一个常驻后台的"自进化知识引擎"。它需要：
- 监听 Git 变更、Agent 对话等事件
- 将事件路由到对应的 Handler
- 管理三层技能（Rule/Meta/Harness）的存储和检索
- 保证所有操作的审计日志和一致性

子阶段 1 构建 Butler 运行的 **四大基础设施**。

## 设计目标

1. **EventBus**：解耦事件生产者和消费者，支持同步/异步发布
2. **Scheduler**：将事件匹配到 Handler，控制并发和重试
3. **ConsistencyGuard**：所有 Butler 写操作的 append-only 日志 + 溯源
4. **SkillStore**：三层技能的 CRUD + 检索（SQLite 存储）

## 模块设计

### 1. EventBus (`src/layerkg/butler/event_bus.py`)

```python
@dataclass
class ButlerEvent:
    event_id: str          # UUID
    event_type: str        # "git_change" | "agent_trace" | "timer" | "manual"
    payload: dict          # 事件数据
    timestamp: str         # datetime.now(UTC).isoformat()
    source: str            # 事件来源标识

class _Subscription:
    subscription_id: str
    event_type: str        # 支持 "*" 通配
    callback: Callable[[ButlerEvent], Awaitable[None]]
    queue: asyncio.Queue[ButlerEvent]  # maxsize=1000，满时阻塞

class EventBus:
    def subscribe(event_type: str, callback: Callable) -> str
    def unsubscribe(subscription_id: str) -> None
    async def publish(event: ButlerEvent) -> None
    def publish_sync(event: ButlerEvent) -> None      # asyncio.run() 包装
    def get_queue_depth(subscription_id: str) -> int   # 监控用
```

**设计决策**：
- `asyncio.Queue(maxsize=1000)`，满时 `put()` 阻塞（不丢弃消息）
- 支持 `"*"` 通配符订阅所有事件类型
- `publish_sync` 内部用 `asyncio.run()` 包装，仅供测试用
- `get_queue_depth()` 用于监控积压

### 2. Scheduler (`src/layerkg/butler/scheduler.py`)

```python
@dataclass
class HandlerSpec:
    handler_id: str
    event_types: list[str]    # 订阅的事件类型
    handler_fn: Callable       # async (event) -> HandlerResult
    max_concurrency: int = 1
    retry_count: int = 2
    retry_delay: float = 5.0
    timeout: float | None = None  # Handler 执行超时（秒）

@dataclass
class HandlerResult:
    handler_id: str
    success: bool
    result_data: dict | None
    error: str | None
    attempts: int = 1         # 实际执行次数（含重试）

@dataclass
class HandlerStatus:
    handler_id: str
    total_invocations: int
    total_failures: int
    last_run_at: str | None   # ISO 8601
    last_error: str | None
    is_running: bool

class Scheduler:
    def __init__(self) -> None  # 不持有 EventBus 引用，避免循环依赖
    def register(spec: HandlerSpec) -> None
    async def dispatch(event: ButlerEvent) -> list[HandlerResult]
    def get_status() -> dict[str, HandlerStatus]
```

**设计决策**：
- **Scheduler 不持有 EventBus 引用**。外部通过 `event_bus.subscribe("*", scheduler.dispatch)` 连接两者
- 每个 Handler 用 `asyncio.Semaphore` 控制并发
- `asyncio.wait_for(handler_fn(event), timeout=spec.timeout)` 做超时控制
- HandlerResult 记录 `attempts`（含重试次数）
- `HandlerStatus` 明确返回结构

### 3. ConsistencyGuard (`src/layerkg/butler/consistency/guard.py`)

```python
@dataclass
class AuditEntry:
    entry_id: str         # UUID
    operation: str        # "skill_create" | "skill_update" | "event_handled" | ...
    target_type: str      # "skill" | "event_log" | "ontology"
    target_id: str        # 操作对象 ID
    before: str | None    # JSON snapshot
    after: str | None     # JSON snapshot
    operator: str         # "butler" | "user" | handler_id
    timestamp: str        # ISO 8601

class ConsistencyGuard:
    def __init__(self, db_path: str = ".butler_audit.db") -> None

    # 全部异步，保持一致性
    async def log_operation(self, op: str, target_type: str, target_id: str,
                            before: dict | None, after: dict | None,
                            operator: str = "butler") -> str
    async def query(self, target_type: str | None = None,
                    target_id: str | None = None,
                    since: str | None = None) -> list[AuditEntry]
    async def get_last_operation(self, target_type: str, target_id: str) -> AuditEntry | None
```

**SQLite 表结构**（`_init_db()` 中创建）：

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    entry_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    operator TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp);
```

**设计决策**：
- 采用 `_get_db()` 每次新建连接 + `PRAGMA journal_mode=WAL` 模式（与 TraceCollector 一致）
- 全部方法异步（通过 `asyncio.to_thread()` 包装同步 SQLite 操作）
- `before_json` / `after_json` 存为 TEXT，查询时 `json.loads()`
- 时间戳用 ISO 8601 字符串（与现有 CodeEntity 一致）
- 不提供 delete 接口（append-only）

### 4. SkillStore (`src/layerkg/butler/skills/store.py`)

```python
class SkillLayer(Enum):
    RULE = "rule"
    META = "meta"
    HARNESS = "harness"

@dataclass
class SkillEntity:
    skill_id: str           # UUID
    name: str               # 人类可读名称
    layer: SkillLayer       # 三层之一
    pattern: dict           # JSON: 触发模式描述
    action: dict            # JSON: 执行动作描述
    confidence: float       # 0.0 - 1.0
    source: str             # "inductor" | "meta_learner" | "user" | "harness"
    status: str             # "candidate" | "active" | "deprecated" | "rejected"
    hit_count: int          # 命中次数
    version: int            # 版本号
    parent_id: str | None   # 升级来源（rule→meta 关联）
    created_at: str         # ISO 8601
    updated_at: str         # ISO 8601

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")

class SkillStore:
    def __init__(self, db_path: str = ".butler_skills.db",
                 guard: ConsistencyGuard | None = None) -> None

    # CRUD（全部异步）
    async def create(self, skill: SkillEntity) -> str
    async def update(self, skill_id: str, **fields) -> bool
    async def get(self, skill_id: str) -> SkillEntity | None
    async def delete(self, skill_id: str) -> bool          # 软删除：status → deprecated

    # 查询（全部异步）
    async def list_by_layer(self, layer: SkillLayer, status: str = "active") -> list[SkillEntity]
    async def search_by_pattern(self, pattern_key: str, pattern_value: str) -> list[SkillEntity]
    async def get_candidates(self, min_confidence: float = 0.5) -> list[SkillEntity]

    # 统计
    async def count_by_layer(self) -> dict[SkillLayer, int]
    async def increment_hit_count(self, skill_id: str) -> None
```

**SQLite 表结构**（`_init_db()` 中创建）：

```sql
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    layer TEXT NOT NULL,
    pattern_json TEXT NOT NULL,
    action_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    hit_count INTEGER DEFAULT 0,
    version INTEGER DEFAULT 1,
    parent_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (confidence >= 0.0 AND confidence <= 1.0)
);
CREATE INDEX IF NOT EXISTS idx_skills_layer_status ON skills(layer, status);
CREATE INDEX IF NOT EXISTS idx_skills_status_conf ON skills(status, confidence);
CREATE INDEX IF NOT EXISTS idx_skills_parent ON skills(parent_id);
```

**设计决策**：
- 独立 SQLite 文件（`.butler_skills.db`）
- 采用 `_get_db()` + WAL 模式（与 TraceCollector 一致）
- 全部方法异步（`asyncio.to_thread()` 包装）
- `search_by_pattern` 实现：`json_extract(pattern_json, '$.' || pattern_key) = pattern_value`
- `delete` 是软删除（status → deprecated）
- `__post_init__` 校验 confidence 范围
- 所有写操作通过 ConsistencyGuard 记录审计日志

## 模块依赖关系（无循环依赖）

```
EventBus         → (无外部依赖)
Scheduler        → EventBus (仅类型引用 ButlerEvent，通过外部注册解耦)
ConsistencyGuard → (无外部依赖)
SkillStore       → ConsistencyGuard
```

外部注册模式（`butler/engine.py` 未来实现）：
```python
event_bus = EventBus()
scheduler = Scheduler()
guard = ConsistencyGuard()
skill_store = SkillStore(guard=guard)

# 外部连接：EventBus → Scheduler
event_bus.subscribe("*", scheduler.dispatch)
```

## 新增文件清单

```
src/layerkg/butler/
├── __init__.py
├── event_bus.py              # EventBus + ButlerEvent
├── scheduler.py              # Scheduler + HandlerSpec + HandlerResult + HandlerStatus
├── consistency/
│   ├── __init__.py
│   └── guard.py              # ConsistencyGuard + AuditEntry
└── skills/
    ├── __init__.py
    └── store.py              # SkillStore + SkillEntity + SkillLayer

tests/unit/
├── test_butler_event_bus.py
├── test_butler_scheduler.py
├── test_butler_consistency_guard.py
└── test_butler_skill_store.py
```

## 不做的事

- ❌ 不实现具体 Handler（子阶段 2-5 的事）
- ❌ 不实现 SkillInductor / MetaLearner（子阶段 3 的事）
- ❌ 不实现 Reflector / Verifier（子阶段 4 的事）
- ❌ 不实现 OntologyHook（子阶段 5 的事）
- ❌ 不实现 Butler Engine 主循环（子阶段 2 集成 Handler 时一起做）
- ❌ 不加 Web UI 或 API 端点
- ❌ 不引入新依赖（只用 stdlib：asyncio, sqlite3, json, uuid, dataclasses, datetime）

## 与现有代码的集成点

| 模块 | 关系 |
|------|------|
| `TraceCollector` | 子阶段 3 的 SkillInductor 会从中读取 TraceLog，本阶段无直接依赖 |
| `IncrementalUpdater` | 子阶段 2 的 knowledge_update handler 会调用，本阶段无直接依赖 |
| `Neo4jStore` | 子阶段 3 的 Meta 技能可能存 Neo4j，本阶段只存 SQLite |
| `LayerKGConfig` | 本阶段不需要新配置项，各模块用默认路径 |

## 验证标准

1. 所有 4 个模块的单元测试通过（预计 ~35 个测试）
2. `ruff check` + `ruff format` 通过
3. 模块间集成验证：EventBus publish → Scheduler dispatch → Handler 执行 → Guard 审计记录
4. SkillStore CRUD + 查询全部正确
5. 现有 910 测试不回归

## V1→V2 修改清单

| # | 级别 | 问题 | 修复 |
|---|------|------|------|
| 1 | P0 | EventBus 队列满时丢弃消息 | 改为 `Queue(maxsize=1000)` 满时阻塞 |
| 2 | P0 | ConsistencyGuard 缺表结构 | 补充完整 CREATE TABLE + 索引 |
| 3 | P0 | SkillStore 缺表结构 | 补充完整 CREATE TABLE + 索引 + CHECK |
| 4 | P1 | Guard 查询同步/写入异步不一致 | 全部改为异步 |
| 5 | P1 | 缺 `_get_db()` + WAL 模式 | Guard 和 SkillStore 都采用 |
| 6 | P1 | HandlerResult 缺 attempts | 添加 `attempts: int` |
| 7 | P1 | SkillEntity.created_at 用 float | 改为 ISO 8601 字符串 |
| 8 | P1 | Scheduler.get_status 返回不明确 | 定义 HandlerStatus dataclass |
| 9 | P1 | EventBus-Scheduler 循环依赖 | Scheduler 不持有 EventBus 引用，外部注册 |
| 10 | P1 | SkillStore.search_by_pattern 实现不明 | 用 `json_extract()` |
| 11 | P1 | increment_hit_count 同步 | 改为异步 |
| 12 | P2 | 缺 confidence 校验 | `__post_init__` 添加范围检查 |
| 13 | P2 | Handler 无超时控制 | HandlerSpec 添加 timeout 参数 |
