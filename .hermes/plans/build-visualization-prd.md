# OntoAgent 构建过程可视化实施计划（阶段进度 + 日志）

> 日期：2026-08-12 · 类型：体验优化（非 bug 修复）
> 流程：独立审查结论（2026-08-12，5 项 ISSUE 已纳入）→ M1/M2/M3 逐里程碑 TDD 实施 → 每里程碑验证、commit 与 push

## 独立审查结论（2026-08-12）

- **ISSUE 1（已采纳，硬性约束）**：`_set_status` 有 4 处整体替换对象（build.py:124/133/154/166）。必须**全部改为原地更新同一 `current` 对象**（保留引用），否则回调闭包持有的引用过期、success/failed 时 logs 丢失。
- **ISSUE 2（已采纳）**：`ontoagent.pipeline.builder` 没有子 logger——`semantic_linker`/`module_clustering` 与 builder 是**兄弟**（均属 `ontoagent.pipeline` 子级）。日志 handler 应挂 **`ontoagent.pipeline`**（构建任务中无其它流水线日志，不会污染），加 levelno >= INFO filter；deque 快照加 `threading.Lock`；**detach 必须放 `finally`**（build 抛异常时 handler 不泄漏）。
- **ISSUE 3（已采纳，硬性约束）**：progress_callback 闭包**直接捕获 `current` 对象引用**，全流程原地改字段（字段赋值 GIL 原子，无数据竞争；真正坑是整体替换对象使引用过期）。
- **ISSUE 4（确认可接受）**：前端失败视图随 `stopPolling` 5s 后 `activeBuilds` 删除而消失——与现有行为一致，接受。
- **ISSUE 5（已采纳，测试）**：T2 回调放 skip 分支**之外**（Stage complete 日志后）；测试 patch `check_llm_available` 返回 False 避免 5s Ollama 探测。T3 logs 断言改为"logs 非空"或 mock build 先 emit builder logger 错误再 raise。
- **ISSUE 6（已采纳）**：stage 标识与前端进度条映射需两端同步——在 builder.py 与 RepoView.vue 加注释"stage 取值必须一致"。

## 实查结论（2026-08-12，对照 master @033bc5a）

| PRD 描述 | 实际现状 | 差异 |
|---------|---------|------|
| `BuildStatusResponse`（build.py:68）无 stage/progress/logs | ✅ 一致：task_id/status/repo_id/message/result | 无 |
| `builder.build()` 各阶段仅 logger.info | ✅ 一致：Stage 1/5→5/5 均有日志点 | 无 |
| 前端 `types.ts` | ⚠️ 实际在 **`frontend/src/api/types.ts`** | 路径修正 |
| 前端 `RepoView.vue` 轮询仅展示 status | ✅ 一致：`POLL_INTERVAL_MS=3000`, `MAX_POLLS=100` | 无 |
| 不做 SSE（轮询已够） | ⚠️ 代码**已有** SSE endpoint `/api/build/stream/{task_id}`（commit a748a8b） | 已存在，本 PR 不扩展它 |

**结论**：方案总体可行。按 PRD 用轮询（改动最小），SSE 已存在但不纳入本次范围。

---

## M1：后端 — BuildStatusResponse 扩展 + builder 进度回调 + 日志捕获

### T1. `BuildStatusResponse` 扩展（build.py）

**现状**（build.py:68-75）：
```python
class BuildStatusResponse(BaseModel):
    task_id: str
    status: str  # pending/cloning/building/success/failed
    repo_id: str
    message: str = ""
    result: dict | None = None
```

**修复要求**：按 PRD 增加字段（保持向后兼容——新字段默认值，旧客户端不受影响）：
```python
    stage: str = ""            # 当前阶段标识: prebuild/parse/structural_write/doc_link/semantic/clustering/vector_index
    stage_detail: str = ""     # 阶段描述: "Parsed 17069 entities, Resolved 19386 relations"
    logs: list[str] = []       # 最近 N 行构建日志（滚动覆盖）
```

**测试要求**（tests/unit/web/test_build_router.py 扩展）：
- 构造 BuildStatusResponse 带新字段 → 序列化含 stage/stage_detail/logs
- 旧字段仍存在（向后兼容）；默认值空字符串/空列表

### T2. `builder.build()` 进度回调（builder.py）

**现状**：`build()` 签名（builder.py:478 附近）：
```python
    def build(
        self,
        repo_path: Path,
        *,
        repo_id: str = "default",
        skip_semantic: bool = False,
        skip_clustering: bool = False,
        clear: bool = False,
    ) -> BuildResult:
```

**修复要求**：
1. 增加可选参数：`progress_callback: Callable[[str, str], None] | None = None`（回调两个参数：stage 标识 + 描述文本）
2. 各阶段调用点（回调**不影响**现有逻辑，None 兜底；不改变现有日志；**stage 取值必须与前端 RepoView.vue 进度条映射一致**）：
   - pre-build 清库后：`progress_callback("prebuild", f"Cleared {cleared} existing nodes")`（仅 clear 时）
   - Stage 1 解析后：`progress_callback("parse", f"Parsed {len(all_entities)} entities, Resolved {len(relations)} relations")`
   - Stage 2 结构写入后：`progress_callback("structural_write", f"Wrote {len(relations)} relations")`
   - Stage 2.5 doc-code link 后：`progress_callback("doc_link", f"Linked {len(describes_rels)} DESCRIBES relations")`
   - Stage 3 语义提取后：`progress_callback("semantic", f"Extracted {concepts_created} concepts, {semantic_rels_created} relations")`（**在 skip 分支之外**，即 Stage complete 日志之后）
   - Stage 4 聚类后：`progress_callback("clustering", f"Clustered {clusters_count} modules")`（**在 skip 分支之外**）
   - Stage 5 向量写入后：`progress_callback("vector_index", "Vector index complete")`
3. 回调放在每个 stage 完成**之后**（非开始前），保证 stage_detail 有准确计数
4. 回调本身抛异常时**不得**中断 build——包 try/except 记 warning（防御：回调是外部注入的）

**测试要求**（tests/unit/pipeline/test_builder.py 扩展）：
- 传 progress_callback（spy 收集调用）→ build 完成后断言按顺序收到 parse → structural_write → ... → vector_index，且 stage_detail 含计数；**patch `check_llm_available` 返回 False** 避免 5s Ollama 探测
- 回调抛异常 → build 不中断，BuildResult 成功
- 不传回调 → 行为不变（现有测试全部通过）

### T3. 路由层接线 + 日志捕获（build.py `_run_build`）

**现状**（build.py:145-152）：
```python
        result = await asyncio.to_thread(
            builder.build,
            repo_path,
            repo_id=repo_id,
            skip_semantic=skip_semantic,
            skip_clustering=skip_clustering,
            clear=clear,
        )
```

**修复要求**：
1. 定义线程安全的日志捕获 handler：`logging.Handler` 子类，`emit()` 将 `record.getMessage()` 追加到 `collections.deque(maxlen=50)`（**加 `threading.Lock` 保护快照**），并做 `levelno >= logging.INFO` 过滤（避免 DEBUG 噪音）——handler 挂到 **`ontoagent.pipeline`** logger（builder 与 semantic_linker/module_clustering 是兄弟 logger，均属此层级；构建任务中无其它流水线日志，不会污染）
2. `_run_build` 内：
   - 构建开始前创建 deque + handler，attach 到 `ontoagent.pipeline` logger
   - **硬性约束：progress_callback 闭包直接捕获 `current` 对象引用**（build_tasks 中已存在的 BuildStatusResponse），回调内**原地更新字段**（`current.stage = stage; current.stage_detail = detail`），不整体替换对象
   - **硬性约束：`_set_status` 的 4 处调用（cloning/building/success/failed）全部改为原地更新同一 `current` 对象**——保留引用，不新建对象替换，否则回调闭包引用过期、logs 丢失
   - 传给 `builder.build(..., progress_callback=cb)`
   - **detach handler 放 `finally`**（build 抛异常时 handler 不泄漏）；成功后 logs 冻结在最终状态
3. 回调内更新 stage 时**不改变 status**（status 仍由主流程控制：building→success/failed）
4. 失败时：`message` 已有（`f"{type(e).__name__}: {e}"`），logs 保留最近 50 行（含错误抛出点上下文）
5. CLI build（`api/cli.py`）不传回调 → 行为不变

**测试要求**（tests/unit/web/test_build_router.py 扩展）：
- mock `builder.build` 的 progress_callback 调用（或真实构建小 repo）→ 轮询状态期间 stage 递进、logs 追加
- 构建失败 → status=failed + message 含错误 + **logs 非空**（mock build 先 emit builder logger 错误再 raise，或仅断言 logs 非空）

---

## M2：前端 — 构建卡片阶段/进度/日志渲染 + 失败视图

### T4. `types.ts` BuildStatus 扩展（frontend/src/api/types.ts）

**现状**：
```typescript
export interface BuildStatus {
  task_id: string
  status: string
  repo_id: string
  message?: string
  result?: Record<string, unknown> | null
}
```

**修复要求**：
```typescript
export interface BuildStatus {
  task_id: string
  status: string
  repo_id: string
  message?: string
  result?: Record<string, unknown> | null
  stage?: string
  stage_detail?: string
  logs?: string[]
}
```

### T5. RepoView.vue 构建卡片渲染（frontend/src/views/RepoView.vue）

**现状**：`startPolling` 只读 status/message；`ActiveBuild` 接口无 stage/logs；表格行内仅显示 badge + message。

**修复要求**：
1. `ActiveBuild` 接口增加 `stage: string`、`stageDetail: string`、`logs: string[]`
2. 轮询回调更新：`build.stage = s.stage ?? ''`、`build.stageDetail = s.stage_detail ?? ''`、`build.logs = s.logs ?? []`
3. 渲染（在表格行状态单元格内，building/cloning 时显示）：
   - **阶段进度条**：按固定 5 主阶段映射计算百分比（parse=20%, structural_write=40%, doc_link=60%, semantic=80%, clustering=90%, vector_index=100%；prebuild=5%；cloning/pending=0 或 5%），用 div + width 样式（保持项目现有 CSS 风格）
   - **阶段描述文本**：`stageDetail`（如 "Parsed 17069 entities, Resolved 19386 relations"）
   - **日志面板**：`logs` 滚动显示（max-height + overflow-y auto），building 时每 3s 轮询自然追加
4. **失败视图**：`status=failed` 时展示 `message` + `logs` 最后 5 行高亮（红色背景类）
5. 成功后（5s 后 activeBuilds 删除）恢复原状

**测试要求**：前端无 vitest/jest 测试框架（实查确认 package.json 无 test 脚本）→ 本任务验证靠 M3 联调 + `npm run build`（vue-tsc 类型检查通过即视为编译验证）。

---

## M3：联调验证

1. `uv run pytest tests/unit -q` 全量回归（M1 后端）
2. `cd frontend && npm run build`（vue-tsc + vite build，M2 前端类型与编译）
3. 本地起后端 + 前端，触发真实构建（小 repo）：
   - 轮询期间 stage 从 parse → structural_write → ... → vector_index 递进，logs 持续追加
   - 成功：status=success + 完整 result
   - 失败：status=failed + 可见错误（无需翻后端日志）
4. 回归：CLI `ontoagent build` 行为不变（不传回调）

## 里程碑与提交

| 里程碑 | 提交信息 |
|--------|---------|
| M1 | `feat(web): build progress visualization backend — stage/stage_detail/logs in status + builder progress_callback` |
| M2 | `feat(web): build progress visualization frontend — stage progress bar + live logs + failure view` |
| M3 | 联调验证（无代码提交，除非发现缺陷需修复） |
