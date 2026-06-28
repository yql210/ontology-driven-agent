# OntoAgent 源码解读手册

> **版本**: 基于 `src/ontoagent/` 实测源码（2026-06） | **总行数**: ~15,000 行 Python + ~200 行 YAML | **实体**: 11 | **关系**: 21 | **工具**: 10

---

## 1. 项目总览

### 1.1 项目定位

OntoAgent 是基于**本体驱动的可更新知识图谱引擎**。它从源代码（Python/Java）+ 文档自动构建知识图谱（Neo4j 5.x + ChromaDB），通过 LangGraph ReAct Agent 提供自然语言查询、变更影响分析、增量更新能力，并以 FastAPI + SSE 暴露 Web API，前端为 Vue3 可视化图谱。

### 1.2 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 语言 | Python 3.13+ | `from __future__ import annotations` |
| 包管理 | uv | 无 pip，使用 PEP 668 兼容 venv |
| AST 解析 | tree-sitter (Python + Java) | `parsing/parser/` |
| 图数据库 | Neo4j 5.x | 结构实体 + 本体约束 |
| 向量存储 | ChromaDB | 语义搜索 embedding（Ollama） |
| Agent 框架 | LangGraph | ReAct 模式，MemorySaver 持久化 |
| Web API | FastAPI + SSE | `api/web/app.py` (55 行) |
| 前端 | Vue3 + Vite + TypeScript | cytoscape 图谱可视化 |
| CLI | Click | `api/cli.py` (399 行) |
| MCP | FastMCP | `api/mcp_server.py` (360 行) |
| 静态检查 | ruff + pyright | 提交前强制通过 |
| Butler 引擎 | 事件驱动 | EventBus + Handler + GitWatcher |

### 1.3 核心设计原则

- **分层单向依赖**: Intent → Control → Capability → Semantic，每层只依赖下一层，不跨层不反向
- **Action 只引用 Function 名**，不引用实现细节
- **Function 通过 `graph_store` 操作语义层**，通过 `Connector` 访问外部
- **所有 Cypher 参数化**，禁止字符串拼接，避免注入
- **约束框架三层加载**: 本体注册表（Layer1）→ YAML 遍历路径（Layer2）→ 覆盖合并（Layer3）

---

## 2. 项目目录结构

以下为 `src/ontoagent/` 完整目录树（Python 源文件 + YAML 配置），含实际行数标注。

```
src/ontoagent/
├── __init__.py                       #  78 行 — 包入口
├── config.py                         # 150 行 — OntoAgentConfig 配置数据类
│
├── config/                           # 4 个 YAML 配置文件
│   ├── approval_policy.yaml          #  27 行 — 审批策略链配置
│   ├── function_danger_levels.yaml   #  19 行 — Function 危险级别
│   ├── tool_gateway.yaml             #  16 行 — Cypher 写操作拦截规则
│   └── constraint_overrides.yaml     #  28 行 — 约束覆盖（Layer3）
│
├── domain/                           # 领域模型层（Semantic 层核心）
│   ├── __init__.py                   #   0 行
│   ├── schema.py                     # 723 行 — 11 实体 + 21 关系 + 约束注册表
│   ├── constraints.py                #  38 行 — GuardLevel/GuardDecision/TraversalConstraint
│   ├── ontology_constraints.py       #  19 行 — ConstraintFieldDescriptor
│   ├── approval.py                   #  72 行 — ApprovalContext/Decision/PendingApproval
│   ├── exceptions.py                 #  29 行 — OntoAgentError 异常体系
│   └── provenance.py                 #  65 行 — 溯源置信度标记
│
├── store/                            # 存储层抽象 + Neo4j + ChromaDB
│   ├── __init__.py                   #   0 行
│   ├── graph_store.py                # 122 行 — GraphStore 抽象接口
│   ├── neo4j_store.py                # 572 行 — Neo4jGraphStore 实现
│   ├── chroma_store.py               # 414 行 — ChromaStore 向量存储
│   ├── schema_version.py             #  99 行 — Schema 版本检查
│   └── migrations/                   # Migration 框架
│       ├── __init__.py               #  28 行
│       ├── registry.py               #  89 行 — MigrationRegistry
│       ├── runner.py                 # 134 行 — MigrationRunner
│       ├── v1_1_0_add_business_entities.py  #  36 行
│       └── v1_2_0_add_cross_service_relations.py  #  38 行
│
├── parsing/                          # 解析与提取层
│   ├── __init__.py                   #   0 行
│   ├── parser/
│   │   ├── __init__.py               #  16 行
│   │   ├── base.py                   #  82 行 — BaseParser 抽象
│   │   ├── python_parser.py          # 880 行 — tree-sitter Python 解析器
│   │   ├── java_parser.py            #1217 行 — tree-sitter Java 解析器
│   │   └── doc_parser.py             # 193 行 — 文档解析器
│   └── extractor/
│       ├── __init__.py               #  25 行
│       ├── relation.py               # 160 行 — RelationExtractor 结构关系
│       ├── semantic.py               # 519 行 — SemanticExtractor LLM 语义提取
│       ├── external_calls.py         # 213 行 — 外部调用提取
│       └── entry_point_rules.py      # 153 行 — 入口点规则
│
├── pipeline/                         # 构建流水线 + 增量更新
│   ├── __init__.py                   #   0 行
│   ├── builder.py                    #1159 行 — OntoAgentBuilder 5 阶段构建
│   ├── builder_utils.py              # 241 行 — 构建工具函数
│   ├── semantic_linker.py            # 425 行 — 语义链接器（doc/code/concept）
│   ├── aligner.py                    # 370 行 — ConceptAligner 概念对齐四步法
│   ├── module_clustering.py          # 434 行 — ModuleClustering 模块聚类
│   ├── incremental_updater.py        # 663 行 — IncrementalUpdater 增量更新
│   ├── change_detector.py            # 443 行 — ChangeDetector git diff 变更检测
│   ├── impact_propagator.py          # 436 行 — ImpactPropagator 双向 BFS 影响传播
│   ├── service_linker.py             #  36 行 — 跨服务关系构建
│   ├── topic_linker.py               #  68 行 — 消息主题关系构建
│   ├── data_mapper.py                #  22 行 — 数据映射
│   ├── business_loader.py            #  23 行 — 业务本体加载
│   ├── ontology_actions.yaml         #  69 行 — Action 意图定义
│   ├── constraints.yaml              #  23 行 — 遍历约束路径（Layer2）
│   └── business_ontology.yaml        #  49 行 — 业务本体 YAML
│
├── execution/                        # 执行层（Control + Capability）
│   ├── __init__.py                   #   0 行
│   ├── action_executor.py            # 132 行 — ActionExecutor 控制层核心
│   ├── action_types.py               #  67 行 — ActionConfig/ActionDefinition
│   ├── intent_router.py              #  45 行 — IntentRouter 意图路由
│   ├── function_runner.py            #  98 行 — FunctionRunner 带重试/熔断
│   ├── circuit_breaker.py            #  38 行 — CircuitBreaker 熔断器
│   ├── execution_policy.py           #  15 行 — 执行策略
│   ├── saga.py                       # 134 行 — SAGA 分布式事务
│   ├── transaction_manager.py        #  97 行 — TransactionManager
│   ├── connectors/
│   │   ├── __init__.py               #   5 行
│   │   ├── base.py                   #  44 行 — Connector 抽象
│   │   └── mock_connector.py         #  27 行 — MockConnector
│   ├── functions/
│   │   ├── __init__.py               #   6 行
│   │   ├── registry.py               #  91 行 — FunctionRegistry 注册表
│   │   ├── builtin.py                # 216 行 — 内置 Function
│   │   ├── general.py                # 118 行 — 通用 Function
│   │   ├── check_compliance.py       #  53 行 — 合规检查 Function
│   │   └── trace_business_impact.py  #  64 行 — 业务影响追踪 Function
│   └── constraints/                  # ★ 约束框架子包（7 个模块）
│       ├── __init__.py               #  70 行 — 公共导出 + aggregate_levels
│       ├── loader.py                 # 161 行 — OntologyConstraintLoader 三层加载
│       ├── engine.py                 # 198 行 — ConstraintEngine 约束评估
│       ├── propagator.py             # 162 行 — ConstraintPropagator BFS 传播
│       ├── guards.py                 # 195 行 — 5 个 Guard 实现
│       ├── guard_pipeline.py         #  77 行 — ActionGuardPipeline 可插拔链
│       ├── approval_gate.py          # 141 行 — ApprovalGate 集中审批引擎
│       └── policies.py               # 187 行 — 3 种审批策略
│
├── agent/                            # Agent 层（Intent 层核心）
│   ├── __init__.py                   #   5 行
│   ├── graph.py                      # 308 行 — LangGraph ReAct 状态图
│   ├── tools.py                      # 798 行 — 10 个 LangChain Tool
│   ├── prompt.py                     #  89 行 — AGENT_SYSTEM_PROMPT 动态生成
│   ├── trace.py                      # 295 行 — TraceCollector 追踪收集器
│   ├── tool_gateway.py               #  46 行 — Cypher 写操作正则拦截
│   └── _helpers.py                   # 101 行 — 内部辅助函数
│
├── api/                              # 对外接口层
│   ├── __init__.py                   #   0 行
│   ├── cli.py                        # 399 行 — Click CLI 入口
│   ├── mcp_server.py                 # 360 行 — FastMCP MCP Server
│   └── web/
│       ├── __init__.py               #   0 行
│       ├── app.py                    #  55 行 — FastAPI 应用工厂
│       └── router/
│           ├── __init__.py           #   0 行
│           ├── chat.py               # 209 行 — /chat + /chat/stream + 审批端点
│           ├── graph.py              # 166 行 — /graph + /graph/stats
│           └── trace.py              # 121 行 — /trace 观测端点
│
└── butler/                           # Butler 事件驱动知识管理引擎
    ├── __init__.py                   #   7 行
    ├── engine.py                     # 329 行 — ButlerEngine 主引擎
    ├── event_bus.py                  # 105 行 — EventBus 事件总线
    ├── scheduler.py                  # 124 行 — Scheduler 调度器
    ├── handlers/
    │   ├── __init__.py               #  12 行
    │   ├── base.py                   #  62 行 — Handler 抽象基类
    │   ├── knowledge_update.py       # 139 行 — 知识更新 Handler
    │   └── reflection.py             # 131 行 — 反思 Handler
    ├── watchers/
    │   ├── __init__.py               #   7 行
    │   └── git_watcher.py            # 171 行 — GitWatcher 文件变更监听
    ├── consistency/
    │   ├── __init__.py               #   7 行
    │   └── guard.py                  # 202 行 — 一致性检查 Guard
    └── skills/
        ├── __init__.py               #   7 行
        └── store.py                  # 470 行 — SkillStore 技能存储
```

**统计**: 约 98 个 Python 源文件 + 8 个 YAML 配置文件，总计约 15,000 行源码。

---

## 3. 架构分层

OntoAgent 遵循 DESIGN_V34 定义的四层架构，每层只依赖下一层：

```
┌─────────────────────────────────────────────────────┐
│ Intent（意图层）                                      │
│ agent/graph.py + tools.py + prompt.py                │
│ Agent 识别自然语言意图 → express_intent 路由到 Action   │
├─────────────────────────────────────────────────────┤
│ Control（控制层）                                     │
│ execution/action_executor.py + constraints/           │
│ ActionExecutor · Guard Pipeline · 审批 · SAGA         │
├─────────────────────────────────────────────────────┤
│ Capability（能力层）                                   │
│ execution/function_runner.py + functions/ + connectors/│
│ FunctionRunner（重试/熔断/并发）· 通用+领域 Function   │
├─────────────────────────────────────────────────────┤
│ Semantic（语义层）                                     │
│ domain/ + store/ + parsing/ + pipeline/               │
│ Schema（11 实体 21 关系）· GraphStore · 本体约束        │
└─────────────────────────────────────────────────────┘
```

**层间依赖规则**:
- Action 只引用 Function 名，不引用实现
- Function 通过 `graph_store` 操作语义层、通过 `Connector` 访问外部系统
- Connector 只搬运数据，不含业务逻辑
- 不跨层、不反向依赖

---

## 4. 领域模型详述

### 4.1 `domain/schema.py`（723 行）— 11 实体 + 21 关系

#### 核心实体（11 个 `@dataclass`）

| # | 类名 | 行号 | 说明 |
|---|------|------|------|
| 1 | `CodeEntity` | 13 | 代码实体（function/class/interface/module/file/enum/record/field） |
| 2 | `ConceptEntity` | 66 | 概念实体（business_concept/design_pattern/api_contract/data_model/process/message_topic） |
| 3 | `DataAsset` | 105 | 数据资产（sensitivity: public/internal/confidential/restricted） |
| 4 | `DocEntity` | 144 | 文档实体（readme/module_doc/api_doc/comment/wiki/architecture_doc） |
| 5 | `ResourceEntity` | 185 | 资源实体（image/diagram/pdf/config/schema_file/log） |
| 6 | `ModuleEntity` | 217 | 功能模块（聚类结果） |
| 7 | `ChangeSetEntity` | 239 | 变更集（Git commit 绑定） |
| 8 | `LogEntity` | 271 | 日志实体（ERROR/WARN/INFO/DEBUG） |
| 9 | `AlertEntity` | 309 | 告警实体（error_spike/latency/service_down/custom） |
| 10 | `ServiceEntity` | 354 | 服务实体（running/stopped/degraded） |
| 11 | `ComplianceItem` | 392 | 合规要求（GDPR/SOX/PCI-DSS） |

所有实体均使用 `@dataclass` + `__post_init__()` 校验，包含 `id: str = uuid4()` 自动生成标识。

#### 辅助数据类

| 类名 | 行号 | 说明 |
|------|------|------|
| `Relation` | 494 | 实体间关系（source_id/target_id/relation_type/weight） |
| `RelationConstraint` | 526 | 关系约束（domain/range/cardinality） |

#### 关系类型（21 种，`VALID_RELATION_TYPES`，第 442 行）

**结构关系（5）**: `CALLS` `EXTENDS` `IMPLEMENTS` `IMPORTS` `CONTAINS`

**语义关系（4）**: `SEMANTIC_IMPACT` `DESCRIBES` `ILLUSTRATES` `DERIVED_FROM`

**变更关系（2）**: `CHANGED_IN` `AFFECTS`

**运维关系（4）**: `TRIGGERED_BY` `LOGS_FROM` `RUNS_AS` `SERVICE_DEPENDS_ON`

**数据与合规（4）**: `PROCESSES_DATA` `SUBJECT_TO` `GOVERNED_BY` `CALLS_SERVICE`

**消息（2）**: `PUBLISHES_TO` `CONSUMED_BY`

全部关系在 `RELATION_TYPE_TO_NEO4J`（第 468 行）中映射为 Neo4j 大写标签。

#### 本体约束注册表（`ONTOLOGY_CONSTRAINT_REGISTRY`，第 693 行）

```python
ONTOLOGY_CONSTRAINT_REGISTRY = {
    "DataAsset.sensitivity": ConstraintFieldDescriptor(
        field_name="sensitivity",
        value_mapping={"restricted": BLOCK, "confidential": WARN, "internal": ALLOW, "public": ALLOW},
    ),
    "ComplianceItem.severity": ConstraintFieldDescriptor(
        field_name="severity",
        value_mapping={"critical": BLOCK, "high": WARN, "medium": ALLOW, "low": ALLOW},
    ),
    "CodeEntity.entry_category": ConstraintFieldDescriptor(
        field_name="entry_category",
        value_mapping={"http_api": WARN, "rpc_service": WARN, "scheduled": ALLOW, "mq_consumer": ALLOW, "event_handler": ALLOW},
        neo4j_property="entryCategory",
    ),
}
```

### 4.2 `domain/constraints.py`（38 行）

约束框架类型定义（从 schema.py 独立拆分以控制行数）：

- `GuardLevel(StrEnum)`: `ALLOW` / `WARN` / `BLOCK`
- `GuardDecision`: `level` + `reason` + `details`
- `TraversalConstraint`: 通用遍历约束（`source_label` → `relation_chain` → `target_label` → `collect_property` → `value_mapping`）

### 4.3 `domain/ontology_constraints.py`（19 行）

`ConstraintFieldDescriptor`: 描述实体字段的约束语义——字段名、值→级别映射、Neo4j 属性名。

### 4.4 `domain/approval.py`（72 行）

审批域对象：

- `DecisionLevel`: `APPROVED` / `PENDING` / `DENIED`
- `PolicyResult`: 单策略评估结果
- `ApprovalDecision`: 审批门总决策（含 token）
- `ApprovalContext`: 审批上下文（intent_type + target + params + entity + guard_checks）
- `PendingApproval`: 待审批记录（含 TTL 过期机制）
- `generate_token()`: SHA-256 生成 12 位唯一审批令牌

---

## 5. 约束框架（`execution/constraints/`）

约束框架是整个系统的安全核心，由 7 个模块组成，共计约 990 行。

### 5.1 `loader.py`（161 行）— `OntologyConstraintLoader`

**三层加载机制**:

1. **Layer 1 — 本体注册表**: 从 `ONTOLOGY_CONSTRAINT_REGISTRY`（schema.py:693）自动填充 `value_mapping`
2. **Layer 2 — YAML 遍历路径**: 读取 `pipeline/constraints.yaml` 定义的 `traversal_constraints` 和 `propagation_rules`
3. **Layer 3 — 覆盖合并**: 读取 `config/constraint_overrides.yaml`，支持三种覆盖操作：
   - `patch`: 局部修改已有约束的 value_mapping
   - `allow_all`: 单点白名单（`"Label:name"` 格式）
   - `add_constraint`: 追加额外约束

**核心方法**: `load_all()` 返回四元组 `(traversals, propagation_rules, warnings, allow_set)`。

### 5.2 `engine.py`（198 行）— `ConstraintEngine`

执行遍历约束检查。构造时校验 `relation_chain` 合法性（relation type 正则白名单 + 域名/范围匹配 `RELATION_CONSTRAINTS`）。

`evaluate(entity_id, constraint_name)` 三步走：
1. 获取源实体 → 2. 沿关系链遍历收集目标属性 → 3. 聚合为 GuardDecision

支持三种聚合策略：`max`（最严格）、`min`（最宽松）、`exists`（任意非 ALLOW 则 WARN）。

### 5.3 `propagator.py`（162 行）— `ConstraintPropagator`

BFS 约束属性传播器。`propagate(entity_id, rule)` 沿关系链传播并收集属性值。支持 `forward`/`backward` 方向，`max_depth` 控制深度（0 表示仅检查自身）。

`PropagationRule` 数据类定义了传播规则：`along`（关系列表）、`collect_property`、`value_mapping`、`direction`、`max_depth`、`aggregation`。

额外提供 `find_entry_points()` 方法反向查找入口点（带 `entryCategory` 的 CodeEntity）。

### 5.4 `guards.py`（195 行）— 5 个 Guard

| Guard | 职责 |
|-------|------|
| `WhitelistGuard` | 检查实体是否在白名单中（`"Label:name"` 格式匹配），命中则放行 |
| `EntityExistsGuard` | 检查 `entity["id"]` 是否存在，不存在则 BLOCK |
| `EntityPropertyGuard` | 评估 `submission_criteria` 表达式（如 `entity.lines > 100`），支持 `> >= < <= == !=` |
| `OntologyTraversalGuard` | 委托 `ConstraintEngine` 执行遍历约束，返回最严重决策 |
| `OntologyPropagationGuard` | 委托 `ConstraintPropagator` 执行传播规则，BLOCK 级直接拦截 |

所有 Guard 实现 `ActionGuard` 抽象接口（`evaluate(config, entity, graph_store) → GuardDecision`）。

### 5.5 `guard_pipeline.py`（77 行）— `ActionGuardPipeline`

可插拔 Guard 链。顺序执行，遇 BLOCK 立即返回，WARN 累积，ALLOW 继续。

推荐顺序（廉价→昂贵）：EntityExistsGuard → EntityPropertyGuard → OntologyTraversalGuard → OntologyPropagationGuard。

`check()` 返回 `(block_reason | None, warnings: list[str])`。

### 5.6 `approval_gate.py`（141 行）— `ApprovalGate`

集中审批引擎（详见 §6）。

### 5.7 `policies.py`（187 行）— 3 种审批策略

详见 §6.2。

---

## 6. 审批引擎

### 6.1 `approval_gate.py`（141 行）— `ApprovalGate`

集中审批引擎，核心流程：

```
check(context) → 遍历策略链
  ├─ 任一策略返回 DENIED → 立即拒绝
  ├─ 有 PENDING → 生成令牌 + 审计 → 返回 PENDING
  └─ 全部 APPROVED → 放行
```

**令牌管理**:
- `generate_token()`: SHA-256 生成 12 位唯一令牌（绑定 intent_type + target + session_id）
- `PendingApproval`: TTL 机制（默认 600 秒），`is_expired` 自动检测
- `max_pending`: 同时最多 10 个待审批令牌，超限拒绝
- `cleanup_expired()`: 清理过期令牌

**审计日志**: `_audit_log` 记录每次决策的时间戳、操作、意图、目标、令牌、审批结果。

**`resolve(token, approved)`**: 一次性消费令牌，通过返回 `ApprovalContext`，拒绝/过期返回 `None`。

### 6.2 `policies.py`（187 行）— 3 种审批策略

| 策略 | 类名 | 逻辑 |
|------|------|------|
| Guard 结果策略 | `GuardResultPolicy` | 读取 Guard Pipeline 结果：`on_block`→`require_approval`/`auto_reject`；`on_warn`→`require_approval`/`auto_allow` |
| Action 审批策略 | `ActionApprovalPolicy` | 检查 `ActionConfig.requires_approval` 字段，标记为 True 则 PENDING |
| Function 危险策略 | `FunctionDangerPolicy` | 根据 function 的 `danger_level` 决定：`read` 自动放行，`read_sensitive`/`write`/`admin` 需要审批 |

配置来源：`config/approval_policy.yaml` 控制策略启用顺序和参数，`config/function_danger_levels.yaml` 定义各 function 的危险级别（默认 `read`，12 个已配置 function 覆盖）。

---

## 7. 构建流水线（`pipeline/builder.py`，1159 行）

`OntoAgentBuilder.build()` 执行多阶段管线（第 476 行起），前两个阶段为关键路径（失败 `aborted=True`），后续可降级跳过。

| 阶段 | 方法 | 说明 | 失败行为 |
|------|------|------|---------|
| **Stage 1** | `_stage_parse()` (L215) | tree-sitter 扫描解析（Python+Java+doc）+ 结构关系提取 + Service/Topic 聚合 | 关键 |
| **Stage 2** | `_stage_structural_write()` (L275) | 结构实体 + 关系 MERGE 写入 Neo4j（`label` 优化: ENTITY_TYPE_TO_LABEL） | 关键（`RuntimeError`） |
| **Stage 2.5** | `link_docs_to_code()` (L673) | 文档→代码 `DESCRIBES` 关联 | 关键 |
| **Stage 2.6** | `_stage_business_ontology()` (L694) | 业务本体 YAML → DataAsset + ComplianceItem + PROCESSES_DATA | 关键 |
| **Stage 3** | `_stage_semantic()` (L763) | LLM 语义提取 → `ConceptAligner` 四步对齐（精确→别名→向量→图结构） → 写 ConceptEntity | 可降级 |
| **Stage 4** | `_stage_clustering()` (L780) | `ModuleClustering.detect_modules()` → ModuleEntity | 可降级 |
| **Stage 5** | `_write_all_vectors()` (L796) | Code/Doc/Concept/Module 实体向量写入 ChromaDB（Ollama embedding） | 可降级 |

**增量更新**: `pipeline/incremental_updater.py`（663 行）基于 `change_detector.py`（443 行，git diff 检测）和 `impact_propagator.py`（436 行，双向 BFS 影响传播）实现。

---

## 8. 存储层

### 8.1 `store/graph_store.py`（122 行）

`GraphStore` 抽象接口，定义 `query()`、`get_node()`、`get_relations()`、`create_entity()`、`create_relation()` 等基础操作。

### 8.2 `store/neo4j_store.py`（572 行）

`Neo4jGraphStore` — 基于 Neo4j Python Driver 的实现。核心方法：

- `merge_code_entity()`: MERGE 实体，自动加 label，返回 entity_id
- `merge_relation()`: MERGE 关系，含 label 优化
- `query()`: 参数化 Cypher 查询
- `create_indexes()`: 自动创建索引（id, name, entityType, filePath 等 14 个）
- `clear_database()`: 清空数据库
- 连接管理: `close()` / 上下文管理器

### 8.3 `store/chroma_store.py`（414 行）

`ChromaStore` — ChromaDB 向量存储。核心方法：

- `put_entities_batch()`: 批量写入 entity → embedding（Ollama API）
- `search()`: 语义搜索，支持 `entity_type` 过滤
- `delete_collection()` + `reset()`: 重建 collection
- `get_embedding()`: 获取单文本向量

### 8.4 Migration 框架

`store/schema_version.py` (99 行) 检查 schema 版本兼容性。`store/migrations/` 提供版本化迁移（Registry + Runner），当前版本 v1.2.0。

---

## 9. Agent 层

### 9.1 `agent/graph.py`（308 行）— LangGraph ReAct Agent

核心组件：
- **`AgentState`**: 继承 `MessagesState`，仅含 messages
- **全局单例**: `_checkpointer`（MemorySaver）和 `_llm`（ChatOpenAI 智谱兼容接口）
- **`build_graph()`**: 构建 `StateGraph`（START → agent → tools_condition → tools | END）
- **`run_query()`**: 同步对话入口，thread_id 支持记忆持久化
- **`run_query_stream()`**: 异步 SSE 流式输出，处理 `express_intent` 返回的 `server_action` 令牌

### 9.2 `agent/tools.py`（798 行）— 10 个 LangChain Tool

| # | 工具名 | 行号 | 功能 |
|---|--------|------|------|
| 1 | `semantic_search` | 26 | 语义搜索代码片段（ChromaDB 向量） |
| 2 | `graph_query` | 51 | 执行 Cypher 图查询（经 tool_gateway 拦截） |
| 3 | `impact_analysis` | 97 | 变更影响范围分析（图传播） |
| 4 | `get_context` | 152 | 查实体详情（属性+关系+相似实体） |
| 5 | `list_concepts` | 211 | 列出概念实体 |
| 6 | `get_module_tree` | 233 | 模块结构树 |
| 7 | `detect_changes` | 286 | 检测 Git 代码变更 |
| 8 | `export_graph` | 340 | 导出可视化数据 |
| 9 | `express_intent` | 382 | ★ 执行操作（经 ActionExecutor + ApprovalGate） |
| 10 | `check_operation` | 555 | 预查操作约束状态（干跑 constraint check） |

`express_intent` 是核心操作工具：内部调用 `ActionExecutor.execute()`，经过 Guard Pipeline → ApprovalGate → FunctionRunner 完整链路，支持返回 `server_action.approval_required` 触发审批流。

`ALL_TOOLS` 列表（第 787 行）在 `graph.py` 中被注入 `ToolNode`。

### 9.3 `agent/prompt.py`（89 行）

动态生成的 `AGENT_SYSTEM_PROMPT`:
- 从 `pipeline/ontology_actions.yaml` 自动构建意图路由段（`build_intent_prompt()`）
- 从 `ONTOLOGY_CONSTRAINT_REGISTRY` 自动生成约束提示表（仅显示非 ALLOW 约束）
- 包含完整工具速查表、Schema 速查（9 实体 15 关系）、常用 Cypher 模板

### 9.4 `agent/trace.py`（295 行）

`TraceCollector` — 线程安全的 Agent 可观测性收集器。使用 SQLite 持久化（`.traces.db`），核心数据模型：

- `TraceStep`: 单步记录（type: thinking/tool_call/tool_result/final/approval_required/approval_resolved）
- `TraceLog`: 完整会话记录，含 `approval_token`、`approval_status`、`parent_trace_thread_id`

支持内存 LRU（max 500 traces, 3600s 过期）+ SQLite 持久化双写。

### 9.5 `agent/tool_gateway.py`（46 行）

Cypher 写操作拦截网关。通过正则匹配拦截 `graph_query` 工具中的写操作关键字（SET/DELETE/REMOVE/CREATE/MERGE/DROP/DETACH DELETE/FOREACH/CALL apoc），写操作被重定向到 `express_intent`。

配置来源：`config/tool_gateway.yaml`，可开关和自定义关键字列表。

---

## 10. Web API

### 10.1 `api/web/app.py`（55 行）

FastAPI 应用工厂 `create_app()`:
- CORS 中间件（环境变量 `CORS_ORIGINS`，默认 `localhost:5173`）
- 注入 `TraceCollector` 单例到 chat/trace router
- 挂载三个路由：`/api/chat`、`/api/graph`、`/api/trace`
- `/health` 健康检查端点
- `lifespan` 管理 Neo4j 连接生命周期

### 10.2 `api/web/router/chat.py`（209 行）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat` | POST | 同步对话（`run_query`） |
| `/api/chat/stream` | POST | SSE 流式对话（`run_query_stream`），处理 `approval_required` 事件 |
| `/api/chat/approve` | POST | 审批决议端点（接收 `approval_id` + `approved`） |

数据模型：`ChatRequest`、`ChatResponse`、`ApprovalRequest`、`ApprovalResponse`。

### 10.3 `api/web/router/graph.py`（166 行）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/graph/stats` | GET | 图谱统计（节点数/边数/按类型分布） |
| `/api/graph` | GET | 图谱数据查询（支持 center+depth+limit） |
| `/api/graph/node/{node_id}` | GET | 节点详情（属性+关系） |

### 10.4 `api/web/router/trace.py`（121 行）

Trace 观测端点：`/api/trace/list`（列出所有 trace）、`/api/trace/{thread_id}`（单 trace 详情）。

### 10.5 CLI 与 MCP

- **`api/cli.py`**（399 行）：Click CLI，命令包括 `build`、`query`、`update`、`migrate`、`ask`、`serve`、`web`、`info`、`version`、`butler`
- **`api/mcp_server.py`**（360 行）：FastMCP Server，暴露 `build_knowledge_graph`、`semantic_search`、`query_graph`、`analyze_impact` 等 MCP 工具

---

## 11. 配置系统

### 11.1 `config.py`（150 行）

`OntoAgentConfig` 数据类，40+ 配置项，`from_env()` 工厂方法从环境变量自动加载（内置 `.env` 解析器，无外部依赖）。

环境变量前缀：`ONTOAGENT_`，关键配置项：
- Neo4j: `ONTOAGENT_NEO4J_URI/USER/PASSWORD`
- ChromaDB: `ONTOAGENT_CHROMA_DIR`
- Ollama: `ONTOAGENT_OLLAMA_URL`、`ONTOAGENT_EMBEDDING_MODEL`
- Agent LLM: `ONTOAGENT_AGENT_LLM_PROVIDER/MODEL/API_KEY/BASE_URL`
- Semantic LLM: `ONTOAGENT_SEMANTIC_LLM_PROVIDER/API_KEY/BASE_URL`
- Build: `ONTOAGENT_BUILD_INCLUDE_DOCS/DOC_EXTENSIONS/SKIP_DIRS`

### 11.2 YAML 配置关系

```
config/
├── approval_policy.yaml     ← 控制 ApprovalGate 策略链
│   policies: [guard_result, action_approval, function_danger]
│   guard_result.on_block: require_approval | auto_reject
│   function_danger.auto_approve: [read]
│   token.ttl: 600, token.max_pending: 10
│
├── function_danger_levels.yaml ← FunctionDangerPolicy 的数据源
│   default: read
│   functions: {check_refactor_eligibility: read, generate_api_doc: write, ...}
│
├── tool_gateway.yaml           ← tool_gateway.py 的拦截规则
│   enabled: true
│   blocked_keywords: [SET, DELETE, REMOVE, CREATE, MERGE, DROP, ...]
│
└── constraint_overrides.yaml   ← Layer 3 覆盖
    overrides: [{type: patch, target: data_sensitivity, modify: ...}, ...]
```

YAML 之间的数据流：
- `function_danger_levels.yaml` → `FunctionDangerPolicy` 实例化参数
- `approval_policy.yaml` → `ApprovalGate` 的策略链组装
- `tool_gateway.yaml` → `validate_graph_query()` 关键字列表
- `constraint_overrides.yaml` → `OntologyConstraintLoader.load_all()` 的 Layer 3

---

## 附录 A：关键行数速查

| 核心文件 | 行数 |
|---------|------|
| `domain/schema.py` | 723 |
| `pipeline/builder.py` | 1159 |
| `parsing/parser/java_parser.py` | 1217 |
| `parsing/parser/python_parser.py` | 880 |
| `agent/tools.py` | 798 |
| `pipeline/incremental_updater.py` | 663 |
| `store/neo4j_store.py` | 572 |
| `parsing/extractor/semantic.py` | 519 |
| `butler/skills/store.py` | 470 |
| `pipeline/change_detector.py` | 443 |
| `pipeline/impact_propagator.py` | 436 |
| `pipeline/module_clustering.py` | 434 |
| `pipeline/semantic_linker.py` | 425 |
| `store/chroma_store.py` | 414 |
| `api/cli.py` | 399 |
| `pipeline/aligner.py` | 370 |
| `api/mcp_server.py` | 360 |
| `butler/engine.py` | 329 |
| `agent/graph.py` | 308 |
| `agent/trace.py` | 295 |
| `pipeline/builder_utils.py` | 241 |
| `execution/functions/builtin.py` | 216 |
| `api/web/router/chat.py` | 209 |
| `butler/consistency/guard.py` | 202 |
| `execution/constraints/engine.py` | 198 |
| `execution/constraints/guards.py` | 195 |
| `execution/constraints/policies.py` | 187 |
| `execution/constraints/propagator.py` | 162 |
| `execution/constraints/loader.py` | 161 |
| `execution/constraints/approval_gate.py` | 141 |
| `config.py` | 150 |

## 附录 B：关键类/函数速查

| 目录 | 关键符号 | 行号 |
|------|---------|------|
| `domain/schema.py` | `CodeEntity`, `DataAsset`, `ComplianceItem`, `ONTOLOGY_CONSTRAINT_REGISTRY` | 13, 105, 392, 693 |
| `execution/constraints/loader.py` | `OntologyConstraintLoader.load_all()` | 16 |
| `execution/constraints/engine.py` | `ConstraintEngine.evaluate()` | 78 |
| `execution/constraints/propagator.py` | `ConstraintPropagator.propagate()` | 45 |
| `execution/constraints/guards.py` | `WhitelistGuard`, `EntityExistsGuard`, `EntityPropertyGuard`, `OntologyTraversalGuard`, `OntologyPropagationGuard` | 168, 18, 30, 88, 121 |
| `execution/constraints/guard_pipeline.py` | `ActionGuardPipeline.check()` | 61 |
| `execution/constraints/approval_gate.py` | `ApprovalGate.check()`, `resolve()` | 36, 76 |
| `execution/constraints/policies.py` | `GuardResultPolicy`, `ActionApprovalPolicy`, `FunctionDangerPolicy` | 23, 103, 132 |
| `agent/tools.py` | `ALL_TOOLS` (10 tools), `express_intent` | 787, 382 |
| `agent/tool_gateway.py` | `validate_graph_query()` | 41 |
| `agent/prompt.py` | `AGENT_SYSTEM_PROMPT` | 37 |
| `agent/graph.py` | `run_query()`, `run_query_stream()` | — |
| `pipeline/builder.py` | `OntoAgentBuilder.build()` | 476 |
| `api/cli.py` | `main` (Click group) | 18 |
| `api/mcp_server.py` | `mcp` (FastMCP) | 14 |
| `api/web/app.py` | `create_app()`, `lifespan()` | 26, 18 |
| `config.py` | `OntoAgentConfig.from_env()` | 96 |
