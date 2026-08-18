# OntoAgent 公共缺陷修复 · 第二批实施计划

> 日期：2026-08-12 · 范围：5 项（T1/T2/T3/T4/T5）
> 流程：独立审查结论（2026-08-12，4 项 ISSUE 已纳入）→ 逐项 TDD 实施 → 每项验证、commit 与 push

## 独立审查结论（2026-08-12）

- **T1 ISSUE（已采纳，重大）**：LangChain `ChatOpenAI` **顶层 `extra_body` 参数各版本行为不一致**（部分抛 pydantic ValidationError，部分静默丢弃）。修正：`kwargs["model_kwargs"] = {"extra_body": cfg.agent_llm_extra_body}`——openai SDK `create()` 接收 `extra_body` 作为合法参数并入请求体，此路径稳定。（注意：`model_kwargs` 的值是 `{"extra_body": {...}}` 嵌套，不是把 `thinking` 等字段直接放 model_kwargs 顶层——后者会被展开成 create() 的顶层 kwargs 导致 TypeError，这正是本缺陷的现象。）
- **T2 PASS**：`CREATE INDEX idx_x IF NOT EXISTS FOR (n:Label) ON (n.prop)` 是 Neo4j 4.3+/5.x 正确语法。label 来自内部常量 ENTITY_LABELS（非外部输入），f-string 插值安全。
- **T3 ISSUE（已采纳）**：避免"props 含 id 返回实体 id、不含返回内部 id"的双语义——**统一始终 `RETURN n.id AS id`**（实体业务 id）；确需内部 id 时另行加参数。nebula 覆写同样统一（props 含 id 用 `Tag.id AS id`，不含也用 `Tag.id`——不再用 `id(vertex)`）。**props 去重**（`dict.fromkeys` 或 set 保序）。
- **T4 ISSUE（已采纳，已验证 merge_node 语义）**：实查 `neo4j_store.merge_node` 是 `MERGE (n:Label {id:$id}) SET n.key=$key`——**仅 SET 传入字段，不覆盖其他属性**（url/branch 保留），方案可行。两个坑：① 找不到 `name==repo_id` 节点时 `n["id"]` 为 None，`merge_node` 会因缺 id 抛 ValueError——**None 守卫跳过**；② merge_node 接收 dict（非 dataclass），签名匹配。`_update_repo_status` 包 try/except 防中断。

## 实查结论（2026-08-12，对照 master @cc4f47b）

| 缺陷 | 实查 | 性质 |
|------|------|------|
| T1 extra_body | master 无 model_kwargs/extra_body 代码、config 无字段 | **新增能力**（正确透传） |
| T2 Neo4j 索引语法 | neo4j_store.py:556 `CREATE INDEX IF NOT EXISTS idx_X FOR` 语法错 | 纯修复 |
| T3 get_nodes_by_label | graph_store.py:204 `RETURN id(n) AS id, n.id AS id` 冲突 | 纯修复 |
| T4 status 未回写 | builder.py:611 硬编码 building，_run_build 无回写 | 纯修复 |
| T5 .env.example Service | master 无 Service 示例；T1 新增示例时避免 | 示例规范 |

---

## M1：T1 extra_body 透传 + T5 示例规范

### T1. config 加 `agent_llm_extra_body` 字段 + `_create_llm` 用 `extra_body` 透传

**现状**（graph.py:54-65）：
```python
def _create_llm() -> ChatOpenAI:
    from ontoagent.agent._helpers import get_config
    cfg = get_config()
    return ChatOpenAI(
        model=cfg.agent_llm_model,
        base_url=cfg.agent_base_url,
        api_key=cfg.agent_api_key,
        timeout=180,
        max_retries=3,
    )
```

**要求**：
1. `config.py` 增加字段 `agent_llm_extra_body: dict | None = None`（dataclass 字段 + `from_env` 解析 `ONTOAGENT_AGENT_LLM_EXTRA_BODY`，JSON 解析失败记 warning 并忽略——参考现有 `build_doc_extensions` 等解析模式）
2. `graph.py::_create_llm` 增加（**审查修正：用 `model_kwargs={"extra_body": ...}` 嵌套，不用顶层 extra_body**——顶层在部分 LangChain 版本抛 ValidationError）：
   ```python
   kwargs: dict = dict(model=..., base_url=..., api_key=..., timeout=180, max_retries=3)
   if cfg.agent_llm_extra_body:
       kwargs["model_kwargs"] = {"extra_body": cfg.agent_llm_extra_body}
   return ChatOpenAI(**kwargs)
   ```
   关键：`model_kwargs` 的值是 `{"extra_body": {...}}` 嵌套；**不是**把 `thinking` 等字段直接放 `model_kwargs` 顶层（那会被展开成 create() 顶层 kwargs → TypeError）
3. 不传时行为不变（无 model_kwargs 参数）

**测试**（tests/unit/agent/ 下新增 test 或扩展现有）：
- config 解析：`ONTOAGENT_AGENT_LLM_EXTRA_BODY={"thinking":true}` → `cfg.agent_llm_extra_body == {"thinking": True}`（JSON 布尔解析）
- 非法 JSON → 字段为 None + 不抛异常
- `_create_llm`：mock config 带 extra_body → 断言 `ChatOpenAI` 收到 `model_kwargs={"extra_body": {"thinking": True}}` 且值不含顶层 `thinking` 键
- 不设 extra_body → `ChatOpenAI` 不收到 model_kwargs

### T5. .env.example 增加 Agent extra_body 示例（不含 Service header）

**要求**：在 `.env.example` 的 Agent LLM 段（L46 附近）增加：
```bash
# OpenAI 兼容网关自定义字段（如 thinking/thinking_budget），经 extra_body 透传
# 注意：不要加 Service 等网关路由 header，部分网关会将其当作服务路由标识
# ONTOAGENT_AGENT_LLM_EXTRA_BODY={"thinking":true,"thinking_budget":1024}
```
**绝不含** `Service` 字段。

---

## M2：T2 Neo4j 索引语法

**现状**（neo4j_store.py:554-559）：
```python
for label in ENTITY_LABELS:
    cypher = f"CREATE INDEX IF NOT EXISTS idx_{label}_repoId FOR (n:{label}) ON (n.repoId)"
```

**修复**：调整关键字顺序：
```python
cypher = f"CREATE INDEX idx_{label}_repoId IF NOT EXISTS FOR (n:{label}) ON (n.repoId)"
```

**测试**：现有 neo4j_store 索引测试若断言了语句串则更新；新增/确认测试断言语句为 `CREATE INDEX idx_CodeEntity_repoId IF NOT EXISTS FOR (n:CodeEntity) ON (n.repoId)`（mock session.run 捕获 cypher）。

---

## M3：T3 get_nodes_by_label 业务 id

**现状**（graph_store.py:189-204）：
```python
def get_nodes_by_label(self, label, properties=None):
    props = properties or ["id", "name"]
    prop_clause = ", ".join(f"n.{p} AS {p}" for p in props)
    return self.query(f"MATCH (n:{label}) RETURN id(n) AS id, {prop_clause}")
```

**修复**（审查修正：统一始终返回业务 id，避免双语义）：
1. `props = list(dict.fromkeys(properties)) if properties else ["id", "name"]`（去重保序）
2. **始终 `RETURN n.id AS id`**（实体业务 id）：
   ```python
   if "id" in props:
       rest = [p for p in props if p != "id"]
       prop_clause = ", ".join(f"n.{p} AS {p}" for p in rest)
       return self.query(f"MATCH (n:{label}) RETURN n.id AS id{', ' + prop_clause if prop_clause else ''}")
   prop_clause = ", ".join(f"n.{p} AS {p}" for p in props)
   return self.query(f"MATCH (n:{label}) RETURN n.id AS id, {prop_clause}")
   ```
3. **nebula_store.py 覆写同样修正**（L925-931）：统一始终 YIELD 业务 id——props 含 "id" 时 `Tag.id AS id` + 其余；不含时也 `Tag.id AS id`（不再用 `id(vertex)`）：
   ```python
   props = list(dict.fromkeys(properties)) if properties else ["id", "name"]
   prop_yield_parts = []
   if "id" not in props:
       props = ["id", *props]  # 保证输出含 id
   for p in props:
       if p == "id":
           prop_yield_parts.append(f"`{label}`.`id` AS `id`")
       else:
           prop_yield_parts.append(f"`{label}`.`{p}` AS `{p}`")
   prop_yield = ", ".join(prop_yield_parts)
   ngql = f'LOOKUP ON `{label}` WHERE `{label}`.name != "" YIELD {prop_yield};'
   ```
   （保留现有 LOOKUP 索引扫描 + MATCH 降级逻辑，只改 YIELD 部分）

**测试**：
- 基类：mock store.query 捕获 cypher → `get_nodes_by_label("RepositoryEntity", ["id","name","url","status"])` → cypher 为 `RETURN n.id AS id, n.name AS name, n.url AS url, n.status AS status`（无 `id(n)`、无重复）
- props 不含 id（如 `["name"]`）→ `RETURN n.id AS id, n.name AS name`（统一业务 id）
- 默认（None）→ props=["id","name"] → `RETURN n.id AS id, n.name AS name`
- props 重复（如 `["id","id","name"]`）→ 去重后不产生重复 alias
- nebula：props 含 id → YIELD `RepositoryEntity.id AS id`（业务 id）；不含 → 同样 `Tag.id AS id`
- repo.py `list_repos` 回归：返回的 id 是业务 uuid

---

## M4：T4 构建完成后 RepositoryEntity.status 回写

**现状**：builder.py:611-619 硬编码 `"status": "building"`；build.py `_run_build` 成功/失败分支只更新内存 task 状态。

**要求**：
1. `build.py::_run_build` 新增辅助函数（**审查修正：None 守卫 + try/except 防中断**）：
   ```python
   def _update_repo_status(request, repo_id: str, status: str) -> None:
       """按 name=repo_id 查找 RepositoryEntity 并回写 status（失败仅告警，不中断主流程）。"""
       try:
           store = request.app.state.graph_store
           nodes = store.get_nodes_by_label("RepositoryEntity", ["id", "name", "status"])
           for n in nodes:
               if n.get("name") == repo_id and n.get("id"):
                   store.merge_node("RepositoryEntity", {"id": n["id"], "name": repo_id, "status": status})
                   return
       except Exception as e:
           logger.warning("Failed to update repo status for %s: %s", repo_id, e)
   ```
2. `_run_build` 成功分支（`current.status = "success"` 后）：`_update_repo_status(request, repo_id, "success")`
3. 失败分支（`current.status = "failed"` 后）：`_update_repo_status(request, repo_id, "failed")`
4. merge_node 是 MERGE + 仅 SET 传入字段（实查确认，url/branch 等已有属性保留）
5. builder.py:611 的 `status="building"` 保留（构建进行中，最终由回写覆盖）

**测试**（tests/unit/web/test_build_router.py 扩展）：
- mock build 成功 → 断言 `_update_repo_status` 被调用且参数为 (repo_id, "success")；mock graph_store 的 get_nodes_by_label 返回含 name=repo_id 的节点 → merge_node 收到 status=success
- mock build 失败 → `_update_repo_status(request, repo_id, "failed")`
- `_update_repo_status` 找不到节点（无 name 匹配）→ 不抛异常、不调 merge_node
- 找到节点但 id 为 None → 跳过（不调 merge_node）
- `_update_repo_status` 内部 store 抛异常 → 不中断（build 状态仍 success/failed）

---

## 里程碑与提交

| 里程碑 | 提交信息 |
|--------|---------|
| M1 | `feat(agent): pass LLM gateway extra_body via openai extra_body + .env.example note` |
| M2 | `fix(store): correct Neo4j CREATE INDEX syntax (name before IF NOT EXISTS)` |
| M3 | `fix(store): get_nodes_by_label returns business id (n.id) not internal id` |
| M4 | `fix(web): write back RepositoryEntity.status on build success/failure` |

每里程碑：实现 + 测试 → Hermes 实查验证（pytest + ruff）→ commit 与 push。
