# OntoAgent — 本体驱动的 AI Agent 约束框架

<p align="center">
  <strong>图遍历即权限检查。</strong><br>
  约束 AI Agent 运行时能做什么——不只是能读什么。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/tests-1885-green.svg" alt="Tests">
  <img src="https://img.shields.io/badge/src-113%20files%2C%2023K%20LOC-orange.svg" alt="LOC">
  <img src="https://img.shields.io/badge/version-0.2.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg" alt="License">
</p>

---

## 为什么需要 OntoAgent？

当 LLM Agent 从"只读助手"进化为"自主执行体"——直接改代码、写数据库、触发部署时，一个全新的安全问题浮出水面：**谁来约束 Agent 的操作？**

传统权限体系（RBAC/ABAC）回答的是"谁能做什么"——它无法回答语义级问题：

> "这段代码处理银行卡号，Agent 是否有权重构它？"

答案不取决于"谁是操作者"（RBAC），不取决于"满足什么条件"（OPA），而取决于**"操作对象在语义上关联了什么数据"**。OntoAgent 把这些关联变成了权限系统的一等公民。

**核心思想：** 业务本体的关系链自动成为权限边界。沿着这些关系做图遍历，**就是**权限检查。

```
Agent 要 UPDATE CodeEntity "process_payment()"
    │
    ▼
┌─────────────────────────────────────────────┐
│  Shape 约束评估                              │
│  PROCESSES_DATA → DataAsset{sensitivity:    │
│    restricted}  ⟹  BLOCK                    │
└─────────────────────────────────────────────┘
    │
    ▼
  拒绝 —— 或升级为人工审批
```

---

## 架构

V3.4 四层架构——每层只依赖下一层，不跨层不反向。

```
┌─────────────────────────────────────────────────────────┐
│  意图层 — Agent 识别意图 → 调度 Action                   │
│  (LangGraph ReAct Agent + 8 个工具)                     │
├─────────────────────────────────────────────────────────┤
│  控制层 — Shape 约束 · 审批门 · 审计追踪                 │
│  (ShapeEvaluator + DecisionFuser + ApprovalGate)        │
├─────────────────────────────────────────────────────────┤
│  能力层 — Function 注册表 · FunctionRunner              │
│  (通用 + 领域 Function，重试/熔断)                      │
├─────────────────────────────────────────────────────────┤
│  语义层 — Schema · GraphStore · Shape 规则              │
│  (12 实体 · 29 关系 · Neo4j/NebulaGraph)               │
└─────────────────────────────────────────────────────────┘
```

### 意图 → 执行 完整链路

1. Agent 收到自然语言 → 通过触发提示匹配 `intent_type`。
2. Agent 调用 `express_intent(intent_type, target, params)`。
3. `ActionExecutor` 解析实体 → 检查提交条件 → 通过 `FunctionRunner` 调用对应 Function。
4. **执行前：** Shape 约束通过图遍历本体关系，评估操作上下文。
5. **决策：** `DENY` → 阻止；`WARN`/`BLOCK` → 升级到 `ApprovalGate`；`ALLOW` → 继续。
6. **执行后：** 完整审计轨迹记录。

---

## 三层约束系统

OntoAgent 的核心——每层增加一个控制维度：

| 层级 | 作用 | 配置来源 |
|------|------|---------|
| **第一层：本体推导** | 从实体关系自动推导约束（如 `CodeEntity --PROCESSES_DATA--> DataAsset`） | `business_ontology.yaml` + schema 注册表 |
| **第二层：覆盖** | 无需改代码即可 patch/白名单/追加约束 | `constraint_overrides.yaml` |
| **第三层：Shape 规则** | 声明式路径约束，支持严重度/优先级/建议 | `shapes.yaml` |

### Shape 规则示例

```yaml
- id: shape:sensitive_data
  name: 敏感数据保护
  description: "操作 restricted/confidential 数据的 CodeEntity 需要额外审查"
  target:
    entry_type: CodeEntity
    operation: UPDATE
  path: "PROCESSES_DATA -> DataAsset"       # 图遍历路径
  constraint:
    field: sensitivity
    operator: in
    value: [restricted, confidential]
  severity: block
  priority: 10
  suggestion: |
    该代码处理敏感数据。可选:
    (a) 降级关联 DataAsset 的 sensitivity 标签
    (b) 申请临时豁免（24h TTL）
    (c) 寻找不涉及敏感数据的替代方案
```

Shape 是**数据，不是代码**。框架运行时动态读取并评估。改一条规则 → 重载 YAML → 不需要重新部署。

### 双级审批

```yaml
# approval_policy.yaml — 三个策略源，按顺序执行
policies:
  - guard_result       # Shape 评估结果 → 审批决策
  - action_approval    # Action 配置标志
  - function_danger    # Function 危险级别 (read/write/admin)

function_danger:
  auto_approve: [read]
  require_approval: [read_sensitive, write, admin]
```

---

## 核心特性

- **自动知识图谱构建** — Tree-sitter AST 解析（Python + Java）+ LLM 语义提取 → Neo4j 或 NebulaGraph
- **增量更新** — git diff → 变更检测 → 双向 BFS 影响传播
- **概念对齐** — 四步对齐器（精确 → 别名 → 向量 → 图结构），解决术语漂移
- **Shape 约束引擎** — 声明式 YAML 规则，图路径遍历，严重度/优先级/标签系统
- **审批门** — 多策略链，令牌管理，TTL 过期，审计追踪
- **工具网关** — 拦截写操作（Cypher + nGQL 双方言感知），阻止未授权变更
- **多仓库支持** — 通过 VID `repo_id` 实现仓库隔离，按仓库访问控制
- **多后端** — Neo4j 和 NebulaGraph，统一 `GraphStore` 抽象 + 工厂模式
- **LangGraph Agent** — ReAct 智能体，8 个工具，支持复杂多步推理
- **Butler 引擎** — 事件驱动常驻引擎（EventBus + Handler + GitWatcher）
- **Web 界面** — Vue 3 + TypeScript，cytoscape 图谱可视化，SSE 流式对话，mermaid 流程图
- **生产就绪** — Docker 非 root 用户、健康检查、nginx 限流/HTTPS、Prometheus 指标、request_id 追踪的结构化日志

---

## 快速开始

```bash
# 克隆并安装
git clone https://gitee.com/sinxyql/ontology-driven-agent.git
cd ontology-driven-agent
uv sync

# 配置环境
cp .env.example .env
# 编辑 .env：设置图后端 (neo4j|nebula)、凭据、LLM API 密钥

# 从代码仓库构建知识图谱
uv run ontoagent build ./your-repo --clear

# 自然语言查询
uv run ontoagent query "处理用户认证的函数"

# 向 Agent 提问（LangGraph 多步推理）
uv run ontoagent ask "谁依赖了 merge_node()？"

# 启动 Web 界面 + API
uv run ontoagent web --port 8000
```

### Docker 部署

```bash
# 启动 Neo4j + ChromaDB + OntoAgent
docker compose up -d

# 在容器内构建图谱
docker compose run --rm ontoagent ontoagent build ./repo
```

---

## OntoAgent Schema

### 12 实体

| 实体 | 说明 |
|------|------|
| `CodeEntity` | function / class / interface / module / file / enum / record / field |
| `ConceptEntity` | business_concept / design_pattern / api_contract / data_model / process |
| `DocEntity` | readme / module_doc / api_doc / comment / wiki / architecture_doc |
| `ResourceEntity` | image / diagram / pdf / config / schema_file / log |
| `ModuleEntity` | 功能模块聚类结果 |
| `ChangeSetEntity` | Git commit 变更追踪 |
| `ServiceEntity` | 外部服务 / API |
| `RepositoryEntity` | 多仓库管理（v0.2.0+） |
| `DataAsset` | 业务数据资产（PII、金融、运营数据） |
| `ComplianceItem` | 合规要求（GDPR、SOX、PCI-DSS） |
| `CapabilityEntity` | 业务能力图谱 |
| `ProcessEntity` | 业务流程追踪 |

### 29 关系

| 分类 | 关系 |
|------|------|
| 结构（AST） | `CALLS` `EXTENDS` `IMPLEMENTS` `IMPORTS` `CONTAINS` |
| 语义（LLM） | `SEMANTIC_IMPACT` `DESCRIBES` `ILLUSTRATES` `DERIVED_FROM` |
| 变更 | `CHANGED_IN` `AFFECTS` `TRIGGERED_BY` |
| 业务 | `PROCESSES_DATA` `SUBJECT_TO` `GOVERNED_BY` `CALLS_SERVICE` `PUBLISHES_TO` `CONSUMED_BY` |
| 能力 | `PRODUCES` `CONSUMES` `COMPOSES_INTO` `REALIZED_BY` `PRECEDES` `EQUIVALENT_TO` |
| 服务 | `LOGS_FROM` `RUNS_AS` `SERVICE_DEPENDS_ON` |
| 多仓库 | `BELONGS_TO_REPO` `DEPENDS_ON_REPO` |

---

## CLI 命令

```bash
ontoagent build <repo>              # 全量构建（多阶段流水线）
ontoagent query <text>              # 语义搜索
ontoagent update <repo> --since <rev>  # 增量更新
ontoagent migrate                   # Schema 迁移
ontoagent ask <question>            # LangGraph Agent 问答
ontoagent serve                     # 启动 MCP Server
ontoagent web                       # 启动 Web API + UI
ontoagent butler serve              # 启动事件驱动 Butler 引擎
ontoagent info                      # 系统状态
```

---

## 项目结构

```
src/ontoagent/
├── domain/            # Schema（12 实体，29 关系）、异常、溯源
│   ├── schema.py      #   实体 + 关系定义
│   ├── approval.py    #   审批领域类型
│   └── shapes.py      #   Shape 约束模型
├── store/             # GraphStore 抽象 + Neo4j/NebulaGraph 后端 + ChromaDB
│   ├── graph_store.py #   抽象接口
│   ├── neo4j_store.py #   Neo4j 实现
│   ├── nebula_store.py#   NebulaGraph 实现
│   ├── chroma_store.py#   向量存储
│   ├── factory.py     #   后端工厂 (neo4j|nebula)
│   └── migrations/    #   Schema 版本迁移
├── parsing/           # Tree-sitter 解析（Python + Java + 文档）
│   ├── parser/        #   语言专用解析器
│   └── extractor/     #   关系 + 语义提取
├── pipeline/          # 构建流水线 + 约束配置
│   ├── builder.py     #   多阶段构建器
│   ├── shapes.yaml    #   第三层约束规则
│   ├── constraints.yaml # 第一层遍历路径
│   ├── business_ontology.yaml # 领域本体配置
│   └── ontology_actions.yaml  # 意图 → action 映射
├── execution/         # 控制层——约束 + 审批
│   ├── action_executor.py  # 编排 intent → function
│   ├── shape_evaluator.py  # 评估 Shape 约束
│   ├── shape_registry.py   # Shape 注册表
│   ├── decision_fuser.py   # 合并多个 Shape 结果
│   ├── path_compiler.py    # 编译路径表达式为查询
│   ├── function_runner.py  # 重试 + 熔断
│   ├── constraints/        # approval_gate, policies
│   └── functions/          # 通用 + 领域 Function
├── config/            # 配置文件
│   ├── approval_policy.yaml   # 审批门策略
│   ├── function_danger_levels.yaml
│   ├── constraint_overrides.yaml # 第二层覆盖
│   └── tool_gateway.yaml    # 写操作拦截
├── agent/             # LangGraph ReAct Agent
├── butler/            # 事件驱动常驻引擎
├── auth/              # 多仓库 ACL + 认证中间件
├── observability/     # 指标 + 结构化日志
├── service/           # 服务层
└── api/               # CLI + MCP Server + Web API
```

---

## 技术栈

| 分类 | 技术 |
|------|------|
| 语言 | Python 3.13+ |
| 包管理 | uv |
| AST 解析 | Tree-sitter（Python + Java） |
| 图数据库 | Neo4j 5.x / NebulaGraph |
| 向量数据库 | ChromaDB |
| 代码嵌入 | Qwen2.5-0.5B-Coder (Ollama) |
| Agent 框架 | LangGraph |
| Web 框架 | FastAPI |
| 前端 | Vue 3 + Vite + TypeScript |
| CLI | Click |
| 代码质量 | ruff + pyright |

---

## 配置

所有配置通过 `.env` 文件或环境变量（参考 `.env.example`）：

| 配置项 | 说明 |
|--------|------|
| `ONTOAGENT_NEO4J_URI` | Neo4j 连接 URI |
| `ONTOAGENT_NEBULA_HOST` | NebulaGraph 地址（使用 nebula 后端时） |
| `ONTOAGENT_GRAPH_BACKEND` | `neo4j` 或 `nebula` |
| `ONTOAGENT_OLLAMA_URL` | Ollama embedding 端点 |
| `ONTOAGENT_SEMANTIC_LLM_PROVIDER` | 语义提取 LLM：`ollama` 或 `openai` |
| `ONTOAGENT_AGENT_LLM_PROVIDER` | Agent LLM 提供商（默认 zhipu） |
| `ONTOAGENT_CHROMA_DIR` | ChromaDB 存储路径 |

---

## 项目统计

| 指标 | 数值 |
|------|------|
| 版本 | 0.2.0 |
| 源代码 | 113 文件，23K 行 |
| 测试 | 1,885 个测试（1,772 单元测试） |
| 实体 | 12 |
| 关系 | 29 |
| 提交 | 271+ |

---

## 开源协议

MPL 2.0
