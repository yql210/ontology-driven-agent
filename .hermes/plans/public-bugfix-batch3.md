# OntoAgent 公共缺陷修复 · 第三批实施计划

> 日期：2026-08-12 · 范围：4 项（U1/U2/U3/U4）
> 流程：独立审查结论（2026-08-12，4 项 ISSUE 已纳入）→ 逐项 TDD 实施 → 每项验证、commit 与 push

## 独立审查结论（2026-08-12）

- **U1 ISSUE（已采纳）**：不要每次调用 os.getenv——项目已有 `OntoAgentConfig.from_env` 统一管 env。改：config dataclass 加 `agent_recursion_limit: int = 30`，`from_env` try/except 取 int 非法回落 30 并 **clamp ≥ 10**（防配 0 死循环失控）。注释不写"约 N 轮"（recursion_limit 是 superstep 上限非严格轮数）。
- **U2 PASS + 2 补充（已采纳）**：merge_node 参数化，URL 特殊字符安全。补充① **url 判空**——`repo_url=""` 时**不覆盖**已有 url（仅新节点写入或非空才 SET），否则复用节点会把旧 url 清空；② 其他构造 RepositoryEntity 的调用方（repo.py:123 已传 url，天然一致）保持一致性。
- **U3 核心 ISSUE（已采纳）**：读-改-写竞态 + 空 url 覆盖。**关键实查发现**：`RepositoryEntity.id = _stable_id(name, url)` 已是确定性稳定哈希——builder 传 `url=""` 时 id=_stable_id(name,"")，repo.py 创建传真实 url 时 id=_stable_id(name,url)，**两入口 id 不一致才是重复累积根因**。修复：① U2 传 repo_url 后 builder 的 id 与 repo.py 一致（MERGE 幂等，天然去重）；② U3 仍做"按 name=repo_id 查询复用已有节点 id"兜底（防 url 为空/变化的旧数据累积）；③ 查询按 name 定向（name 已有索引/约束），不拉全表；④ 查询不带无用字段（status 不需要则不取）。
- **U4 ISSUE（已采纳）**：**不建议默认 `os._exit(0)`**（跳过 finally/atexit/缓冲刷新，可能丢未提交事务与日志）。改：① shutdown 资源逐个独立 try/except 关闭（一个失败不阻塞其余）；② 若担心挂死用 `asyncio.wait_for` 限时优雅关闭；③ nebula3 ConnectionPool 有 `close()`，但 app.state 未持有 pool 则无需处理（graph_store 内自管）。

## 实查结论（2026-08-12，对照 master @0dc3f8b）

| 缺陷 | 实查 | 性质 |
|------|------|------|
| U1 recursion_limit 15 过小 | graph.py:109 `recursion_limit: 15` | 修复（config 化默认 30） |
| U2 RepositoryEntity.url 空 | builder.py:623 `"url": ""` 硬编码；build() 无 repo_url 参数；_run_build 有 repo_url 变量可传 | 修复 + 参数贯通 |
| U3 每次新建 RepositoryEntity | **根因修正**：id=_stable_id(name,url) 是稳定哈希，但 builder 用 url="" vs repo.py 用真实 url → 两入口 id 不一致累积 | 修复（传 url + 按 name 复用兜底） |
| U4 退出卡住 | app.py:46-47 lifespan 仅 store.close() + acl.close() | 修复（shutdown 优雅清理） |

---

## M1：U1 recursion_limit 30（config 化）

**现状**（graph.py:105-110）：
```python
def _make_config(thread_id: str = "default") -> dict[str, Any]:
    """生成 Agent 运行配置"""
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 15,  # 防止死循环（agent→tools→agent 最多 7 轮）
    }
```

**修复（审查修正：config dataclass 化）**：
1. `config.py` dataclass 加字段：`agent_recursion_limit: int = 30`（agent 段，agent_llm_extra_body 附近）
2. `from_env` 解析 `ONTOAGENT_AGENT_RECURSION_LIMIT`：try/except 取 int，非法回落 30，**clamp 到 ≥10**（`max(10, value)`）
3. `graph.py::_make_config`：
```python
def _make_config(thread_id: str = "default") -> dict[str, Any]:
    """生成 Agent 运行配置"""
    from ontoagent.agent._helpers import get_config
    cfg = get_config()
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": cfg.agent_recursion_limit,  # 防止死循环（superstep 上限）
    }
```
4. 注释改"superstep 上限"，不写"约 N 轮"

**测试**（tests/unit/test_config.py + tests/unit/agent/）：
- config 默认 `agent_recursion_limit == 30`
- `ONTOAGENT_AGENT_RECURSION_LIMIT=50` → 50
- 非法值（"abc"）→ 回落 30 不抛异常
- 过小值（"3"）→ clamp 到 10
- `_make_config()` 返回 recursion_limit == cfg.agent_recursion_limit

---

## M2：U2 RepositoryEntity.url 持久化

**现状**：build() 无 repo_url 参数；builder.py:623 `"url": ""`；`_run_build` 已有 repo_url。

**修复**：
1. `builder.build()` 增加可选参数 `repo_url: str = ""`（放在 repo_id 之后）
2. `builder.py` 写 RepositoryEntity 处：`"url": repo_url`（**审查结论：非空才覆盖**——见 U3 合并方案）
3. `_run_build` 调用 `builder.build(..., repo_url=repo_url)`（build.py:188-196）

**测试**：
- builder.build(repo_url="https://gitee.com/x/y.git") → merge_node 收到 url 属性
- 默认不传 → url=""（向后兼容）
- _run_build mock build 捕获 kwargs 含 repo_url=repo_url

---

## M3：U3 RepositoryEntity 复用（与 U2 合并实施）

**根因**：`id=_stable_id(name,url)` 确定性，但 builder 用 url="" vs repo.py 用真实 url → id 不一致累积。

**修复（builder.py build() 内，Stage 2 前，与 U2 一起改）**：
```python
# 复用已有 RepositoryEntity（按 name=repo_id 定向查询），避免两入口 id 不一致累积重复
existing = graph_store.get_nodes_by_label("RepositoryEntity", ["id", "name", "url"])
repo_entity_id = ""
repo_url_old = ""
for n in existing:
    if n.get("name") == self._repo_id:
        repo_entity_id = n.get("id") or ""
        repo_url_old = n.get("url") or ""
        break
if not repo_entity_id:
    repo_entity_id = RepositoryEntity(name=self._repo_id).id
# url 优先取本次构建传入，其次保留已有 url（审查结论：非空才覆盖）
final_url = repo_url or repo_url_old
repo_props = add_provenance(
    {
        "id": repo_entity_id,
        "name": self._repo_id,
        "url": final_url,
        "branch": "main",
        "status": "building",
    },
    source="builder",
    confidence=1.0,
    extracted_at=batch_time,
)
graph_store.merge_node("RepositoryEntity", repo_props)
```
说明：
- 查已有节点取 **id + url**（url 用于兜底保留，不取 status 等无用字段）
- 已有节点 → 复用其 id（merge_node MERGE 键），url 用新值或旧值
- 无已有 → 新建 `RepositoryEntity(name=repo_id)`（id=_stable_id(name,"")）
- 并发读-改-写竞态：残留风险（两任务同时查不到会各建一条），审查结论接受该风险——确定性 id + MERGE 幂等已大幅缓解；严格并发锁不纳入本批

**测试**（tests/unit/pipeline/test_builder.py）：
- get_nodes_by_label 返回含 name=repo_id 节点（有 id 有 url）→ merge_node 收到该 id + 新 repo_url
- 已有节点 + repo_url="" → merge_node 收到**旧 url**（不覆盖）
- 无已有节点 → 新建 id=_stable_id(name,"") + 传入 url
- 已有节点但 id 为 None → 新建兜底

---

## M4：U4 lifespan shutdown 优雅清理

**现状**（app.py:46-47）：
```python
    yield
    store.close()
    app.state.acl.close()
```

**修复（审查修正：不用 os._exit，逐个独立 try/except）**：
```python
    yield
    # ---- shutdown: 逐个清理资源（独立 try/except，一个失败不阻塞其余）----
    try:
        store.close()  # Neo4j driver / NebulaGraph 连接池
    except Exception:
        logger.warning("graph store close failed", exc_info=True)
    try:
        app.state.acl.close()  # SQLite
    except Exception:
        logger.warning("acl close failed", exc_info=True)
    # ChromaDB：app.state 未直接持有 chroma_store（builder 内自管），无需处理
    # 若未来 app.state 增加 httpx/embed client，在此追加独立 try/except
```
说明：
- 实查确认 app.state 仅有 graph_store / build_tasks / build_asyncio_tasks / acl / limiter
- chroma_store 由 builder `_get_chroma_store()` 内部懒加载持有（不在 app.state），本批不动
- nebula3 ConnectionPool 在 graph_store 内部，store.close() 覆盖
- **不做 os._exit(0)**（丢 finally/atexit/缓冲刷新）
- 若仍有非守护线程卡住（如 chroma 后台线程），本批先覆盖已有资源；定位具体线程用 `kill -QUIT` 单独验证

**测试**（tests/unit/web/ 或现有 app 测试）：
- lifespan shutdown：mock store.close / acl.close 均被调用
- store.close 抛异常 → acl.close 仍被调用（不中断）
- 回归：现有 web 测试通过

---

## 里程碑与提交

| 里程碑 | 提交信息 |
|--------|---------|
| M1 | `fix(agent): raise recursion_limit to 30 (env-configurable)` |
| M2 | `fix(builder): persist RepositoryEntity.url from repo_url` |
| M3 | `fix(builder): reuse RepositoryEntity by name=repo_id to avoid duplicates` |
| M4 | `fix(web): clean up graph/chroma/httpx resources on lifespan shutdown` |

每里程碑：实现 + 测试 → Hermes 实查验证（pytest + ruff）→ commit 与 push。
