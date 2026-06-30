# OntoAgent 本体约束框架 v2 架构设计

> **状态**: 方案设计阶段  
> **日期**: 2026-06-30  
> **来源**: 两轮 Claude Code 架构脑暴 + 讨论整合

---

## 1. 背景与核心目标

### 1.1 目标

构建一个真正能把 AI Agent 关在语义笼子里的本体驱动约束系统：

| 层级 | 含义 |
|------|------|
| **不可绕过** | Agent 的任何写操作必经约束检查，无后门路径 |
| **可感知** | Agent 能主动理解约束规则，不是撞墙后才知被拦 |
| **可配置** | 换项目 = 换本体定义 YAML，框架代码不动 |

### 1.2 当前痛点

- ONTOLOGY_CONSTRAINT_REGISTRY 只有 3 条硬编码映射（DataAsset.sensitivity、ComplianceItem.severity、CodeEntity.entryCategory）
- constraint_overrides.yaml 全是注释模板，从未实际使用
- Agent 无法感知约束——约束是一堵看不见的墙
- 约束挂在 Action 级别，耦合在特定业务意图上
- Agent 可通过 graph_query 绕过约束系统
- 传播约束因数据粒度不匹配无法在真实项目上生效

---

## 2. 核心设计原则

### 2.1 三大关键纠正

经过架构审查，纠正了早期脑暴中的三个误判：

| # | 误判 | 纠正 |
|---|------|------|
| 1 | 照搬 SHACL 的验证语义 | 借鉴 SHACL 的**路径表达语法**（`^`反向、`+`闭包、`/`序列），决策语义对齐 **OPA / Cedar** 策略引擎 |
| 2 | Capability 用动词字符串（`read_code`） | 改为 `(resource_type, operation)` 二元组：resource 来自本体 schema（随项目变），operation 固定 6 种枚举（不变） |
| 3 | ConstraintShape 作为节点存入 Neo4j | Shape 放 YAML + 内存索引，评估时编译 path 为 Cypher 查实例图。元数据与实例数据物理分离 |

### 2.2 架构哲学

> **「约束不是墙，是地图。让 Agent 能读地图，比让墙更高更重要。」**

- Agent 应该像识别「DataAsset 是数据资产」一样识别「SensitiveData 是一种约束形状」
- BLOCK 不是终点——是导航信号："此路不通，但有以下替代路径"
- 约束的语义来源是本体（ontology），不是 Action 定义或 Function 实现

---

## 3. 数据模型

### 3.1 ConstraintShape（约束形状）

约束的完整定义单元。一个 Shape 描述"在什么条件下、对什么操作、产生什么决策"。

```python
@dataclass
class ConstraintShape:
    id: str                              # 唯一标识：shape:sensitive_data
    name: str  
    description: str  
    kind: ShapeKind                      # STRUCTURAL（构建时）| OPERATIONAL（运行时）
    target: ShapeTarget                  # 此约束适用于什么操作
    path: PathExpression                 # 在图上怎么走（SHACL 路径语法）
    constraint: ConstraintExpr           # 走到终点后怎么判断
    severity: Severity                   # ALLOW | WARN | BLOCK | ESCALATE
    priority: int = 0                    # 多 Shape 融合时的优先级
    tags: list[str] = []
    version: str = "1"
    enabled: bool = True

@dataclass  
class ShapeTarget:
    resource_type: str                   # 来自本体 schema（如 "CodeEntity"）
    operation: Operation                 # CREATE | READ | UPDATE | DELETE | EXECUTE | EXPORT
    field_filter: dict | None = None     # 可选 narrowing

class Severity(StrEnum):
    ALLOW = "allow"          # 通过
    WARN = "warn"            # 日志记录，放行
    BLOCK = "block"          # 拒绝，返回 DecisionReport
    ESCALATE = "escalate"    # 触发审批 Saga
```

### 3.2 PathExpression（路径表达式）

借鉴 SHACL 子集语法，编译为参数化 Cypher：

| 表达式 | 含义 | Cypher 编译 |
|--------|------|-------------|
| `CALLS` | 单跳 | `-[:CALLS]->` |
| `CALLS+` | 1..n 跳 | `-[:CALLS*1..$max_depth]->` |
| `CALLS{1,3}` | 1-3 跳 | `-[:CALLS*1..3]->` |
| `^EXTENDS` | 反向 | `<-[:EXTENDS]-` |
| `CALLS / IMPLEMENTS` | 序列 | `-[:CALLS]->()-[:IMPLEMENTS]->` |
| `CALLS \| REFERENCES` | 或 | 拆为两条查询 |

### 3.3 Function Capability（能力声明）

每个 Function 声明自己能做什么，用二元组表达：

```yaml
# functions.yaml
- name: refactor_rename
  impl: ontoagent.execution.functions.RefactorFunction
  capabilities:
    - { resource: CodeEntity, operation: UPDATE }
    - { resource: CodeEntity, operation: READ }
  
- name: check_compliance
  impl: ontoagent.execution.functions.ComplianceFunction
  capabilities:
    - { resource: CodeEntity, operation: READ }
    - { resource: DataAsset,  operation: READ }
```

- `resource` 来自本体 schema 中定义的实体类型
- `operation` 固定为 6 种枚举之一
- 匹配逻辑：`Function.capabilities ∩ Shape.target → 触发评估`

### 3.4 Shape 配置（YAML）

```yaml
# shapes.yaml
version: "2.0"
shapes:
  - id: shape:sensitive_data
    name: 敏感数据保护
    description: "操作 restricted/confidential 数据的代码需要额外审查"
    target: { resource: CodeEntity, operation: UPDATE }
    path: PROCESSES_DATA -> DataAsset
    constraint:
      field: sensitivity
      in: [restricted, confidential]
    severity: BLOCK
    suggestion: |
      该代码处理敏感数据。可选:
      (a) 降级数据标签
      (b) 申请临时豁免
      (c) 寻找不涉及敏感数据的替代方案

  - id: shape:architecture_boundary
    name: 架构边界保护
    target: { resource: CodeEntity, operation: UPDATE }
    path: ^CALLS+
    constraint:
      field: layer
      equals: infrastructure
      unless: { field: layer, equals: infrastructure }  # 自身也在 infrastructure 层则放行
    severity: WARN
```

### 3.5 与现有代码的关系

| 现有模块 | 处置 | 原因 |
|----------|------|------|
| `ONTOLOGY_CONSTRAINT_REGISTRY`（Python dict，3 条） | **废弃** | 硬编码 → YAML 驱动 |
| `constraints.yaml`（旧格式） | **升级**为 `shapes.yaml`（Shape 中心） | 结构升级 |
| `ontology_actions.yaml` 的 `guard_configs` | **删除** | 约束不再挂 Action |
| Function 注册表 | **扩展** `capabilities` 字段 | 结构不变 |
| `ActionExecutor._check_criteria` | **重写**为 ShapeEvaluator 中间件 | 硬编码条件 → 通用主循环 |

---

## 4. 执行流程

```
Agent 表达意图
   │
   ▼
express_intent(intent_type, target, params)
   │
   ▼
IntentRouter → 命中 ActionConfig
   │
   ▼
ActionExecutor.execute()
   │
   ├─ 1. resolve_entity(target) → 实体节点
   │
   ├─ 2. 查 Action → [refactor_rename, check_compliance, ...]
   │
   ├─ 3. 收集能力标签
   │      { (CodeEntity, UPDATE), (CodeEntity, READ), (DataAsset, READ) }
   │
   ├─ 4. ShapeEvaluator.match(capabilities)
   │      ├─ 内存索引: (resource_type, operation) → 候选 Shape 集合
   │      ├─ 对每个匹配 Shape:
   │      │    ├─ PathCompiler.compile(path) → 参数化 Cypher
   │      │    ├─ Neo4j 执行查询 → 拿终点节点
   │      │    ├─ 在终点节点上 eval constraint
   │      │    └─ 输出 (severity, evidence, suggestion)
   │      └─ DecisionFuser.merge() → 最终决策
   │
   ├─ 5. 决策路由:
   │      ├─ ALLOW     → 执行 function chain
   │      ├─ WARN      → log + 继续执行
   │      ├─ BLOCK     → 返回 DecisionReport，让 Agent 决定
   │      └─ ESCALATE  → 走审批 Saga
   │
   ├─ 6. BLOCK 时 Agent 可选:
   │      ├─ suggest_alternatives(intent, target) → 单步启发式
   │      ├─ explain_constraint(shape_id) → 查看规则全文
   │      └─ 放弃操作
   │
   └─ 7. Function 执行 → TransactionManager → 返回结果
```

### 关键设计点

- ShapeEvaluator 是同步中间件，不可跳过
- DecisionFuser 默认严格优先：`BLOCK > ESCALATE > WARN > ALLOW`
- Shape.priority 可覆盖默认融合逻辑
- `graph_query` 工具加**写操作白名单拦截**——只允许 `MATCH ... RETURN`，拦截 CREATE/MERGE/DELETE/SET/REMOVE

---

## 5. Agent 约束感知

### 5.1 System Prompt（≤200 token，精简）

```
你在本体驱动的代码图谱上工作。所有操作会自动经过约束评估。

被 BLOCK 时会收到 DecisionReport（包含路径、证据、替代建议）。

工具:
- explore_ontology() — 浏览本体摘要（实体、关系、约束 Shape）
- explain_constraint(id) — 查看某个 Shape 的完整定义
- suggest_alternatives(intent, target) — 被拦后请求替代方案

约束是地图，不是墙。BLOCK 是导航信号，不是终点。
```

### 5.2 三个新工具

| 工具 | 用途 | 返回内容 |
|------|------|---------|
| `explore_ontology()` | 了解系统有哪些约束 | 实体类型/关系类型/Shape 的 name+description 摘要 |
| `explain_constraint(shape_id)` | 查看某个约束的详细定义 | Shape 全文 + path + constraint 条件 |
| `suggest_alternatives(intent, target)` | 被 BLOCK 后寻找绕行方案 | 去掉触发约束的 path 终点，重跑评估的结果 |

### 5.3 BLOCK 决策报告

```yaml
decision: BLOCK
intent: refactor_rename
target: CodeEntity:login

triggered_shapes:
  - id: shape:sensitive_data
    severity: BLOCK
    path_traveled: "(login)-[:PROCESSES_DATA]->(userPII:DataAsset)"
    evidence: { field: sensitivity, value: restricted }
    suggestion: |
      login 处理 restricted 级数据 userPII。
      (a) 降级 userPII 的 sensitivity 标签
      (b) 申请临时豁免（24h TTL，需审批）
      (c) 寻找不触碰敏感数据的替代重构方案

next_actions:
  - suggest_alternatives
  - explain_constraint("shape:sensitive_data")
```

---

## 6. 与现有代码的差异

### 需要重写

| 模块 | 程度 | 原因 |
|------|------|------|
| `domain/schema.py` 的 ONTOLOGY_CONSTRAINT_REGISTRY | 废弃 | Python dict → YAML 驱动 |
| `constraints.yaml` → `shapes.yaml` | 升级 | 结构从规则列表 → Shape 中心 |
| `ActionExecutor._check_criteria` | 重写 | 硬编码条件 → ShapeEvaluator 主循环 |
| Guard Pipeline（5 个固定 Guard） | 替换 | → ShapeEvaluator + DecisionFuser |
| `ontology_actions.yaml` 解析 | 删 guard_configs | 约束不再挂 Action |

### 可以复用

| 模块 | 复用度 |
|------|:---:|
| 6 实体 + 关系 schema（domain/schema.py） | 100% |
| Neo4j / ChromaDB 存储层 | 100% |
| 全部 parser / builder 管线 | 100% |
| FunctionRunner / SAGA / CircuitBreaker | 100% |
| IntentRouter 主流程 | 80% |
| Agent graph / tools 主结构 | 90% |

### 新增模块

| 模块 | 文件 | 职责 |
|------|------|------|
| ConstraintShape | `domain/shapes.py` | ConstraintShape + PathExpression + ShapeTarget dataclass |
| ShapeRegistry | `execution/shape_registry.py` | 加载 shapes.yaml + functions.yaml → 内存索引 |
| ShapeEvaluator | `execution/shape_evaluator.py` | 中间件：capability 匹配 → Cypher 编译 → 约束评估 |
| PathCompiler | `execution/path_compiler.py` | SHACL 路径语法 → 参数化 Cypher |
| DecisionFuser | `execution/decision_fuser.py` | 多 Shape 结果融合 |

---

## 7. 可扩展性验证

### 7.1 场景 A：新增本体领域（微服务依赖合规）

定义一个全新的约束："ServiceEntity.dependency_type 为 critical 时沿 SERVICE_DEPENDS_ON 遍历，block 循环依赖"。

**需要改的文件**：
1. `domain/schema.py` — 新增 `ServiceEntity` dataclass + `SERVICE_DEPENDS_ON` 关系
2. `store/migrations/` — 新增迁移脚本
3. `shapes.yaml` — 新增一条 Shape
4. `parsing/parser/` — 如需解析 k8s/compose 文件（可选）

**不需要改**：任何 Python 框架代码、Function 实现、Agent 工具。

### 7.2 场景 B：新增 Function（deploy_to_k8s）

**需要改的文件**：
1. `functions/deploy.py` — 实现 DeployFunction 类
2. `functions.yaml` — 注册 + 声明 capabilities: `[(ServiceEntity, EXECUTE)]`
3. `ontology_actions.yaml` — 可选：新增 intent → action 路由

**不需要改**：Shape、ShapeEvaluator、Agent 工具。

---

## 8. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 能力标签膨胀 | Shape 匹配失效 | 强制二元组 `(resource, operation)`；resource 启动时校验必须在 schema 中；operation 固定 6 种枚举 |
| 变长路径查询性能爆炸 | 大图上 5+ 跳超时 | Shape 强制 `max_depth`（默认 3）；>3 跳走 GDS 预计算传播索引 |
| 衰减在 Cypher 里难表达 | 复杂传播不可用 | 简单线性衰减用 `reduce()`；复杂场景走 GDS；不做 Python BFS |
| 协商协议 NP-hard | suggest_alternatives 不收敛 | 只做单步启发式，不承诺全局最优 |
| 多 Shape 同时 BLOCK 但 suggestion 矛盾 | Agent 困惑 | 返回全部触发的 Shape report，让 Agent 自行判断；引擎只融合 severity |
| 本体演进时 Shape 迁移 | 老规则和新 schema 不兼容 | Shape 加 `version` 字段；启动时校验 resource_type 存在；CI 检查 |
| 元数据与实例数据混淆 | 查询污染、版本断裂 | Shape 放 YAML + 内存；评估时编译 Cypher 查实例图 |
| Agent 通过 graph_query 绕过约束 | 约束形同虚设 | graph_query 工具加 Cypher 白名单：仅允许 MATCH...RETURN，拦截所有写操作 |

---

## 9. 总结

```
                    本体定义              能力声明              引擎匹配
                 (可插拔 YAML)      (Function 不改)          (框架通用)
                 ────────────      ──────────────     ──────────────────
                 shapes.yaml       capabilities:      ShapeEvaluator.match()
                 functions.yaml    [(CodeEntity,       PathCompiler → Cypher
                 schema.py         UPDATE), ...]       → 实例图查询 → 决策
```

**核心断言**：这个架构把约束从"硬编码在 Action 上的 guard_configs"解放为"可插拔的本体定义 × 能力标签的自动匹配"。换一个项目，不用改框架代码——只换 schema + shapes.yaml。

**Agent 侧**：三个工具让 Agent 从"撞墙的盲人"变成"看地图的导航者"。约束成为可理解、可绕过（通过 suggestion）、可升级（通过 ESCALATE）的语义资源。

**唯一需要补的缺口**：`graph_query` 工具加 Cypher 写操作白名单拦截（改动量小）。
