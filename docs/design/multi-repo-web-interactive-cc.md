# OntoAgent 多仓库管理 + Web 交互构建 + Git 拉取 + 权限隔离 架构设计

> **作者**: Claude Code (CC) 独立审核架构师
> **日期**: 2026-07-30
> **状态**: 交叉审核稿（与 Hermes 方案交叉比对）
> **方法**: 自底向上通读源码 → 独立推导方案 → 参考对比

---

## 〇、架构现状总结（通读源码后的事实基础）

### 0.1 当前的构建链路

```
CLI (api/cli.py::build)
  └─ OntoAgentBuilder(config)          # pipeline/builder.py
       ├─ _stage_parse(repo_path)       # tree-sitter 扫描本地目录
       ├─ _stage_write_structural()      # GraphStore.merge_node / merge_relation
       ├─ _stage_semantic()              # LLM 提取 → ConceptEntity
       ├─ _stage_clustering()            # ModuleEntity
       └─ _stage_vector()               # ChromaStore
```

**关键发现：**
- `builder.build()` 的入口参数是 `repo_path: Path`（本地已存在的目录），没有任何 Git clone 能力
- `GitWatcher`（`butler/watchers/git_watcher.py`）只做 `_get_head_ref()`（`git rev-parse HEAD`），它**监视**本地仓库变更，不做 clone
- Web API（`api/web/app.py`）只注册了 `chat_router`、`graph_router`、`trace_router`，**完全没有 build/repo 管理端点**
- GraphStore 是单例：`app.state.graph_store = create_graph_store(config)`，一个 Web 进程只有一个 store

### 0.2 当前的 VID 机制（核心风险点）

```python
# domain/schema.py
def _stable_id(*parts: str) -> str:
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

# CodeEntity.__post_init__
self.id = _stable_id(self.name, self.entity_type, self.file_path,
                     str(self.start_line), str(self.end_line))
```

**事实确认**：VID 计算不含 `repo_id`。两个仓库如果存在 `src/utils/helpers.py:foo()`（同名同路径同行号），VID 必然碰撞，后写入的会 UPSERT 覆盖前一个。这是多仓库场景的**首要技术债**。

NebulaGraph 的 VID 配置是 `FIXED_STRING(36)`（`config.py: nebula_vid_type`），32 字符 hex（+4 字符余量）刚好兼容。

### 0.3 当前的 Space 管理机制

```python
# config.py
nebula_space: str = "ontoagent"   # 单一全局 Space

# store/factory.py
NebulaGraphStore(host, port, user, password, space=config.nebula_space)
```

**事实确认**：一个进程对应一个 NebulaGraph Space，没有动态切 Space 的能力。`NebulaSchemaInitializer` 在 store 初始化时创建 Schema（DDL 异步，有 120s 探针等待）。

### 0.4 GraphStore ABC 的接口边界

`GraphStore`（`store/graph_store.py`）是纯数据操作接口：`merge_node`、`get_node`、`delete_node`、`merge_relation`、`query` 等。**没有**任何 `repo_id`、`space` 切换、或 multi-tenant 的概念。这是所有方案的设计约束。

### 0.5 Neo4j + NebulaGraph 双后端的分叉点

- `graph_router.py` 已经有 `_is_nebula(store)` / `_label_expr(store, var)` 的后端兼容层
- Neo4j 的隔离手段是 **database**（`USE database`）或 **label 前缀**；NebulaGraph 的隔离手段是 **Space**
- 两者语义不同：Neo4j 跨 database 查询不支持原生 JOIN，NebulaGraph 跨 Space 也不支持

---

## 一、设计方案

### 方案一：VID 命名空间 + 属性隔离（最小侵入，逻辑分区）

#### 核心思路

不改变单 Space 的物理拓扑，通过**VID 前缀注入**实现仓库隔离。每个实体 VID 在计算时注入 `repo_id` 作为第一个 hash part，从根本上消除 VID 碰撞。配合所有实体新增 `repoId` 属性，查询层通过属性过滤实现逻辑分区。

```
单 Space "ontoagent"
  ├── CodeEntity {id: "a3f2...", repoId: "repo-github-foo", name: "foo", ...}
  ├── CodeEntity {id: "b7c1...", repoId: "repo-gitee-bar", name: "foo", ...}   ← 不碰撞
  └── RepositoryEntity {id: "repo:github-foo", url: "...", branch: "main", ...}
```

#### VID 改造（解决碰撞的根因）

```python
# 改造后的 _stable_id 签名（向后兼容）
def _stable_id(*parts: str) -> str:
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

# 在 builder 层注入 repo_id 到所有实体的 hash 输入
class OntoAgentBuilder:
    def __init__(self, config, repo_id: str = "default"):
        self._repo_id = repo_id
        ...

    def _entity_to_dict(self, entity):
        d = super()._entity_to_dict(entity)
        d["repoId"] = self._repo_id
        # 如果 entity.id 尚未显式传入，在 __post_init__ 前注入 repo_id
        return d
```

更精确的做法是修改 `CodeEntity.__post_init__`，让它在有 `repo_id` context 时参与 hash：

```python
@dataclass
class CodeEntity:
    name: str
    entity_type: str
    repo_id: str = ""           # 新增字段
    id: str = ""
    ...

    def __post_init__(self):
        if not self.id:
            self.id = _stable_id(
                self.repo_id,           # ← 注入 repo_id 消除跨仓库碰撞
                self.name,
                self.entity_type,
                self.file_path,
                str(self.start_line),
                str(self.end_line),
            )
```

**VID 长度不变**（32 hex 字符），`FIXED_STRING(36)` 足够容纳。这是一次 **schema version 升级 + 数据迁移**（旧 VID 全部失效，需要全量重建或写迁移脚本重 hash）。

#### 改动量评估

| 层 | 文件 | 改动 | 复杂度 |
|----|------|------|--------|
| Domain | `schema.py` | 所有 dataclass 加 `repo_id` 字段 + `__post_init__` 注入 | 中 |
| Domain | `schema.py` | 新增 `RepositoryEntity`（url, branch, commit, status, built_at） | 低 |
| Store | `nebula_schema.py` | 所有 Tag DDL 加 `repoId string` 属性 | 低 |
| Store | `neo4j_store.py` | `ensure_constraints` 为 repoId 加索引 | 低 |
| Store | `nebula_store.py` | 无需改（属性透传） | 无 |
| Pipeline | `builder.py` | `build()` 加 `repo_id` 参数，传给所有 entity 构造 | 中 |
| Pipeline | `builder.py` | 写入 `RepositoryEntity` 作为仓库元数据节点 | 低 |
| API | `web/router/build.py`（新增） | `POST /api/build` 异步触发 | 中 |
| API | `web/router/repo.py`（新增） | 仓库 CRUD + Git clone | 中 |
| API | `web/app.py` | 注册新 router + BuildTaskManager | 低 |
| Service | `build/task_manager.py`（新增） | 异步任务编排（复用 asyncio） | 中 |
| Service | `git/clone_service.py`（新增） | 安全 clone 封装 | 中 |
| Config | `config.py` | 加 git 白名单、clone 超时、work 目录 | 低 |
| 前端 | `frontend/` | 仓库管理页面 + 构建触发 + 进度 | 中 |

#### 优缺点

**优点：**
- **改动量可控**：核心改动集中在 Domain 层（加字段）和 Pipeline 层（注入 repo_id），Store 层几乎不动
- **双后端天然兼容**：`repoId` 属性在 Neo4j 和 NebulaGraph 上语义一致，不需要 store 级分叉
- **跨仓库查询原生支持**：同一个 Space 内 `MATCH` 直接查，只需 `WHERE repoId IN [...]`
- **无 Space 数量上限问题**：始终只有一个 Space
- **已有基础设施复用**：`ButlerEngine` + `EventBus` 可直接承载异步构建

**缺点：**
- **隔离是逻辑的，非物理的**：忘加 `WHERE repoId` 过滤会跨仓库查（但可以通过 query 拦截层自动注入）
- **权限隔离最复杂**：NebulaGraph Space RBAC 用不上，必须自建应用层 user→repo 映射表
- **性能衰减**：单 Space 数据量大后，`repoId` 索引选择性可能不够（需要在 NebulaGraph 上建 Tag 索引）
- **Schema 迁移成本**：VID 改变 = 所有已有图谱数据失效，需要全量重建

**适用场景：** 仓库数 < 30，单团队或弱多租户，优先快速交付

---

### 方案二：动态 Space 路由 + 全局桥接 Space（物理隔离，NebulaGraph 原生）

#### 核心思路

利用 NebulaGraph 的 Space 级物理隔离 + 原生 RBAC。每个仓库（或仓库组）对应一个独立 Space，通过一个 `SpaceRouter` 在运行时动态切换 `USE space`。新增一个 `ontoagent_global` Space 专门存跨仓库关系和仓库元数据。

```
NebulaGraph 集群
  ├── space: ontoagent_repo_<hash_1>    ← 仓库 A 的全部实体和边
  ├── space: ontoagent_repo_<hash_2>    ← 仓库 B 的全部实体和边
  ├── space: ontoagent_global           ← RepositoryEntity + 跨仓库 DEPENDS_ON 关系
  └── (RBAC: 每个仓库 Space 可独立授权用户)
```

#### SpaceRouter 设计

```python
class SpaceRouter:
    """运行时 Space 路由器。

    管理 {repo_id: space_name} 映射，自动在操作前切换 Space。
    底层复用同一个 ConnectionPool（NebulaGraph 的 session 是 per-space 的）。
    """

    def __init__(self, pool: ConnectionPool, user: str, password: str):
        self._pool = pool
        self._user = user
        self._password = password
        self._repo_to_space: dict[str, str] = {}
        self._global_space = "ontoagent_global"

    @contextmanager
    def session_for(self, repo_id: str) -> Iterator[Session]:
        """获取指定仓库的 session（自动 USE space）。"""
        space = self._repo_to_space.get(repo_id)
        if not space:
            raise ValueError(f"Repo {repo_id} not registered")
        session = self._pool.get_session(self._user, self._password)
        try:
            session.execute(f"USE `{space}`;")
            yield session
        finally:
            session.release()

    def register_repo(self, repo_id: str) -> str:
        """注册仓库 → 创建/确认 Space → 初始化 schema。"""
        space_name = f"ontoagent_repo_{_hash_repo(repo_id)[:12]}"
        self._create_space_if_needed(space_name)
        self._init_schema(space_name)
        self._repo_to_space[repo_id] = space_name
        return space_name
```

#### 跨仓库关系建模

跨仓库关系（如服务 A 调用服务 B 的 API）存在 `ontoagent_global` Space：

```
(global space)
  Repository(url=git://A) --DEPENDS_ON--> Repository(url=git://B)
  Repository(url=git://A) --CONTAINS--> ServiceEntity(name="order-svc")
  ServiceEntity(name="order-svc") --SERVICE_DEPENDS_ON--> ServiceEntity(name="inventory-svc")
```

跨仓库查询需要在应用层做两次查询 + 合并：

```python
def query_across_repos(router: SpaceRouter, repo_ids: list[str], cypher: str):
    """跨仓库查询：每个 repo space 各查一次，结果在应用层合并。"""
    results = []
    for repo_id in repo_ids:
        with router.session_for(repo_id) as session:
            result = session.execute(cypher)
            results.extend(decode(result))
    # 再查 global space 的跨仓库关系
    with router.session_for_global() as session:
        cross_rels = session.execute("MATCH (a)-[r:DEPENDS_ON]->(b) RETURN ...")
    return merge_results(results, cross_rels)
```

#### Neo4j 后端兼容

Neo4j 没有 Space 概念，但有 **database**（`SHOW DATABASES` / `USE database`）。映射关系：

| NebulaGraph | Neo4j |
|-------------|-------|
| Space | Database |
| `USE space` | Session 驱动器指定 database |
| Space RBAC | Database RBAC |

在 `SpaceRouter` 层做抽象，后端实现各走各的：

```python
class SpaceRouter(ABC):
    @contextmanager
    @abstractmethod
    def session_for(self, repo_id: str) -> Iterator: ...

class NebulaSpaceRouter(SpaceRouter): ...
class Neo4jDatabaseRouter(SpaceRouter): ...
```

#### 改动量评估

| 层 | 文件 | 改动 | 复杂度 |
|----|------|------|--------|
| Store | `nebula_store.py` | store 不再在 `__init__` 绑定单一 Space，改由 SpaceRouter 注入 | 高 |
| Store | `space_router.py`（新增） | NebulaSpaceRouter + Neo4jDatabaseRouter | 高 |
| Store | `nebula_schema.py` | 支持 per-space schema 初始化（已有能力，需封装） | 低 |
| Store | `graph_store.py` | `GraphStore` ABC 可能需要加 `set_space` 或由工厂方法传入 space | 中 |
| Domain | `schema.py` | 新增 `RepositoryEntity` + `DEPENDS_ON` 关系 | 低 |
| Pipeline | `builder.py` | `build()` 接收 `repo_id`，通过 router 获取 session | 中 |
| API | `web/router/build.py`（新增） | 异步 build + Space 创建编排 | 中 |
| API | `web/app.py` | 用 SpaceRouter 替代单例 store | 中 |
| 权限 | NebulaGraph 原生 | Space 级 RBAC（`GRANT ROLE IN SPACE`） | 低 |
| 前端 | `frontend/` | 同方案一 | 中 |

#### 优缺点

**优点：**
- **物理隔离最强**：仓库间零数据干扰，`DROP SPACE` 秒级清理
- **权限隔离直接用原生能力**：NebulaGraph `CREATE USER` + `GRANT ROLE IN SPACE xxx TO USER` 即可
- **性能好**：每个 Space 数据量小，查询无需 `WHERE repoId` 过滤
- **单仓库故障隔离**：一个 Space 出问题不影响其他仓库

**缺点：**
- **Space 数量上限**：NebulaGraph 推荐 < 100 Space / 集群（大量 Space 会导致 meta service 压力），不适合 SaaS 多租户
- **首次构建慢**：每个新 Space 需要 DDL 异步等待（已有 120s 探针机制，但每次新建仓库都要等）
- **跨仓库查询复杂**：没有原生跨 Space JOIN，应用层合并性能差，且代码复杂
- **GraphStore ABC 要改**：当前 store 是 "一个实例 = 一个 Space"，改为动态 Space 需要重构初始化链路
- **连接管理复杂**：多 Space 场景下 session 的生命周期管理更复杂
- **Neo4j 兼容成本**：需要实现 Neo4jDatabaseRouter，且 Neo4j Community Edition 不支持多 database

**适用场景：** 仓库数 < 50，强隔离需求（多团队、合规要求、多租户付费场景）

---

### 方案三：Virtual GraphStore 代理层（应用层路由，后端无关）

#### 核心思路

在 `GraphStore` 和后端实现之间插入一个 `MultiRepoGraphStore` 代理层。它不持有实际连接，而是维护一个 `{repo_id: GraphStore}` 映射，所有操作根据 `repo_id` 路由到具体的底层 store 实例。底层 store 可以是同一 Space 的不同 collection，也可以是不同 Space，**代理层不关心**。

```
                    MultiRepoGraphStore (GraphStore ABC)
                     ├── repo_id="A" → NebulaGraphStore(space="ontoagent") [过滤 repoId="A"]
                     ├── repo_id="B" → NebulaGraphStore(space="ontoagent") [过滤 repoId="B"]
                     └── repo_id="global" → NebulaGraphStore(space="ontoagent_global")

Web API / Pipeline 只看到 MultiRepoGraphStore，不感知底层拓扑
```

#### VID 改造（与方案一一致）

VID 计算必须加入 `repo_id`，否则同一 Space 内两个仓库的 `foo()` 会碰撞。这部分改动与方案一相同。

#### MultiRepoGraphStore 设计

```python
class MultiRepoGraphStore(GraphStore):
    """多仓库图存储代理。

    所有操作通过 repo_id 路由到底层 store。
    底层 store 共享同一个 NebulaGraph 连接池（只是 Space/过滤策略不同）。
    """

    def __init__(self, config: OntoAgentConfig):
        self._config = config
        self._stores: dict[str, GraphStore] = {}
        self._repo_registry: dict[str, dict] = {}  # repo_id → metadata

    def register_repo(self, repo_id: str, repo_meta: dict) -> None:
        """注册仓库（创建底层 store 实例或复用）。"""
        store = create_graph_store(self._config)
        self._stores[repo_id] = store
        self._repo_registry[repo_id] = repo_meta

    def _route(self, repo_id: str) -> GraphStore:
        store = self._stores.get(repo_id)
        if store is None:
            raise ValueError(f"Unknown repo: {repo_id}")
        return store

    def merge_node(self, label: str, properties: dict) -> dict:
        repo_id = properties.get("repoId", "default")
        return self._route(repo_id).merge_node(label, properties)

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        # 单仓库查询：路由到对应 store
        # 跨仓库查询：需要特殊处理（应用层 fan-out）
        ...
```

#### 与方案一/二的区别

| 维度 | 方案一（属性隔离） | 方案二（Space 隔离） | 方案三（Virtual Store） |
|------|-------------------|---------------------|------------------------|
| 隔离位置 | 查询 WHERE 过滤 | NebulaGraph Space 物理隔离 | 代理层路由（可选） |
| GraphStore ABC | 不改 | 需改 | 需改（新增子类） |
| 后端耦合 | 无 | NebulaGraph Space + Neo4j DB | 无（底层 store 不变） |
| 跨仓库查询 | 同 Space MATCH | 应用层合并 | 代理层 fan-out |
| 演进灵活性 | 低（锁死单 Space） | 中（锁死多 Space） | 高（底层可切换） |

#### 改动量评估

| 层 | 文件 | 改动 | 复杂度 |
|----|------|------|--------|
| Store | `multi_repo_store.py`（新增） | MultiRepoGraphStore 代理类 | 中 |
| Store | `graph_store.py` | 可能加 `get_repo_id()` / `set_repo_id()` | 低 |
| Domain | `schema.py` | 同方案一（repo_id 字段 + RepositoryEntity） | 中 |
| Pipeline | `builder.py` | `build()` 加 `repo_id`，store 用 MultiRepoGraphStore | 中 |
| API | `web/router/build.py` + `repo.py`（新增） | 同方案一 | 中 |
| Config | `config.py` | 加多仓库策略选择（single_space / multi_space / virtual） | 低 |
| 前端 | `frontend/` | 同方案一 | 中 |

#### 优缺点

**优点：**
- **后端无关性最好**：底层 store 不变，代理层处理路由逻辑，Neo4j 和 NebulaGraph 代码路径一致
- **演进灵活**：初期底层用单 Space（等同方案一），后续可以无缝切换为多 Space（底层 store 换配置即可）
- **对 pipeline 侵入最小**：pipeline 只拿 MultiRepoGraphStore，不关心隔离策略
- **测试友好**：代理层可以 mock，底层 store 的测试不受影响

**缺点：**
- **多了一层抽象**：调试时需要穿透代理层定位问题
- **跨仓库查询仍然需要应用层处理**：代理层的 fan-out 性能取决于底层拓扑
- **连接池管理**：如果底层是多 Space，每个 store 实例占独立 session，连接池膨胀
- **代理层的 query 方法难抽象**：Cypher 是底层 store 特定的（Neo4j vs NebulaGraph 语法不同），代理层无法做通用 query 改写

**适用场景：** 需要同时支持 Neo4j 和 NebulaGraph，且未来可能切换隔离策略的场景

---

## 二、横向对比：六个关键设计决策

### 2.1 NebulaGraph 特性适配

| 特性 | 方案一（属性） | 方案二（Space） | 方案三（Virtual） |
|------|--------------|----------------|-------------------|
| Space 级 RBAC | ❌ 用不上 | ✅ 原生支持 | ⚠️ 取决于底层 |
| Space 数量上限 | ✅ 无影响（1个） | ⚠️ <100 限制 | ✅ 可控 |
| DDL 异步等待 | ✅ 只等一次 | ❌ 每个新仓库都等 | ⚠️ 取决于底层 |
| VID 碰撞 | ✅ hash 注入解决 | ✅ 天然隔离 | ✅ hash 注入解决 |
| Tag 索引选择性 | ⚠️ repoId 需建索引 | ✅ 每空间小数据量 | ⚠️ 取决于底层 |

### 2.2 双后端兼容（Neo4j + NebulaGraph）

| 维度 | 方案一 | 方案二 | 方案三 |
|------|--------|--------|--------|
| Neo4j 兼容 | ✅ 属性过滤天然兼容 | ⚠️ 需实现 Neo4jDatabaseRouter（Community 不支持多 DB） | ✅ 代理层屏蔽差异 |
| 代码分叉 | 无 | 有（Space/DB 路由不同） | 无 |
| 测试成本 | 低 | 高（双后端各测一套） | 中 |

### 2.3 VID 碰撞风险（三方桘认同的共识）

**所有三个方案都必须解决 VID 碰撞**。根因是 `_stable_id()` 的 hash 输入不含 `repo_id`：

```python
# 当前（危险）
_stable_id(name, entity_type, file_path, start_line, end_line)

# 修复后（安全）
_stable_id(repo_id, name, entity_type, file_path, start_line, end_line)
```

**方案二的特殊性**：如果每个仓库用独立 Space，VID 碰撞不会跨仓库发生（不同 Space 的 VID 空间独立）。但**跨仓库关系**（存在 global Space）如果引用实体 ID，仍然需要 repo_id 前缀来消歧。因此方案二也建议改 VID。

**迁移成本**：VID 改变 = 已有图谱数据全部失效。要么全量重建（推荐），要么写一次性迁移脚本（重 hash 所有节点 + 重建边）。

### 2.4 异步构建（Web 端 build 是长操作）

构建流水线涉及 tree-sitter 解析 + Neo4j 批量写入 + LLM 语义提取，大型仓库可能几分钟。Web 端必须异步：

```
POST /api/build {repo_url, branch}
  → 202 Accepted {task_id}
  → 后台: GitService.clone() → BuildTask.run(builder.build()) → 更新状态
  → GET /api/build/status/{task_id} 轮询
  → SSE /api/build/stream/{task_id} 推进度（复用 sse-starlette）
```

**已有基础设施**：
- `ButlerEngine` + `EventBus`（`butler/engine.py`）是现成的异步任务引擎，可以直接复用
- `FullBuildHandler`（`butler/handlers/knowledge_update.py`）已经实现了 build 事件的 handler
- `sse-starlette` 已在 `chat.py` 使用，进度推送可以复用

**新增组件**：`BuildTaskManager`（内存任务表，或用 SQLite 持久化 task 状态）。

### 2.5 Git Clone 安全

Web 端接收任意 Git URL 是高风险操作。安全清单：

| 风险 | 措施 | 实现位置 |
|------|------|---------|
| SSRF（内网探测） | URL 域名白名单（`github.com`、`gitee.com`、内网 GitLab 域名） | `config.py` + `GitCloneService` |
| 恶意仓库体积 | `--depth 1 --single-branch` 浅克隆 | `GitCloneService` |
| Clone 超时 | `asyncio.timeout(300)` + `subprocess` 超时 | `GitCloneService` |
| 命令注入 | URL 经 `urllib.parse` 校验，禁止 shell=True | `GitCloneService` |
| 临时目录泄露 | `tempfile.TemporaryDirectory()` + finally 清理 | `GitCloneService` |
| 私有仓库 | 支持 HTTPS token / SSH key 注入（env 注入，不落盘） | `config.py` + `GitCloneService` |
| 路径穿越 | clone 目标目录用 `uuid` 命名，禁止用户控制路径 | `GitCloneService` |

```python
class GitCloneService:
    """安全的 Git clone 服务。"""

    ALLOWED_HOSTS: list[str] = ["github.com", "gitee.com", "gitlab.com"]
    CLONE_TIMEOUT: int = 300
    MAX_DEPTH: int = 1

    def __init__(self, config: OntoAgentConfig):
        self._work_dir = Path(config.git_work_dir)
        self._allowed_hosts = config.git_allowed_hosts or self.ALLOWED_HOSTS

    async def clone(self, repo_url: str, branch: str = "main",
                    token: str | None = None) -> Path:
        self._validate_url(repo_url)
        work_path = self._work_dir / uuid4().hex
        cmd = ["git", "clone", "--depth", str(self.MAX_DEPTH),
               "--single-branch", "--branch", branch, repo_url, str(work_path)]
        await asyncio.to_thread(self._run_clone, cmd)
        return work_path

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "git", "ssh"):
            raise ValueError(f"Unsupported scheme: {parsed.scheme}")
        if parsed.hostname not in self._allowed_hosts:
            raise ValueError(f"Host not allowed: {parsed.hostname}")
```

### 2.6 权限隔离

| 方案 | 机制 | 复杂度 | 强度 |
|------|------|--------|------|
| 方案一 | 应用层 user→repo 映射表（SQLite） | 高 | 中（代码级） |
| 方案二 | NebulaGraph Space RBAC（GOD/DBA/USER/GUEST） | 低 | 强（数据库级） |
| 方案三 | 代理层路由 = 天然 ACL | 中 | 中（代码级） |

**方案二的 RBAC 细节**：
```ngql
-- 管理员创建仓库 Space
CREATE SPACE ontoagent_repo_xxx (...);
-- 创建仓库专属用户
CREATE USER IF NOT EXISTS repo_xxx_user WITH PASSWORD '...';
-- 授权
GRANT ROLE DBA IN SPACE ontoagent_repo_xxx TO repo_xxx_user;
```

**方案一/三的应用层 ACL**：
```python
class RepoAccessControl:
    """应用层仓库权限控制。"""

    def can_access(self, user_id: str, repo_id: str, action: str) -> bool:
        # 查 SQLite: user_repo_acl(user_id, repo_id, role)
        ...
```

---

## 三、推荐排序与理由

### 🥇 首推：方案一（属性隔离 + VID 注入）作为 Phase 1

**理由：**

1. **改动量与风险的最佳平衡**：核心改动只在 Domain 层（加 `repo_id` 字段 + 改 hash 输入）和 Pipeline 层（注入 repo_id），Store 层几乎不动。这意味着双后端（Neo4j + NebulaGraph）的测试矩阵不需要大改。

2. **立即解决最致命的 bug**：VID 碰撞是当前多仓库场景的 P0 隐患，方案一直接修复根因。

3. **复用已有基础设施**：ButlerEngine 已有异步事件驱动能力，`POST /api/build` 只需要把 Web 端请求转成 `ButlerEvent` 投递到 EventBus。SSE 进度推送已在 chat router 验证过。

4. **演进路径清晰**：方案一 → 方案三（加代理层）→ 方案二（底层切多 Space）是渐进升级，每一步都可以独立交付价值。

5. **跨仓库查询是大多数场景的刚需**：属性过滤在同一 Space 内做 `MATCH` 性能远好于跨 Space 应用层合并。

### 🥈 次选：方案三（Virtual GraphStore）作为架构演进储备

**理由：**

如果团队预见到未来需要切换隔离策略（先单 Space，后多 Space），或需要同时支持 Neo4j 和 NebulaGraph 的多仓库，方案三的代理层是最干净的抽象。但它引入了额外的复杂度，不适合作为第一版。

### 🥉 备选：方案二（Space 隔离）仅在强合规场景使用

**理由：**

物理隔离最强，但代价也最高。只有在以下条件**同时满足**时才推荐：
- 仓库数确定 < 50
- 有合规要求（如不同客户的代码不能混在同一个数据库）
- 主要使用 NebulaGraph（Neo4j Community 不支持多 database）
- 跨仓库查询需求弱

---

## 四、实施路线图（推荐方案一的 Phase 分解）

### Phase 0：VID 修复 + RepositoryEntity（1-2 天）
- `schema.py`：所有 dataclass 加 `repo_id` 字段，`__post_init__` 注入到 hash
- `schema.py`：新增 `RepositoryEntity`（name, url, branch, commit_hash, status, built_at）
- `nebula_schema.py`：所有 Tag 加 `repoId string` 属性 + 索引
- `neo4j_store.py`：`ensure_constraints` 加 repoId 索引
- 写迁移脚本：旧 VID → 新 VID 的重 hash 工具

### Phase 1：Git Clone 服务 + Web Build 端点（2-3 天）
- `service/git_clone.py`：安全 clone 封装（白名单、超时、浅克隆）
- `api/web/router/build.py`：`POST /api/build`（异步）+ `GET /api/build/status/{task_id}`
- `api/web/router/repo.py`：仓库注册/列表/详情
- `build/task_manager.py`：异步任务编排（复用 ButlerEngine）
- SSE 进度推送端点

### Phase 2：Pipeline 注入 + 多仓库构建（2 天）
- `builder.py`：`build(repo_path, repo_id=...)` 参数注入
- builder 写入 `RepositoryEntity` 作为仓库元数据
- 所有实体和关系写入时自动带 `repoId` 属性

### Phase 3：前端仓库管理页面（3-4 天）
- 仓库列表页（CRUD + 构建状态）
- 添加仓库页（输入 Git URL + 分支 + Token）
- 构建进度页（SSE 实时进度）
- 图谱可视化页加仓库选择器（`WHERE repoId = $selectedRepo`）

### Phase 4：权限隔离（按需，2-3 天）
- 应用层 user→repo ACL（SQLite 表）
- Web 中间件注入 `user_id` → query 自动过滤
- 或对接已有认证系统（如 OIDC）

---

## 五、与 Hermes 方案的交叉比对

| 维度 | Hermes 方案 | CC 方案（本文） | 差异分析 |
|------|------------|----------------|---------|
| 方案数量 | 5 个（A-E） | 3 个 | CC 精简为 3 个核心方案，合并了 Hermes 的 A/C 和 D/E |
| 推荐策略 | B→C 渐进 | 方案一（属性隔离）优先 | **一致**：双方都推荐属性隔离作为首选 |
| VID 修复 | hash 加 repo_id | hash 加 repo_id | **完全一致** |
| Space 隔离 | 方案 A 不推荐 | 方案二备选 | CC 更倾向于保留 Space 隔离作为合规场景选项 |
| Virtual Store | 方案 D 备选 | 方案三次选 | **一致**：双方认为代理层是好抽象但不宜第一版 |
| MVP 方案 | 方案 E（快速验证） | 未单列 | CC 认为 Phase 分解比 MVP 更务实 |
| 异步构建 | 复用 Butler | 复用 Butler | **完全一致** |
| Git 安全 | 白名单+超时+浅克隆 | 白名单+超时+浅克隆 | **完全一致**，CC 补充了 SSRF 和命令注入细节 |
| Neo4j 兼容 | 方案 D 提及 | 方案二和三详细分析 | CC 更深入分析了 Neo4j Community 不支持多 DB 的问题 |

**结论**：两个方案在核心推荐上高度一致（属性隔离 + VID 修复优先），CC 的差异在于：
1. 更精简的方案矩阵（3 vs 5）
2. 更详细的 NebulaGraph/Neo4j 双后端兼容性分析
3. 更具体的 Phase 分解和工时估算
4. 保留了 Space 隔离作为合规场景的可选项（而非直接不推荐）

---

## 六、风险清单

| # | 风险 | 影响 | 缓解措施 |
|---|------|------|---------|
| R1 | VID 变更导致旧数据失效 | 高 — 全量重建 | 提供迁移脚本 + 版本号检测自动提示 |
| R2 | NebulaGraph Tag 索引未建导致全扫描 | 中 — 查询慢 | `CREATE TAG INDEX IF NOT EXISTS` 在 schema init 时建 |
| R3 | Git clone 超时占用 worker | 中 — 并发降低 | asyncio 超时 + 任务队列限流 |
| R4 | 大仓库构建 OOM | 中 — 进程崩溃 | 分批写入（已有 batch_size=200）+ 内存监控 |
| R5 | 前端 SSE 连接泄漏 | 低 — 资源浪费 | 心跳检测 + 超时断开 |
| R6 | 多仓库跨仓库关系建立规则不明确 | 中 — 数据不完整 | Phase 2 明确跨仓库关系发现策略（import 分析 + 服务依赖） |
