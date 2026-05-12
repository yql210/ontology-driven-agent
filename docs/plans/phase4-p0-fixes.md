# Phase 4 P0 修复方案（v2 — 审核修订版）

## 审核记录
- v1: 7.2/10（同步 I/O 阻塞、写入时机、缺重置机制）
- v2: 修订以下阻塞问题

## 修订内容

### 修订 1：P0-1 使用 SQLite 替代 JSONL

**审核意见**：JSONL 同步 I/O 阻塞事件循环，多进程不安全。
**修订**：改用 SQLite（stdlib `sqlite3`，无需额外依赖）。

```python
import sqlite3
from pathlib import Path

class TraceCollector:
    def __init__(self, max_traces: int = 500, max_age_seconds: int = 3600, persist_path: str = ".traces.db"):
        self._persist_path = Path(persist_path)
        self._traces: dict[str, TraceLog] = {}  # 内存仍为主存储
        self._lock = asyncio.Lock()
        self._step_counters: dict[str, int] = {}
        self._max_traces = max_traces
        self._max_age_seconds = max_age_seconds
        
        # SQLite 持久化 — 初始化建表
        self._init_db()
        self._load_from_db()
    
    def _get_db(self) -> sqlite3.Connection:
        """每次获取新连接（线程安全，asyncio.to_thread 可能在不同线程）"""
        db = sqlite3.connect(str(self._persist_path))
        db.execute("PRAGMA journal_mode=WAL")
        return db
    
    def _init_db(self):
        """初始化建表"""
        db = self._get_db()
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    thread_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
            """)
            db.commit()
        finally:
            db.close()

    def _load_from_db(self):
        """启动时从 SQLite 加载历史 traces"""
        try:
            db = self._get_db()
            try:
                rows = db.execute("SELECT thread_id, data FROM traces").fetchall()
            finally:
                db.close()
            for thread_id, data_json in rows:
                data = json.loads(data_json)
                log = self._dict_to_trace(data)
                if log:
                    self._traces[thread_id] = log
                    self._step_counters[thread_id] = len(log.steps)
        except Exception:
            pass

    async def _save_trace(self, log: TraceLog):
        """异步保存 trace 到 SQLite（通过 to_thread 避免阻塞）"""
        data = json.dumps(self._trace_to_dict(log), ensure_ascii=False)
        def _write():
            db = self._get_db()
            try:
                db.execute("INSERT OR REPLACE INTO traces (thread_id, data) VALUES (?, ?)", (log.thread_id, data))
                db.commit()
            finally:
                db.close()
        await asyncio.to_thread(_write)

    async def _delete_trace_persisted(self, thread_id: str):
        """异步删除 trace"""
        def _delete():
            db = self._get_db()
            try:
                db.execute("DELETE FROM traces WHERE thread_id = ?", (thread_id,))
                db.commit()
            finally:
                db.close()
        await asyncio.to_thread(_delete)

    async def _rewrite_all_traces(self):
        """全量重写（清理后）"""
        pairs = [
            (tid, json.dumps(self._trace_to_dict(log), ensure_ascii=False))
            for tid, log in self._traces.items()
        ]
        def _bulk_write():
            db = self._get_db()
            try:
                db.execute("DELETE FROM traces")
                db.executemany("INSERT INTO traces (thread_id, data) VALUES (?, ?)", pairs)
                db.commit()
            finally:
                db.close()
        await asyncio.to_thread(_bulk_write)
```

**关键变更**：
- SQLite + WAL 模式：原生多进程安全、索引查询
- `asyncio.to_thread()` 包装所有同步 sqlite3 调用，不阻塞事件循环
- 内存仍为主存储，SQLite 为持久化备份（读快写少）
- `end_trace` 时写入（完整数据），`start_trace` 时不写（此时无 steps）

**写入时机调整**：
- `start_trace`：只写内存，不写文件
- `end_trace`：写内存 + 写 SQLite（完整 trace）
- `add_step`：只写内存（步骤在 end_trace 时一起持久化）
- `delete_trace`：删内存 + 删 SQLite
- `_clean_old_traces`：清内存 + SQLite 全量重写

### 修订 2：P0-3 LLM 重置机制

**审核意见**：全局 `_llm` 无法重置，测试间会泄漏。
**修订**：

```python
# graph.py
_llm: ChatOpenAI | None = None

def _get_llm() -> ChatOpenAI:
    """获取全局 LLM 单例"""
    global _llm
    if _llm is None:
        _llm = _create_llm()
    return _llm

def _reset_llm() -> None:
    """重置全局 LLM（测试用）"""
    global _llm
    _llm = None

async def _agent_node(state: AgentState) -> dict[str, list[BaseMessage]]:
    llm = _get_llm()  # 使用单例
    llm_with_tools = llm.bind_tools(ALL_TOOLS)
    messages: list[BaseMessage] = [
        SystemMessage(content=AGENT_SYSTEM_PROMPT),
        *state["messages"],
    ]
    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}
```

**conftest.py 添加**：
```python
@pytest.fixture(autouse=True)
def _reset_singletons():
    """每个测试后重置全局单例"""
    yield
    from layerkg.agent.graph import _reset_llm
    _reset_llm()
```

**说明**：不采用 config hash 检测机制。原因：当前项目没有运行时切换 LLM 配置的需求，过度设计。如果未来需要，再加 config hash 即可。

### P0-2 方案不变（审核 9.0 分，可执行）

唯一调整：合并两个 except 块的错误消息逻辑。

```python
async def event_generator():
    trace_ended = False
    try:
        async with asyncio.timeout(120):
            async for event in run_query_stream(...):
                yield ServerSentEvent(...)
        # 正常结束 → yield done
        yield ServerSentEvent(
            data=json.dumps({"thread_id": thread_id}),
            event="done",
        )
    except TimeoutError:
        if collector:
            await collector.end_trace(thread_id, status="failed")
            trace_ended = True
        yield ServerSentEvent(
            data=json.dumps({"type": "error", "message": "Agent timeout"}),
            event="error",
        )
    except Exception as e:
        if collector:
            await collector.end_trace(thread_id, status="failed")
            trace_ended = True
        yield ServerSentEvent(
            data=json.dumps({"type": "error", "message": str(e)}),
            event="error",
        )
    finally:
        # 只做 trace 清理，不 yield 事件
        if collector and not trace_ended:
            try:
                trace = await collector.get_trace(thread_id)
                if trace and trace.status == "running":
                    await collector.end_trace(thread_id, status="completed")
            except Exception:
                pass
```

## 序列化实现

```python
@staticmethod
def _trace_to_dict(log: TraceLog) -> dict:
    return {
        "thread_id": log.thread_id,
        "query": log.query,
        "status": log.status,
        "created_at": log.created_at,
        "total_duration_ms": log.total_duration_ms,
        "steps": [
            {
                "step_id": s.step_id,
                "type": s.type,
                "content": s.content,
                "tool_name": s.tool_name,
                "tool_args": s.tool_args,
                "tool_result": s.tool_result,
                "duration_ms": s.duration_ms,
            }
            for s in log.steps
        ],
    }

@staticmethod
def _dict_to_trace(data: dict) -> TraceLog | None:
    try:
        log = TraceLog(
            thread_id=data["thread_id"],
            query=data["query"],
            status=data.get("status", "running"),
            created_at=data.get("created_at", time.time()),
            total_duration_ms=data.get("total_duration_ms"),
        )
        for s in data.get("steps", []):
            log.steps.append(TraceStep(
                step_id=s["step_id"],
                type=s["type"],
                content=s["content"],
                tool_name=s.get("tool_name"),
                tool_args=s.get("tool_args"),
                tool_result=s.get("tool_result"),
                duration_ms=s.get("duration_ms"),
            ))
        return log
    except (KeyError, TypeError):
        return None
```

## 测试计划（修订）

### P0-1 测试（6 个）
1. `test_trace_persist_on_end` — end_trace 后 SQLite 有记录
2. `test_trace_load_from_db` — 启动时从 SQLite 加载
3. `test_trace_db_corrupted` — 数据损坏时优雅忽略
4. `test_trace_clean_rewrites_db` — 清理过期 trace 后 SQLite 同步
5. `test_trace_delete_removes_from_db` — delete_trace 删 SQLite
6. `test_trace_start_not_persisted` — start_trace 不写 SQLite

### P0-2 测试（3 个）
1. `test_stream_done_after_normal` — 正常路径最后收到 done
2. `test_stream_error_no_done` — 异常路径无 done
3. `test_stream_timeout_no_done` — 超时路径无 done

### P0-3 测试（2 个）
1. `test_llm_singleton` — 多次 _get_llm() 返回同一实例
2. `test_llm_reset` — _reset_llm() 后创建新实例

## 不改什么

1. 不改 TraceCollector 内存结构（内存为主存储）
2. 不改 SSE 事件类型
3. 不改 Agent 工具/Prompt
4. 不改前端代码
5. 不引入 aiofiles 等新依赖（stdlib sqlite3 + asyncio.to_thread 足够）
6. 不做 LLM config hash 检测（当前无需求）
