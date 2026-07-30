# OntoAgent 多仓库 / Web 交互 / Git 拉取 / 权限隔离 方案设计

> 作者: Hermes Agent
> 日期: 2026-07-30
> 状态: 待 CC 交叉审核

## 一、问题诊断

当前 OntoAgent 有 5 个架构短板，全部命中用户提出的痛点：

| # | 短板 | 根因 | 影响 |
|---|------|------|------|
| 1 | 只能 CLI 构建 | Web API 没有 build trigger 端点 | 无法从网站触发图谱构建 |
| 2 | 无网站交互 | 前端只有可视化/对话，没有构建入口 | 用户体验差 |
| 3 | 不能拉 Git | GitWatcher 只检测本地 HEAD，不 clone | 给 URL 也拉不了代码 |
| 4 | 多仓库混在一起 | 单 NebulaGraph space，无仓库边界 | 仓库间数据污染 |
| 5 | 无仓库打标/权限隔离 | Schema 无 Repository 概念，无 RBAC | 无法区分仓库归属和访问控制 |

## 二、方案矩阵

### 方案 A：多 Space 物理隔离（NebulaGraph 原生）

**核心思路**：每个仓库用独立的 NebulaGraph Space，物理隔离数据。

```
space: ontoagent_repo_<hash>  ← 仓库A 的实体和边
space: ontoagent_repo_<hash>  ← 仓库B 的实体和边
space: ontoagent_global       ← 跨仓库关系（服务依赖、API 契约等）
```

**改动量**：

| 层 | 改动 | 复杂度 |
|----|------|--------|
| Domain | 新增 `RepositoryEntity`（url, branch, commit, space_name, status） | 低 |
| Store | `NebulaGraphStore` 支持动态切 space（`session.execute("USE \`xxx\`;")`） | 中 |
| Store | 新增 `MultiSpaceManager`：管理 space 创建/删除/列表 | 中 |
| API | 新增 `POST /api/build` 端点（接收 repo_url, branch） | 低 |
| API | 新增 `POST /api/repo/clone`（git clone 到临时目录） | 低 |
| Pipeline | `OntoAgentBuilder.build` 接收 `repo_id` 参数，写前切 space | 中 |
| 权限 | NebulaGraph 原生 RBAC（GOD/DBA/USER），每个 space 独立授权 | 低 |
| 前端 | 新增仓库管理页面（列表 + 添加 + 构建状态） | 中 |

**优点**：
- 物理隔离最彻底，仓库间零干扰
- NebulaGraph 原生 RBAC 直接用，不造轮子
- 跨仓库查询走 global space，逻辑清晰
- 单仓库清理（DROP SPACE）秒级完成

**缺点**：
- Space 数量多时管理复杂（NebulaGraph 推荐 <100 space/集群）
- 跨仓库 JOIN 需要在应用层做（两个 space 各查一次再合并）
- 每个 space 独立 schema 初始化，首次构建慢（DDL 异步等待 ~20s）

**适用场景**：仓库数 <100，强隔离需求（多团队、多租户）

---

### 方案 B：单 Space + 仓库标签属性隔离

**核心思路**：所有仓库数据在同一个 NebulaGraph Space 里，通过 CodeEntity 的 `repoId` 属性做逻辑隔离。

```
CodeEntity {
  id: "sha256hash",
  repoId: "repo-a",      ← 新增属性
  repoUrl: "git://...",   ← 新增属性
  name: "foo",
  ...
}
```

查询时加 `WHERE repoId == "repo-a"` 过滤。

**改动量**：

| 层 | 改动 | 复杂度 |
|----|------|--------|
| Domain | CodeEntity 加 `repoId` / `repoUrl` 属性 | 低 |
| Store | NebulaSchema CodeEntity Tag 加 `repoId` 字段 | 低 |
| Store | 查询自动注入 `WHERE repoId == $repoId` | 中（需改 query 拦截层） |
| API | 新增 `POST /api/build` + `POST /api/repo/clone` | 低 |
| 权限 | 应用层 RBAC（space 级无隔离，需自建 user-repo 映射表） | 高 |
| 前端 | 同方案 A | 中 |

**优点**：
- 改动最小（加属性 + 查询过滤）
- 跨仓库查询天然支持（同一 space 内 MATCH）
- 无 space 数量上限问题
- 已有图遍历能力（GO FROM / LOOKUP ON）不需改

**缺点**：
- 物理隔离弱：一个误查（忘加 WHERE）就跨仓库了
- 权限隔离最复杂：NebulaGraph Space RBAC 帮不上忙，必须在应用层做 user→repo 映射
- 大规模时性能下降（一个 space 里几十万节点，索引选择性降低）
- VID 冲突风险：两个仓库的 `foo()` 函数如果 file_path 相同会产生 VID 碰撞（当前 hash = name+type+file_path）

**适用场景**：仓库数少（<10），弱隔离需求（单团队内部）

---

### 方案 C：混合模式 — Space 分组 + 标签路由

**核心思路**：按"团队"或"项目"分 Space，同组内多仓库共存（标签隔离），跨组物理隔离。

```
space: ontoagent_team_alpha    ← 团队A 的 3 个仓库（repoId 属性隔离）
space: ontoagent_team_beta     ← 团队B 的 5 个仓库
space: ontoagent_global        ← 跨团队关系（公开 API、共享概念等）
```

**改动量**：方案 A + 方案 B 的并集，但每块都简化：
- Space 粒度从"每仓库"放宽到"每团队/项目"
- Space 内用 repoId 属性区分仓库
- 权限用 NebulaGraph Space RBAC（团队级） + 应用层 repo 级 ACL

**优点**：
- Space 数量可控（= 团队数，通常 <20）
- 团队级 RBAC 用 NebulaGraph 原生能力
- 同团队跨仓库查询走同一 space（性能好）
- 跨团队查询走 global space

**缺点**：
- 复杂度最高（两种隔离模式并存）
- 团队 → 仓库映射的运维复杂
- 查询路由层需要判断"同团队还是跨团队"

**适用场景**：中型团队（10-50 仓库），需要团队级 RBAC + 仓库级 ACL

---

### 方案 D：Virtual Graph 抽象层（应用层路由）

**核心思路**：不改 NebulaGraph Space 结构，在 GraphStore 之上加一个 `MultiRepoGraphStore`，封装"多仓库路由"逻辑。

```python
class MultiRepoGraphStore(GraphStore):
    """多仓库图存储路由器。
    
    内部维护 {repo_id: GraphStore} 映射。
    每次操作根据 repo_id 路由到对应的底层 store 实例。
    """
    def __init__(self):
        self._stores: dict[str, NebulaGraphStore] = {}
    
    def register_repo(self, repo_id: str, space: str): ...
    def merge_node(self, label, properties) -> dict:
        repo_id = properties.get("repoId")
        return self._stores[repo_id].merge_node(label, properties)
```

**改动量**：

| 层 | 改动 | 复杂度 |
|----|------|--------|
| Store | 新增 `MultiRepoGraphStore`（GraphStore 子类） | 中 |
| Store | 每仓库一个底层 store 实例（连接池共享） | 低 |
| API | 同上 | 低 |
| 权限 | repo_id 驱动的路由 = 天然 ACL（用户只能访问授权的 repo_id） | 中 |

**优点**：
- 对现有代码侵入最小（只改 store 层，pipeline/agent 不动）
- 路由逻辑在应用层，后端可以是 Neo4j 也可以是 NebulaGraph
- 权限隔离自然（repo_id 路由 = ACL）
- 仓库增删 = store 实例注册/注销

**缺点**：
- 底层仍然是多 space（或连接池开销）
- 跨仓库查询需要显式跨 store 调用（没有原生跨 space JOIN）
- 每个 repo 一个 store 实例 → 连接池数量膨胀

**适用场景**：需要同时支持 Neo4j 和 NebulaGraph 双后端的多仓库

---

### 方案 E：最小可行方案（MVP 快速落地）

**核心思路**：不改架构，只补"能用的缺口"，先让基本流程跑通。

| 需求 | 改动 | 工作量 |
|------|------|--------|
| Web 构建 | 加 `POST /api/build` 端点，调 `OntoAgentBuilder.build` | 1天 |
| Git 拉取 | `POST /api/build` 接收 `repo_url` → `git clone` 到临时目录 → build | 0.5天 |
| 多仓库 | CodeEntity 加 `repoId` 属性，build 时注入 | 1天 |
| 仓库打标 | 新增 `RepositoryEntity`（name, url, branch, commit, built_at） | 0.5天 |
| 权限隔离 | **暂不做**，留 API Key 全局认证 | 0 |

**优点**：快速验证产品价值，一周可用
**缺点**：权限隔离缺失，多仓库靠属性过滤（脆弱）

---

## 三、Hermes 推荐排序

| 优先级 | 方案 | 理由 |
|--------|------|------|
| ⭐ 推荐 | **方案 B（标签属性）→ 方案 C（混合）演进路径** | 先用 repoId 属性快速上多仓库（改动最小），验证后再按需升级到 Space 分组 |
| 备选 | **方案 D（Virtual Graph）** | 如果未来要支持 Neo4j 多仓库，这个抽象最干净 |
| 快速验证 | **方案 E（MVP）** | 如果只是想先跑通"输入 URL → 出图"的演示 |
| 不推荐 | **方案 A（纯多 Space）** | Space 管理复杂度高，且对现有代码侵入大 |

## 四、关键设计决策

### 4.1 VID 碰撞风险

当前 VID = `_stable_id(name, entity_type, file_path)`。如果两个仓库有相同 file_path + 同名函数，VID 会碰撞。

**解决**：VID hash 输入加 `repo_id`：
```python
# 改前
vid = _stable_id(name, entity_type, file_path, start_line, end_line)
# 改后  
vid = _stable_id(repo_id, name, entity_type, file_path, start_line, end_line)
```

这意味着 schema version 升级 + 数据迁移。

### 4.2 跨仓库关系建模

不管哪个方案，跨仓库关系（如服务A调用服务B的API）都需要建模：

```
Repository --DEPENDS_ON--> Repository   （仓库级依赖）
ServiceEntity --SERVICE_DEPENDS_ON--> ServiceEntity  （服务级依赖，已有）
```

跨仓库关系存在"全局 space"（方案 A/C）或"同一 space 内跨 repoId"（方案 B）。

### 4.3 Git Clone 安全

Web 端接收 Git URL 需要安全措施：
- URL 白名单（只允许 github.com / gitee.com / 内网 GitLab 域名）
- clone 深度限制（`--depth 1` 避免拉全量历史）
- 超时控制（大仓库 clone 超时）
- 临时目录隔离 + 用后清理
- 私有仓库支持（SSH key / token 注入）

### 4.4 异步构建

Web 端 build 是长时间操作（几十秒到几分钟），必须异步：
- `POST /api/build` 返回 `task_id`
- 后台任务执行 build（Butler EventBus 已有基础设施）
- `GET /api/build/status/{task_id}` 轮询状态
- WebSocket / SSE 推送进度（前端已有 SSE 基础）

## 五、各方案与 OntoAgent 当前架构的兼容性

| 约束 | A（多Space） | B（标签） | C（混合） | D（Virtual） | E（MVP） |
|------|-------------|-----------|-----------|-------------|----------|
| 改动 GraphStore ABC | 需要 | 不需要 | 需要 | 需要 | 不需要 |
| 改动 pipeline/builder | 中 | 小 | 中 | 小 | 小 |
| 改动 schema version | 需要 | 需要 | 需要 | 不需要 | 需要 |
| 支持 NebulaGraph RBAC | ✅ 原生 | ❌ | ✅ 部分 | ❌ | ❌ |
| 跨仓库查询 | 应用层合并 | 同space MATCH | 同space MATCH | 跨store | 同space MATCH |
| 连接池开销 | 高(N×pool) | 无 | 中(T×pool) | 高(N×store) | 无 |
| 数据迁移成本 | 高 | 中(加属性) | 高 | 低 | 中(加属性) |
