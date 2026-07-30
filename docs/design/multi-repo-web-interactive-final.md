# 最终方案：多仓库管理 + Web 交互构建 + Git 拉取 + 权限隔离

> 合并自 Hermes 5 套方案 + Claude Code 3 套方案的交叉审核
> 日期: 2026-07-30
> 状态: **三方审核完毕，可作为实施依据**

## 一、两方审核结论

### Hermes（5 套方案）和 CC（3 套方案）的共识

| 维度 | 共识 |
|------|------|
| **首要技术债** | VID 碰撞 — `_stable_id()` 不含 `repo_id`，两仓库同名函数会互相覆盖 |
| **推荐首选** | 属性隔离（单 Space + `repoId` 属性过滤）— 改动最小、双后端兼容 |
| **Space 隔离定位** | 备选，仅强合规场景使用 |
| **Virtual Store 定位** | 架构储备，不作为第一版 |
| **异步构建** | 复用 Butler EventBus + SSE 进度推送 |
| **Git 安全** | URL 白名单 + 浅克隆 + 超时 + 临时目录隔离 |

### CC 对 Hermes 方案的审核意见（有价值补充）

1. **Neo4j Community 不支持多 database** — Hermes 方案 D 没提到，CC 指出这意味着 Space 隔离方案在 Neo4j Community 上无法实现
2. **SSRF 风险** — CC 补充了 Web 端接收 Git URL 的 SSRF 防护（禁止内网地址探测）
3. **方案精简** — CC 把 Hermes 的 A/C 合并为"Space 隔离"，D/E 合并为"Virtual Store"，更清晰
4. **Phase 分解** — CC 给了具体的工时估算（8-13 天），Hermes 没给
5. **NebulaGraph 索引选择性** — CC 指出单 Space 方案在数据量大后 `repoId` 索引需要显式建 Tag 索引

### Hermes 对 CC 方案的审核意见（有价值补充）

1. **CC 遗漏了跨仓库关系建模的具体方案** — Hermes 方案 A 提出了"全局 Space 存跨仓库关系"的模式，CC 方案二也有但没有充分展开跨仓库 DEPENDS_ON 关系的自动发现机制
2. **CC 的方案一没有处理 query 拦截层** — 单 Space 属性隔离的最大风险是"忘加 WHERE repoId"，需要一个查询拦截层自动注入过滤条件，CC 只提了一句"可以通过 query 拦截层自动注入"但没设计

---

## 二、最终推荐方案：渐进式三阶段

### Phase 1：属性隔离 + VID 修复（核心，1-2 周）

**策略**：单 Space + `repoId` 属性隔离 + VID hash 注入 `repo_id`

这是 Hermes 和 CC **双方一致推荐的首选方案**。

#### 改动清单

| 序号 | 层 | 改动 | 复杂度 | 工时 |
|------|-----|------|--------|------|
| 1 | Domain | `CodeEntity` 加 `repo_id` 字段，`__post_init__` hash 输入加 `repo_id` | 中 | 0.5天 |
| 2 | Domain | 新增 `RepositoryEntity`（url, branch, commit_hash, status, built_at, space_name） | 低 | 0.5天 |
| 3 | Domain | `RELATION_TYPE_TO_NEO4J` 加 `belongs_to_repo` → `BELONGS_TO_REPO` | 低 | 0.1天 |
| 4 | Store | `nebula_schema.py` 所有 Tag DDL 加 `repoId string` + `CREATE TAG INDEX idx_repoId` | 低 | 0.5天 |
| 5 | Store | `neo4j_store.py` `ensure_constraints` 为 repoId 加索引 | 低 | 0.3天 |
| 6 | Pipeline | `builder.py` `build()` 加 `repo_id` 参数，实体构造时注入 | 中 | 1天 |
| 7 | Pipeline | builder 写入 `RepositoryEntity` + `BELONGS_TO_REPO` 关系 | 低 | 0.5天 |
| 8 | Schema | `CURRENT_SCHEMA_VERSION` 升级（VID 变更需数据迁移） | 低 | 0.3天 |
| 9 | Service | `git/clone_service.py` 安全 clone（白名单+超时+浅克隆+SSRF防护） | 中 | 1天 |
| 10 | API | `web/router/build.py` `POST /api/build` 异步 + `GET /api/build/status/{task_id}` | 中 | 1天 |
| 11 | API | `web/router/repo.py` 仓库 CRUD + 列表 | 中 | 1天 |
| 12 | API | SSE 进度推送端点（复用 `sse-starlette`） | 低 | 0.5天 |
| 13 | Config | `config.py` 加 git 白名单、clone 超时、work 目录 | 低 | 0.3天 |

**合计**：~8 天

#### VID 改造方案（最关键）

```python
# 改前（碰撞风险）
vid = _stable_id(name, entity_type, file_path, start_line, end_line)

# 改后（安全）
vid = _stable_id(repo_id, name, entity_type, file_path, start_line, end_line)
```

**影响**：
- VID 长度不变（32 hex），`FIXED_STRING(36)` 兼容
- 旧数据全部失效（VID 变了）→ 需要 `--clear` 全量重建
- `CURRENT_SCHEMA_VERSION` 升级

#### Query 拦截层（Hermes 补充设计）

单 Space 属性隔离的最大风险是"忘加 WHERE repoId 过滤"。两种解决路径：

**路径 A：显式传参（推荐）**
所有需要仓库隔离的查询，调用方显式传 `repo_id`，store 层自动注入 `WHERE repoId == $repoId`。

**路径 B：ContextVar 自动注入**
用 Python `contextvars` 在请求级别绑定 `repo_id`，store 层 query 方法自动读取并注入。对已有代码侵入更小但有隐式行为。

> **建议先用路径 A**，显式 > 隐式，等 pattern 稳定后再考虑提取为拦截层。

### Phase 2：前端仓库管理 + 跨仓库关系（2-3 天）

- 仓库列表页（CRUD + 构建状态 badge）
- 添加仓库页（输入 Git URL + 分支 + 可选 Token）
- 构建进度页（SSE 实时进度条）
- 图谱可视化页加仓库选择器（`WHERE repoId = $selectedRepo`）
- 跨仓库关系自动发现（分析 import 路径 → 建立 `Repository --DEPENDS_ON--> Repository`）

### Phase 3：权限隔离（按需，2-3 天）

**推荐**：应用层 ACL（SQLite user→repo 映射表），不依赖 NebulaGraph RBAC。

理由：
- Phase 1 是单 Space，NebulaGraph Space RBAC 用不上
- 应用层 ACL 更灵活（可以做到 repo 级 read/write/admin 三级权限）
- 未来如果升级到 Space 隔离（Phase 4），再叠加 NebulaGraph RBAC

---

## 三、备选方案（仅当 Phase 1 不够用时升级）

### 备选 A：Virtual GraphStore 代理层

**升级时机**：当需要同时支持多种隔离策略（部分仓库单 Space，部分仓库独立 Space），或需要从单 Space 无缝迁移到多 Space 时。

**核心**：在 GraphStore 之上加 `MultiRepoGraphStore` 代理层，路由逻辑与底层拓扑解耦。

### 备选 B：多 Space 物理隔离

**升级时机**：强合规场景（不同客户代码不能混在同一数据库），或仓库数确定 <50 且需要数据库级 RBAC。

**核心**：每仓库独立 NebulaGraph Space + `SpaceRouter` 动态切换 + `ontoagent_global` 存跨仓库关系。

**注意**：Neo4j Community Edition 不支持多 database，此方案在 Neo4j Community 上无法使用。

---

## 四、Git Clone 安全设计（双方一致）

| 风险 | 措施 |
|------|------|
| SSRF（内网探测） | URL 域名白名单（github.com / gitee.com / 内网 GitLab） |
| 恶意仓库体积 | `--depth 1 --single-branch` 浅克隆 |
| Clone 超时 | `asyncio.timeout(300)` |
| 命令注入 | URL 经 `urllib.parse` 校验，禁止 `shell=True` |
| 临时目录泄露 | `tempfile.TemporaryDirectory()` + finally 清理 |
| 私有仓库 | HTTPS token / SSH key 通过环境变量注入，不落盘 |
| 路径穿越 | clone 目录用 `uuid4().hex` 命名 |

---

## 五、异步构建设计（双方一致）

```
POST /api/build {repo_url, branch}
  → 202 Accepted {task_id}
  → 后台: GitCloneService.clone() → BuildTask.run(builder.build(repo_id=...)) → 更新状态
  → GET /api/build/status/{task_id} 轮询
  → SSE /api/build/stream/{task_id} 推进度（复用已有 sse-starlette）
```

**复用已有基础设施**：
- `ButlerEngine` + `EventBus`（异步任务引擎）
- `FullBuildHandler`（已有 build 事件 handler）
- `sse-starlette`（已有 SSE 基础设施，chat/stream 已验证）

---

## 六、风险清单（合并双方）

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| R1 | VID 变更导致旧数据失效 | 高 | 全量重建 + `CURRENT_SCHEMA_VERSION` 升级提示 |
| R2 | NebulaGraph Tag 索引未建 → 全扫描 | 中 | `CREATE TAG INDEX IF NOT EXISTS idx_repoId` 在 schema init 时建 |
| R3 | Git clone 超时占用 worker | 中 | asyncio 超时 + 任务队列限流 |
| R4 | 大仓库构建 OOM | 中 | 分批写入（已有 batch_size=200）+ 内存监控 |
| R5 | 单 Space 数据量大后性能衰减 | 中 | 监控节点数，>10万考虑升级到 Virtual Store 或 Space 隔离 |
| R6 | "忘加 WHERE repoId" 导致跨仓库泄漏 | 中 | 显式 `repo_id` 参数传递 + 未来加 query 拦截层 |
| R7 | Neo4j Community 不支持多 DB | 低 | 仅影响备选方案 B，Phase 1 不受影响 |
