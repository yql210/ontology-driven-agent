# OntoAgent 公共缺陷修复实施计划（Gitee 开源版）

> 日期：2026-08-12 · 范围：仅 5 项已实查确认的公共缺陷（P1-2/P2-3 经用户确认跳过——master 分支无对应代码）
> 流程：独立审查结论（2026-08-12，3 项 ISSUE 已纳入）→ M1/M2/M3 逐里程碑 TDD 实施 → 每里程碑验证、commit 与 push

## 独立审查结论（2026-08-12）

- **T1 ISSUE（已采纳）**：`raise_server_exceptions=False` 时 Starlette 返回 500 文本 `Internal Server Error`，body 不含真实错误。测试改断言：status==500、body 不含 `UnboundLocalError`、`record_http_request` 收到 status=500。`tests/unit/web/` 下无现有 app 测试，需**新建 `tests/unit/web/test_app.py`**。`record_http_request` 用嵌套 try/finally 包住，确保其自身抛错时 `reset_request_id` 仍执行。
- **T2 PASS**（次要：ls-remote 复用 `git_clone_timeout`；重试也失败时合并原始+重试 stderr）。
- **T3 PASS**（边界：tab 前导注释不剥离、带引号值 `KEY="a # b"` 会被误截——现有代码本无引号支持，测试注明不支持）。
- **T4 ISSUE（已采纳，次要）**：`self._get_graph_store()` 一并纳入 try，与 chroma 侧 `_get_chroma_store()` 在 try 内对齐。
- **T5 ISSUE（重点，已采纳）**：`type.split(",")`（graph.py L72）**无任何校验**，用户可控参数直接 `repr()` 拼接进 Cypher 有注入面。修正：先对 `allowed_types` 做**已知实体标签白名单校验**（不合法即忽略该值/400），再拼接字面量；`tests/unit/web/` 无 `test_graph_api.py`，需**新建**。
- **执行顺序 PASS**：M1/M2/M3 无隐藏依赖；M1 中间件改动影响全部 web 测试需全量回归；M2 需确认仓库 `.env` 无含 `" #"` 的值。

## 实查结论（2026-08-12，对照 Gitee master @b516815）

| 缺陷 | 文件 | 行号 | 实查确认 |
|------|------|------|---------|
| P1-1 中间件掩盖错误 | `src/ontoagent/api/web/app.py` | 124-139 | ✅ try/finally 中 `response` 异常路径未定义 |
| P1-3 git clone 分支无回退 | `src/ontoagent/service/git_clone.py` | clone() 签名 branch="main" | ✅ 硬编码 main，无探测/回退 |
| P2-1 .env 行内注释 | `src/ontoagent/config.py` | _load_dotenv L19 | ✅ 仅 strip 未剥注释 |
| P2-2 clear 失败中止 | `src/ontoagent/pipeline/builder.py` | 545-548 | ✅ graph clear 无 try/except（chroma 侧 550-555 有） |
| P3-1 nebula 兼容 | `src/ontoagent/api/web/router/graph.py` | 146, 176 | ✅ 硬编码 labels(n)[0] + $types list 参数 |

跳过项：P1-2（_create_llm 无 model_kwargs/extra_body，git 历史零命中）、P2-3（.env.example 无 Service 示例）。

---

## M1：P1-1 中间件错误掩盖 + P1-3 git clone 分支回退

### T1. P1-1 metrics_middleware 修复（app.py）

**现状**（app.py 117-139）：
```python
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid4().hex[:16]
    token = set_request_id(request_id)
    start_time = time.time()
    try:
        response = await call_next(request)
    finally:
        duration = time.time() - start_time
        route = request.scope.get("route")
        endpoint = route.path_format if route and hasattr(route, "path_format") else request.url.path
        record_http_request(method=request.method, endpoint=endpoint, status=response.status_code, duration=duration)
        response.headers["X-Request-ID"] = request_id
        reset_request_id(token)
    return response
```

**修复要求**：
1. `response = None` 预置；`status` 在 `response is None` 时记 500（`record_http_request(status=500)`）
2. `X-Request-ID` header 仅在 `response is not None` 时回传
3. 异常原样向上传播（不要在 finally 中吞掉或改写）
4. 保证 `reset_request_id(token)` 始终执行（防止日志上下文泄漏）

**测试要求**（**新建 tests/unit/web/test_app.py**）：
- 正常请求：status 200 记录、`X-Request-ID` 回传、request_id 上下文重置
- 注入抛错 endpoint（如通过临时路由抛 RuntimeError）：TestClient(raise_server_exceptions=False) → status==500、body **不含** `UnboundLocalError`、`record_http_request` 收到 status=500
- `record_http_request` 自身抛错时（mock 抛异常）`reset_request_id` 仍执行

### T2. P1-3 git clone 默认分支回退（git_clone.py）

**现状**：`clone()` 直接 `--branch main`，失败即 GitCloneError。

**修复要求**：
1. `subprocess.CalledProcessError` 且 stderr 匹配 `Remote branch .* not found`（大小写不敏感）时：
   - 先探测远端默认分支：`git ls-remote --symref <authed_url> HEAD`，解析 `ref: refs/heads/<branch> HEAD` 行
   - 若探测成功且分支 != 原请求分支，用探测到的分支重试 clone 一次
   - 探测/重试失败则保留原错误抛出（GitCloneError，含原始 stderr）
2. 探测分支为空/无法解析时直接抛原错误
3. 不改变对外签名（`branch: str = "main"` 保留）

**测试要求**（tests/unit/test_git_clone.py 新增）：
- mock subprocess.run：第一次 CalledProcessError(stderr="fatal: Remote branch main not found in upstream origin")，ls-remote 返回 "ref: refs/heads/master HEAD\n...", 第二次成功 → 断言第二次调用含 --branch master
- mock 两次都失败 → GitCloneError 保留原始 stderr
- ls-remote 输出无 ref 行 → 抛原错误，不重试

---

## M2：P2-1 .env 行内注释 + P2-2 clear 容错

### T3. P2-1 _load_dotenv 剥离行内注释（config.py）

**现状**（config.py L18-21）：
```python
key, _, value = line.partition("=")
key, value = key.strip(), value.strip()
```

**修复要求**：
1. value 按 `" #"`（空格+井号）分割取前段后 strip；兼容无空格 `#`（如 URL fragment）不受影响
   - 实现建议：`value = value.split(" #", 1)[0].strip()`
2. 不改变 `key` 处理与"已存在环境变量不覆盖"行为
3. 注释说明：仅剥离 ` # ` 带空格前缀的行内注释

**测试要求**（tests/unit/test_config.py 新增）：
- `KEY=value # comment` → value == "value"
- `KEY=value#fragment`（无空格）→ value == "value#fragment"
- `KEY= value ` → value == "value"（strip 行为不回归）
- 引号/井号在值中间（如 `KEY=ab#cd # 注释`）→ value == "ab#cd"

### T4. P2-2 builder clear 容错（builder.py）

**现状**（builder.py 545-548）：
```python
if clear:
    graph_store = self._get_graph_store()
    cleared = graph_store.clear_all()
    self._logger.info("═══ Pre-build: Cleared %d existing nodes ═══", cleared)
```

**修复要求**：
1. `graph_store.clear_all()` 及其 `self._get_graph_store()` 一起包 try/except Exception → logger.warning("Pre-build: graph clear failed, continuing incremental: %s", e)，不中止 build
2. 与 ChromaDB 侧（550-555）行为对齐（chroma 的 `_get_chroma_store()` 本就在 try 内）
3. 成功路径日志不变

**测试要求**（tests/unit/ 下 builder 相关测试文件）：
- mock graph_store.clear_all 抛异常 → build 继续执行（后续 stage 正常跑完，BuildResult 成功）
- mock clear_all 正常返回 → 原有行为不变

---

## M3：P3-1 graph API nebula 兼容

### T5. graph.py 全图模式 + get_node_detail 兼容（router/graph.py）

**现状**：
- L146 全图模式：`AND ($types = [] OR labels(n)[0] IN $types)` + `{"types": allowed_types}`（list 参数 → nebula TypeError）
- L176 get_node_detail：`labels(n)[0] AS label`
- 已有 `_label_expr()` / `_has_label_check()` 兼容封装（L21-38）

**修复要求**：
1. 全图模式：`labels(n)[0]` 替换为 `{label_fn}`（复用 L139 已计算的 `_label_expr(store, "n")`）
2. 类型筛选：**先对 `allowed_types` 做已知实体标签白名单校验**（仅允许 schema 中合法标签，如 CodeEntity/ConceptEntity/DocEntity/DataAsset 等；不合法值直接忽略，不拼接进查询），再以字面量 `IN [...]` 拼接（类型名经白名单校验后安全）；空类型列表时不加筛选条件（避免 `IN []` 语义歧义）
   - 实现建议：`VALID_LABELS` 取自 `schema.py` 实体标签集合；`allowed_types = [t for t in allowed_types if t in VALID_LABELS]`；`type_filter = "" if not allowed_types else f"AND {label_fn} IN [{', '.join(f'\"{t}\"' for t in allowed_types)}]"`，直接拼进 WHERE
3. get_node_detail：`labels(n)[0]` 替换为 `_label_expr(store, "n")`
4. neo4j 后端行为不变（字面量 `IN [...]` 在 Cypher 中合法）

**测试要求**（**新建 tests/unit/web/test_graph_api.py**）：
- neo4j（mock store 非 nebula）：全图模式含类型筛选 → 断言查询串包含 `labels(n)[0] IN ["CodeEntity"...]`（字面量），且无 `$types`
- nebula（mock NebulaGraphStore 类名）：全图模式 → 断言查询串用 `tags(n)[0]`，类型筛选为字面量；不触发 TypeError
- 注入非法类型名（如 `CodeEntity\" OR 1=1`）→ 被白名单过滤，不进入查询串
- get_node_detail：nebula → `tags(n)[0]`；neo4j → `labels(n)[0]`
- 现有 graph router 测试全部回归通过

---

## 验证方案（每里程碑）

1. `uv run ruff check src/ tests/ && uv run ruff format src/ tests/`
2. `uv run pytest tests/unit -v`（新增 + 全量回归）
3. M3 额外：`uv run pytest tests/ -q`（含 integration 中不依赖真实库的部分，或 `-m "not integration"`）

## 里程碑与提交

| 里程碑 | 提交信息 |
|--------|---------|
| M1 | `fix(web): preserve real errors in metrics middleware + git clone default branch fallback` |
| M2 | `fix(config): strip inline comments in .env parsing + builder clear_all fault tolerance` |
| M3 | `fix(web): nebula-compatible graph API queries (labels/tags + literal type filter)` |

每里程碑：实现 → 测试 → 实查验证（Hermes 跑 pytest + ruff）→ commit 与 push → 进入下一里程碑。
