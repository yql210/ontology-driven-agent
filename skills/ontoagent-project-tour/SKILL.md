---
name: ontoagent-project-tour
description: |
  Systematic learning guide for the OntoAgent project — an ontology-driven knowledge
  graph engine. Use this skill when someone wants to understand, learn, or onboard to
  the OntoAgent codebase. Provides a 7-layer progressive tour from high-level
  architecture to implementation details, with exact file paths and reading order.
  当有人想学习、理解或上手 OntoAgent 项目时使用此 skill。
---

# OntoAgent 项目导学

> **目标读者**: 想吃透这个项目的开发者、研究者、新成员
> **项目地址**: `gitee.com:sinxyql/ontology-driven-agent`
> **技术栈**: Python 3.13 · Neo4j 5.x · ChromaDB · LangGraph · tree-sitter · FastAPI

## 项目一句话

**OntoAgent = 本体驱动的可更新知识图谱引擎。**
从源代码 + 文档自动构建知识图谱（Neo4j + ChromaDB），上层 LangGraph ReAct Agent 编排查询与操作，Shape 约束系统（SHACL 风格）实现意图→操作的治理。

---

## 学习路径：7 层渐进式导学

每层读完后你应该能回答的核心问题：

| 层 | 主题 | 读完后能回答 | 预计时间 |
|----|------|-------------|---------|
| **L1** | 宏观架构 | 这是什么？解决什么问题？ | 15min |
| **L2** | 数据模型 | 图里有哪些实体和关系？ | 20min |
| **L3** | 构建流水线 | 源码怎么变成知识图谱？ | 30min |
| **L4** | 查询与 Agent | 用户问题怎么被回答？ | 25min |
| **L5** | Shape 约束系统 | 操作怎么被治理？（核心卖点） | 30min |
| **L6** | 增量更新 | 代码变了图谱怎么更新？ | 20min |
| **L7** | 动手实践 | 怎么跑起来？怎么换域？ | 30min |

---

## L1: 宏观架构（15 分钟）

### 先读的文件

1. **`README.md`** — 项目概述、功能列表、快速开始
2. **`CLAUDE.md`** — 最全面的架构说明（给 AI 看的，但人看更好）
3. **`docs/design/DESIGN_V34.md`** — 当前架构设计文档

### 四层架构

```
用户 ─→ API 层 (CLI/Web/MCP)
          │
          ▼
      Agent 层 (LangGraph ReAct) ── tools.py (17个工具函数)
          │
          ▼
     Execution 层 (ActionExecutor + Shape约束 + 审批)
          │
          ▼
     Semantic 层 (Neo4j + ChromaDB)
```

### 核心设计理念

- **本体驱动**: 实体类型、关系类型、操作规则全部由 YAML 配置定义，不改代码
- **可更新**: 增量更新而非全量重建（git diff → 影响传播 → 局部更新）
- **治理优先**: 每个操作经过 Shape 约束评估（BLOCK/WARN/ALLOW/ESCALATE）

### 验证理解

读完这层，你应该能回答：
- OntoAgent 解决什么问题？（代码知识图谱自动构建 + 治理）
- 四层架构各负责什么？
- 为什么用"本体驱动"而不是硬编码？

---

## L2: 数据模型（20 分钟）

### 核心文件: `src/ontoagent/domain/schema.py`

这是整个项目的数据地基。读懂这个文件就理解了"图里有什么"。

### 实体类型（9 种，Neo4j Label = 类名）

| 实体 | 用途 | 典型 entity_type |
|------|------|-----------------|
| `CodeEntity` | 代码实体 | function, class, module, file, interface, enum |
| `ConceptEntity` | 业务概念 | business_concept, design_pattern, api_contract |
| `DocEntity` | 文档实体 | markdown, readme, api_doc |
| `ResourceEntity` | 资源实体 | image, diagram, config, schema_file |
| `ModuleEntity` | 模块聚类 | community |
| `ChangeSetEntity` | 变更集 | commit |
| `DataAsset` | 数据资产 | database, table, api |
| `ComplianceItem` | 合规项 | policy, regulation |
| `ServiceEntity` | 微服务 | service |

### 关系类型（11 种，Neo4j Type = UPPER_SNAKE）

```python
# 结构关系 (AST 提取)
CALLS       # 函数调用
EXTENDS     # 类继承
IMPLEMENTS  # 接口实现
IMPORTS     # 导入
CONTAINS    # 包含（模块→函数）

# 语义关系 (LLM 提取)
SEMANTIC_IMPACT  # 语义影响
DESCRIBES        # 文档描述代码
ILLUSTRATES      # 示例说明
DERIVED_FROM     # 派生自

# 变更关系
CHANGED_IN  # 实体在哪个提交中变更
AFFECTS     # 变更影响传播

# 业务关系
PROCESSES_DATA  # 代码处理数据资产
SUBJECT_TO      # 代码受合规约束
```

### 属性规范

- 属性名使用 **camelCase**（如 `entityType`, `filePath`）
- Cypher 查询必须**参数化**，禁止字符串拼接（防注入）

### 验证理解

- 列举 3 种实体和 3 种关系，说明它们的语义
- 为什么属性名用 camelCase 而不是 snake_case？
- `PROCESSES_DATA` 和 `AFFECTS` 有什么区别？

---

## L3: 构建流水线（30 分钟）

### 核心文件: `src/ontoagent/pipeline/builder.py`

`OntoAgentBuilder.build()` 是项目最重要的方法——5 阶段流水线，从源码到知识图谱。

### 5 阶段流水线

```
Stage 1: Parse (解析)
  ├── tree-sitter 扫描 Python/Java 源码
  ├── 提取 AST 实体（CodeEntity）
  └── 提取结构关系（CALLS, CONTAINS, EXTENDS...）
         ↓ 关键路径（失败=中止）

Stage 2: Structural Write (结构写入)
  ├── 实体 MERGE → Neo4j
  ├── 关系 MERGE → Neo4j
  ├── ServiceEntity / Topic 写入
  └── 业务本体加载（DataAsset, ComplianceItem）
         ↓ 关键路径（失败=中止）

Stage 2.5: Doc-Code Link (文档关联)
  └── DESCRIBES 关系：文档 ↔ 代码

Stage 3: Semantic (语义提取) [可降级]
  ├── LLM 提取业务概念（ConceptEntity）
  ├── 四步对齐：精确 → 别名 → 向量 → 图结构
  └── 写入语义关系
         ↓ 可降级（LLM 不可用时跳过）

Stage 4: Clustering (模块聚类) [可降级]
  └── 社区检测 → ModuleEntity
         ↓ 可降级

Stage 5: Vector Index (向量索引) [可降级]
  └── 实体向量 → ChromaDB（Ollama embedding）
         ↓ 可降级
```

### 降级策略

只有 Stage 1-2 是关键路径。Stage 3-5 失败时只记 warning，不中止构建。这是设计意图——**部分图谱也比没有图谱好**。

### 解析器架构

```
BaseParser (模板方法)
  ├── PythonParser (tree-sitter Python)
  ├── JavaParser (tree-sitter Java)
  └── DocParser (独立，Markdown 解析)
```

`base.py` 定义 `parse_source()` 模板方法，子类只需实现 4 个钩子：`_create_root_entity`, `_pre_scan`, `_walk`, `_extract_external_calls`。

### 阅读顺序

1. `builder.py:build()` (L479-700) — 编排逻辑
2. `builder.py:_stage_parse()` (L215) — Stage 1
3. `builder.py:_stage_write_structural()` (L271) — Stage 2
4. `parsing/parser/base.py` — 模板方法模式
5. `parsing/parser/python_parser.py` — 具体实现

### 验证理解

- 5 个阶段中哪些是关键路径？哪些可降级？
- 为什么用模板方法模式而不是 if-else 区分语言？
- 如果 LLM 不可用，构建结果是什么？

---

## L4: 查询与 Agent（25 分钟）

### 核心文件

1. `src/ontoagent/agent/graph.py` — LangGraph ReAct 状态图
2. `src/ontoagent/agent/tools.py` — 17 个工具函数
3. `src/ontoagent/execution/action_executor.py` — 意图执行

### 意图 → 执行链路

```
用户: "UserService 类太大了，帮我重构"
  │
  ▼
Agent (LangGraph ReAct)
  │  prompt 中的 trigger_hint 匹配 → intent_type="refactor"
  │
  ▼
express_intent(intent_type="refactor", target="UserService")
  │
  ▼
ActionExecutor.execute()
  ├── 1. 查 intent_map → ActionConfig
  ├── 2. resolve_entity("UserService") → 实体字典
  ├── 3. check_with_shapes(entity, config) → 约束检查
  │      ├── BLOCK → 直接拒绝
  │      ├── ESCALATE → 需要审批
  │      └── WARN/ALLOW → 继续
  ├── 4. 构建 ActionContext (graph_store + match_data)
  └── 5. FunctionRunner.run("check_refactor_eligibility", ctx)
         │
         ▼
      Function 执行（读/写图数据，返回 FunctionResult）
```

### 意图路由配置: `pipeline/ontology_actions.yaml`

```yaml
actions:
  refactor:
    intent_type: refactor
    trigger_hint: "用户要求重构、拆分、优化代码"
    bind_to: code_entity
    functions:
      - check_refactor_eligibility
    requires_approval: false
```

**添加新操作不改代码——只改 YAML。** 这是"本体驱动"的核心体现。

### Agent 工具函数 (tools.py)

| 工具 | 功能 |
|------|------|
| `semantic_search` | ChromaDB 向量搜索 |
| `graph_query` | Cypher 查询（给 LLM 用的 Text2Cypher） |
| `impact_analysis` | 变更影响 BFS 传播 |
| `get_context` | 获取实体完整上下文 |
| `express_intent` | 执行操作（经过约束+审批） |
| `check_operation` | 预检操作是否被允许 |
| `explain_constraint` | 解释 Shape 约束 |
| `suggest_alternatives` | 被阻断后推荐替代方案 |
| ... | 共 17 个 |

### 验证理解

- 从用户输入到 Function 执行，经过几个步骤？
- `ontology_actions.yaml` 的作用是什么？
- 为什么 `graph_query` 给 LLM 而不是直接给用户？

---

## L5: Shape 约束系统（30 分钟）⭐ 核心卖点

### 这是 OntoAgent 区别于其他代码图谱工具的关键

### 核心文件

1. `src/ontoagent/pipeline/shapes.yaml` — 约束规则定义
2. `src/ontoagent/execution/shape_registry.py` — 规则加载
3. `src/ontoagent/execution/shape_evaluator.py` — 规则评估
4. `src/ontoagent/execution/decision_fuser.py` — 多规则融合
5. `src/ontoagent/domain/shapes.py` — 数据模型

### Shape 规则结构

```yaml
shapes:
  - id: shape:sensitive_data        # 唯一标识
    name: 敏感数据保护
    target:
      entry_type: CodeEntity         # 作用于哪种实体
      operation: UPDATE              # 什么操作触发
    path: "PROCESSES_DATA -> DataAsset"  # 图遍历路径
    constraint:
      field: sensitivity             # 检查什么属性
      operator: in                   # 怎么比较
      value: [restricted, confidential]
    severity: block                  # 严重级别
    suggestion: |                    # 被阻断后的建议
      该代码处理敏感数据。可选:
      (a) 降级关联 DataAsset 的 sensitivity 标签
      (b) 申请临时豁免（24h TTL）
```

### 约束评估流程

```
express_intent("refactor", "PaymentService")
  │
  ▼
ShapeRegistry.get_shapes_for(CodeEntity, UPDATE)
  │  通过 ontology_ref 预过滤（116→1，跨域不误触发）
  │
  ▼
ShapeEvaluator.evaluate(entity, operations)
  │  对每条 Shape:
  │  1. PathCompiler 编译 path → Cypher
  │  2. 执行 Cypher 获取关联节点
  │  3. 检查 constraint（field operator value）
  │  4. 匹配 → ShapeResult(triggered)
  │
  ▼
DecisionFuser.fuse(results)
  │  多规则结果融合，取最高严重级别
  │
  ▼
  ├── BLOCK → 操作被拒绝
  ├── ESCALATE → 需要人工审批
  ├── WARN → 允许但发出警告
  └── ALLOW → 正常执行
```

### 严重级别（severity）

| 级别 | 含义 | 行为 |
|------|------|------|
| `block` | 绝对阻断 | 直接拒绝，返回 suggestion |
| `escalate` | 需要审批 | 生成审批令牌，等待人工决策 |
| `warn` | 仅警告 | 允许执行，记录 warning |
| `allow` | 正常 | 无约束触发 |

### 审批流程

```
ESCALATE → ApprovalGate.check()
  │
  ▼
生成令牌 → 返回 PENDING 状态
  │
  ▼
用户/管理员决策 → /chat/approval (approved=true/false)
  │
  ├── approved=true → 继续执行
  └── approved=false → 操作被拒绝
```

### 多域支持

Shape 的 `ontology_ref` 字段实现跨域隔离：
- 电商域的 Shape 只评估"订单""客户"相关实体
- 代码安全域的 Shape 只评估"漏洞""策略"相关实体
- **换域 = 换 shapes.yaml，框架代码不动**

### 验证理解

- 一个 Shape 的 4 个核心要素是什么？（target, path, constraint, severity）
- BLOCK 和 ESCALATE 的区别？
- 为什么需要 DecisionFuser？
- 如何添加一个新域而不改代码？

---

## L6: 增量更新（20 分钟）

### 核心文件

1. `src/ontoagent/pipeline/incremental_updater.py` — 增量更新编排
2. `src/ontoagent/pipeline/change_detector.py` — Git diff 检测
3. `src/ontoagent/pipeline/impact_propagator.py` — 影响传播

### 增量更新流程

```
git diff HEAD~1
  │
  ▼
ChangeDetector.detect_changes()
  │  识别: 哪些文件增/删/改
  │
  ▼
ImpactPropagator (双向 BFS)
  │  向上: 谁依赖了变更的实体？(callers)
  │  向下: 变更实体依赖谁？(callees)
  │  → 生成影响范围
  │
  ▼
IncrementalUpdater.update()
  ├── 重新解析变更文件
  ├── MERGE 新实体/关系到 Neo4j
  ├── 删除已移除的实体
  └── 更新向量索引
```

### 与全量构建的区别

| | 全量 build() | 增量 update() |
|---|---|---|
| 触发 | 首次 / --clear | 每次 git commit |
| 范围 | 全仓库 | 仅变更+影响范围 |
| 耗时 | 分钟级 | 秒级 |
| 语义提取 | 全量 LLM | 跳过（按需） |

### 验证理解

- 增量更新为什么需要"双向 BFS"？
- 如果一个核心函数被修改，哪些实体会受影响？
- 增量更新跳过语义提取，这合理吗？

---

## L7: 动手实践（30 分钟）

### 环境准备

```bash
# 1. 克隆项目
git clone git@gitee.com:sinxyql/ontology-driven-agent.git
cd ontology-driven-agent

# 2. 安装依赖
uv sync

# 3. 配置 .env (参考 .env.example)
#    需要: Neo4j 连接信息、Ollama 地址（可选，用于 embedding/LLM）

# 4. 启动 Neo4j (Docker)
docker compose up -d neo4j
```

### 构建知识图谱

```bash
# 用项目自身构建自身的图谱（元编程！）
uv run ontoagent build ./src --skip-semantic --clear --verbose-build

# 查看构建结果
uv run ontoagent info
```

### 查询

```bash
# 语义搜索
uv run ontoagent query "ActionExecutor"

# Agent 问答 (需要 LLM)
uv run ontoagent ask "ShapeEvaluator 是怎么工作的？"
```

### 查看图谱

打开 Neo4j Browser (`http://localhost:7474`)，运行：

```cypher
// 查看所有实体类型
MATCH (n) RETURN labels(n), count(*) AS count

// 查看函数调用链
MATCH (caller:CodeEntity)-[:CALLS]->(callee:CodeEntity)
RETURN caller.name, callee.name LIMIT 20

// 查看敏感数据关联
MATCH (code:CodeEntity)-[:PROCESSES_DATA]->(asset:DataAsset)
WHERE asset.sensitivity IN ['restricted', 'confidential']
RETURN code.name, asset.name, asset.sensitivity
```

### 换域实验

OntoAgent 不只能分析代码。通过 `ontoagent.yaml` 定义业务本体，可以适配任何领域：

1. 电商域: DataAsset(客户/订单) + ComplianceItem(PCI-DSS)
2. 代码安全域: Vulnerability + SecurityPolicy + CodeModule
3. 你的域: 定义你的实体类型和关系

```yaml
# ontoagent.yaml 示例
data_assets:
  - id: customer-db
    name: 客户数据库
    sensitivity: confidential
    compliance: [GDPR, PIPL]
```

### 跑测试

```bash
# 全量测试
uv run pytest tests/ -v

# 只跑单元测试（不需要 Neo4j）
uv run pytest tests/unit/ -v

# 跑 E2E 测试
uv run pytest tests/integration/ -v
```

### 验证理解

- 成功构建一次知识图谱，查看 Neo4j 中的节点
- 尝试添加一个新 Shape 规则到 `shapes.yaml`
- 尝试定义一个新的 `ontoagent.yaml` 业务本体

---

## 附录：关键文件速查表

| 文件 | 行数 | 职责 | 何时读 |
|------|------|------|--------|
| `domain/schema.py` | 886 | 所有实体类型定义 | L2 最先读 |
| `pipeline/builder.py` | 1317 | 构建流水线编排 | L3 核心 |
| `agent/tools.py` | 959 | 17 个工具函数 | L4 核心 |
| `agent/graph.py` | 308 | LangGraph ReAct 图 | L4 入口 |
| `execution/action_executor.py` | 231 | 意图→操作执行 | L4/L5 核心 |
| `execution/shape_evaluator.py` | 211 | Shape 规则评估 | L5 核心 |
| `execution/shape_registry.py` | 217 | Shape 加载和索引 | L5 |
| `execution/decision_fuser.py` | 144 | 多规则融合 | L5 |
| `pipeline/shapes.yaml` | 107 | 约束规则定义 | L5 先读 |
| `pipeline/ontology_actions.yaml` | — | 意图路由配置 | L4 |
| `store/neo4j_store.py` | 572 | Neo4j 操作封装 | L3 存储 |
| `parsing/parser/base.py` | 144 | 解析器模板方法 | L3 解析 |

---

## 附录：项目演进历史

| 版本 | 架构 | 关键变化 |
|------|------|---------|
| V1 | 单体 | 基础代码图谱 |
| V3 | 四层架构 | Intent/Control/Capability/Semantic 分层 |
| V3.3 | +Shape 系统 | SHACL 风格约束引入 |
| V3.4 | +多域支持 | ontology_ref 预过滤，跨域不误触发 |
| 当前 | 精简版 | 删除死代码（SAGA/DAG/Planner），保留核心链路 |

## 附录：已知的架构决策

1. **为什么不用 SAGA 分布式事务？** → v0.2 删除了。当前规模（单 Neo4j）不需要分布式事务，线性执行足够。
2. **为什么用 LangGraph 而不是自定义 Agent？** → 利用 ReAct 模式 + checkpointer（对话恢复）+ tool calling 标准化。
3. **为什么 Shape 约束用 YAML 而不是代码？** → 运维人员可以不改代码调整治理规则。SHACL 风格，业界标准。
4. **为什么保留 butler/ 模块？** → 实验性功能（自动化构建服务），有 CLI 入口。标注为实验性。
5. **builder.py 1317 行是不是上帝类？** → 是。计划在 v0.3 拆分为 Builder/Writer/Extractor/Clusterer。
