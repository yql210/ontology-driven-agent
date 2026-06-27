# LayerKG 本体架构升级方案 V3.0

> 数据驱动闭环架构：Schema规则触发Action，Action编排Function，Function更新数据形成闭环

## 一、架构总览

```
┌─────────────────────────────────────────────┐
│            用户 / Agent                      │
│  角色：意图转化器（把自然语言变成临时层数据）  │
│  工具：express_intent (写入临时层)            │
│        query_status (查询流程状态)            │
└──────────────┬──────────────────────────────┘
               │ 写入临时层
               ▼
┌─────────────────────────────────────────────┐
│         知识图谱（Neo4j）                     │
│                                             │
│  核心层：构建时写入的代码实体、关系（可信数据）│
│  临时层：运行时产生的请求、状态变更（待验证）  │
│         用 is_temporary=true 标签区分        │
└──────────────┬──────────────────────────────┘
               │ 数据变化
               ▼
┌─────────────────────────────────────────────┐
│          规则引擎 (RuleEngine)                │
│                                             │
│  监听图谱数据变化                            │
│  匹配 Schema 中定义的触发规则               │
│  满足条件 → 自动触发对应 Action             │
└──────────────┬──────────────────────────────┘
               │ 触发 Action
               ▼
┌─────────────────────────────────────────────┐
│       Action 编排器 (ActionOrchestrator)      │
│                                             │
│  每个 Action 内部定义 Function 调用顺序      │
│  支持条件判断、串联调用                      │
│  支持审批节点（高风险操作挂起等待确认）      │
└──────────────┬──────────────────────────────┘
               │ 调用 Function
               ▼
┌─────────────────────────────────────────────┐
│        Function 执行层                        │
│                                             │
│  原子化操作，功能单一不可拆分                │
│  执行后更新图谱数据（核心层或临时层）        │
│  数据更新 → 触发规则引擎 → 新 Action → 闭环 │
└─────────────────────────────────────────────┘
```

**核心循环：**
```
数据变化 → 规则匹配 → Action触发 → Function执行 → 数据又变化 → 新规则匹配 → ...
```

**Agent 不再是决策者，而是数据入口。** 它把用户的自然语言转化为临时层实体数据，剩下的由规则引擎自动驱动。

## 二、四层详细设计

### 第 1 层：Schema（设计规范 + 触发规则库）

当前 `schema.py` 只定义了实体属性和关系约束。升级后还要定义**触发规则**。

```python
# schema.py 新增

TRIGGER_RULES: list[TriggerRule] = [
    # --- 代码重构场景 ---
    TriggerRule(
        name="validate_refactor_request",
        description="用户请求重构 → 先验证目标实体是否存在且可重构",
        condition="""
        MATCH (req:UserRequest {type: 'refactor', status: 'pending'})-[:TARGETS]->(c:CodeEntity)
        WHERE c.lines > 50 OR c.branches > 10
        RETURN req, c
        """,
        action="validate_refactor",
    ),
    TriggerRule(
        name="execute_validated_refactor",
        description="重构请求验证通过 → 执行重构",
        condition="""
        MATCH (req:UserRequest {type: 'refactor', status: 'validated'})
        RETURN req
        """,
        action="execute_refactor",
    ),

    # --- 告警处理场景 ---
    TriggerRule(
        name="auto_diagnose_alert",
        description="P0/P1 告警创建 → 自动启动诊断",
        condition="""
        MATCH (a:AlertEntity)
        WHERE a.severity IN ['P0', 'P1'] AND a.status = 'open'
        AND NOT (a)<-[:DIAGNOSED_BY]-(:DiagnosisResult)
        RETURN a
        """,
        action="diagnose_alert",
    ),
    TriggerRule(
        name="auto_rollback_on_confirmed_root_cause",
        description="根因确认且有可疑变更集 → 触发回滚流程",
        condition="""
        MATCH (a:AlertEntity {status: 'root_cause_confirmed'})
              -[:TRIGGERED_BY]->(log:LogEntity),
              (a)<-[:AFFECTS]-(cs:ChangeSetEntity)
        RETURN a, cs
        """,
        action="rollback",
    ),

    # --- 文档补全场景 ---
    TriggerRule(
        name="auto_document_low_coverage",
        description="用户请求文档补全 → 先检查覆盖率",
        condition="""
        MATCH (req:UserRequest {type: 'document', status: 'pending'})-[:TARGETS]->(c:CodeEntity)
        RETURN req, c
        """,
        action="check_and_generate_docs",
    ),

    # --- 变更影响通知场景 ---
    TriggerRule(
        name="notify_on_new_changeset",
        description="新变更集创建 → 通知受影响模块的负责人",
        condition="""
        MATCH (cs:ChangeSetEntity {notified: false})
              -[:AFFECTS]->(c:CodeEntity)
        WHERE EXISTS {
            MATCH (c)-[:CALLS|IMPORTS*1..3]->(dependent:CodeEntity)
            RETURN dependent
        }
        RETURN cs, collect(DISTINCT c) AS affected
        """,
        action="notify_stakeholders",
    ),
]
```

### 第 2 层：Action（流程编排）

Action 不再是单次执行，而是**定义 Function 的调用顺序和条件判断**。

```yaml
# ontology_actions.yaml 全新结构

actions:
  # ========== 代码重构场景 ==========

  validate_refactor:
    description: "验证重构请求（实体存在性、可重构性、风险评估）"
    trigger_rule: validate_refactor_request
    steps:
      - function: check_entity_exists
        input: { entity_name: "$.request.target" }
        on_failure: reject_request

      - function: check_refactor_eligibility
        input: { entity_name: "$.request.target" }
        on_failure: reject_request

      - function: assess_impact
        input: { entity_name: "$.request.target", depth: 3 }

      - function: update_request_status
        input: { request_id: "$.request.id", status: "validated" }

  execute_refactor:
    description: "执行重构（生成方案、写入变更集、通知下游）"
    trigger_rule: execute_validated_refactor
    approval: false
    steps:
      - function: analyze_complexity
        input: { entity_name: "$.request.target" }

      - function: generate_refactor_plan
        input: { entity_name: "$.request.target" }

      - function: create_changeset
        input: { entity_name: "$.request.target", plan: "$.steps[1].result" }

      - function: update_request_status
        input: { request_id: "$.request.id", status: "completed" }

  # ========== 告警处理场景 ==========

  diagnose_alert:
    description: "告警自动诊断（日志分析 + 调用链追踪 + 变更关联）"
    trigger_rule: auto_diagnose_alert
    steps:
      - function: diagnose_by_log
        input: { alert_name: "$.alert.name" }

      - function: diagnose_by_chain
        input: { alert_name: "$.alert.name" }

      - function: correlate_changeset
        input: { alert_name: "$.alert.name" }

      - function: write_diagnosis_result
        input:
          alert_name: "$.alert.name"
          diagnosis: "$.steps[0].result"
          call_chain: "$.steps[1].result"
          changeset: "$.steps[2].result"

      - function: update_alert_status
        input: { alert_name: "$.alert.name", status: "root_cause_confirmed" }

  rollback:
    description: "回滚到最近稳定版本（高风险，需审批）"
    trigger_rule: auto_rollback_on_confirmed_root_cause
    approval: true
    steps:
      - function: find_last_stable
        input: { alert_name: "$.alert.name" }

      - function: generate_rollback_plan
        input: { alert_name: "$.alert.name", target: "$.steps[0].result" }

      - function: execute_rollback
        input: { alert_name: "$.alert.name", plan: "$.steps[1].result" }

      - function: update_alert_status
        input: { alert_name: "$.alert.name", status: "resolved" }

  # ========== 文档补全场景 ==========

  check_and_generate_docs:
    description: "检查文档覆盖率并生成文档"
    trigger_rule: auto_document_low_coverage
    steps:
      - function: check_doc_coverage
        input: { entity_name: "$.request.target" }

      - function: generate_api_doc
        input: { entity_name: "$.request.target", coverage: "$.steps[0].result" }
        condition: "$.steps[0].result.coverage_pct < 0.5"

      - function: write_doc_entity
        input: { entity_name: "$.request.target", doc: "$.steps[1].result" }

      - function: update_request_status
        input: { request_id: "$.request.id", status: "completed" }

  # ========== 变更通知场景 ==========

  notify_stakeholders:
    description: "通知受变更影响的模块负责人"
    trigger_rule: notify_on_new_changeset
    steps:
      - function: find_affected_owners
        input: { changeset: "$.changeset.id" }

      - function: send_notification
        input: { owners: "$.steps[0].result", changeset: "$.changeset.id" }

      - function: mark_changeset_notified
        input: { changeset: "$.changeset.id" }

# ========== Function 注册表 ==========

functions:
  # --- 通用 ---
  check_entity_exists:
    description: "检查实体是否存在于核心层"
    params: [entity_name]
    implementation: layerkg.functions.general:check_entity_exists

  update_request_status:
    description: "更新 UserRequest 状态"
    params: [request_id, status]
    implementation: layerkg.functions.general:update_request_status

  update_alert_status:
    description: "更新 AlertEntity 状态"
    params: [alert_name, status]
    implementation: layerkg.functions.general:update_alert_status

  reject_request:
    description: "拒绝请求并写入原因"
    params: [request_id, reason]
    implementation: layerkg.functions.general:reject_request

  # --- 代码分析 ---
  check_refactor_eligibility:
    description: "检查代码实体是否可重构"
    params: [entity_name]
    implementation: layerkg.functions.code:check_refactor_eligibility

  assess_impact:
    description: "评估变更影响范围"
    params: [entity_name, depth]
    implementation: layerkg.functions.code:assess_impact

  analyze_complexity:
    description: "分析代码复杂度"
    params: [entity_name]
    implementation: layerkg.functions.code:analyze_complexity

  generate_refactor_plan:
    description: "生成重构方案"
    params: [entity_name]
    implementation: layerkg.functions.code:generate_refactor_plan

  check_doc_coverage:
    description: "检查文档覆盖率"
    params: [entity_name]
    implementation: layerkg.functions.code:check_doc_coverage

  generate_api_doc:
    description: "生成 API 文档"
    params: [entity_name, coverage]
    implementation: layerkg.functions.code:generate_api_doc

  # --- 变更管理 ---
  create_changeset:
    description: "创建变更集实体"
    params: [entity_name, plan]
    implementation: layerkg.functions.changeset:create_changeset

  find_affected_owners:
    description: "查找受影响的模块负责人"
    params: [changeset]
    implementation: layerkg.functions.changeset:find_affected_owners

  send_notification:
    description: "发送通知"
    params: [owners, changeset]
    implementation: layerkg.functions.changeset:send_notification

  mark_changeset_notified:
    description: "标记变更集已通知"
    params: [changeset]
    implementation: layerkg.functions.changeset:mark_changeset_notified

  # --- 告警诊断 ---
  diagnose_by_log:
    description: "按日志模式分析告警根因"
    params: [alert_name, max_patterns]
    implementation: layerkg.functions.alert:diagnose_by_log

  diagnose_by_chain:
    description: "按调用链追踪故障路径"
    params: [alert_name, depth]
    implementation: layerkg.functions.alert:diagnose_by_chain

  correlate_changeset:
    description: "关联最近变更集"
    params: [alert_name]
    implementation: layerkg.functions.alert:correlate_changeset

  write_diagnosis_result:
    description: "写入诊断结果到图谱"
    params: [alert_name, diagnosis, call_chain, changeset]
    implementation: layerkg.functions.alert:write_diagnosis_result

  # --- 回滚 ---
  find_last_stable:
    description: "找到最近稳定版本"
    params: [alert_name]
    implementation: layerkg.functions.alert:find_last_stable

  generate_rollback_plan:
    description: "生成回滚方案"
    params: [alert_name, target]
    implementation: layerkg.functions.alert:generate_rollback_plan

  execute_rollback:
    description: "执行回滚操作"
    params: [alert_name, plan]
    implementation: layerkg.functions.alert:execute_rollback

  # --- 文档 ---
  write_doc_entity:
    description: "写入 DocEntity 到图谱"
    params: [entity_name, doc]
    implementation: layerkg.functions.doc:write_doc_entity
```

### 第 3 层：Function（原子化执行单元）

每个 Function 是一个独立的 Python 函数，职责单一，接收参数，返回结果。

关键特性：
- **可以读图谱**（通过 graph_store）
- **可以写图谱**（更新实体属性、创建新实体和关系）
- **可以调用外部系统**（工单系统、通知系统）
- **执行后的数据更新会触发新的规则**

```python
# src/layerkg/functions/code.py

def check_refactor_eligibility(entity_name: str, **kwargs) -> FunctionResult:
    """检查代码实体是否可重构。"""
    graph_store = kwargs["graph_store"]
    
    # 查核心层
    node = graph_store.get_node_by_name(entity_name)
    if not node:
        return FunctionResult(success=False, data={"reason": "entity not found"})
    
    lines = node.get("lines", 0)
    branches = node.get("branches", 0)
    
    # 查下游依赖数
    deps = graph_store.query(
        "MATCH (c:CodeEntity {name: $name})<-[:CALLS*1..3]-(caller:CodeEntity) "
        "RETURN count(DISTINCT caller) AS cnt",
        {"name": entity_name},
    )
    dep_count = deps[0]["cnt"] if deps else 0
    
    eligible = lines > 100 or branches > 15
    risk = "low" if dep_count < 10 else "medium" if dep_count < 30 else "high"
    
    return FunctionResult(
        success=True,
        data={
            "eligible": eligible,
            "lines": lines,
            "branches": branches,
            "downstream_deps": dep_count,
            "risk_level": risk,
        },
    )
```

### 第 4 层：Agent（意图转化器）

Agent 不再有 `ontology_action` 工具。它只有两个核心工具：

```python
@tool
def express_intent(intent_type: str, target: str, details: dict) -> str:
    """将用户意图转化为临时层数据。
    
    Args:
        intent_type: 意图类型 (refactor/document/query/alert)
        target: 目标实体名称
        details: 附加信息
    """
    # 在 Neo4j 临时层创建 UserRequest 节点
    # 规则引擎会自动匹配并触发后续 Action
    ...

@tool
def query_status(entity_name: str) -> str:
    """查询实体当前的处理流程状态。"""
    # 查询临时层中的 UserRequest、DiagnosisResult 等
    ...
```

Agent 的工作流程变为：
1. 用户说 "Cache 类太臃肿了，帮我重构"
2. Agent 识别意图，调用 `express_intent("refactor", "Cache", {"reason": "too large"})`
3. 临时层写入 UserRequest → 规则引擎自动触发 validate_refactor → Action 自动编排 Function
4. Agent 调 `query_status("Cache")` 告诉用户当前进度

## 三、场景完整流程

### 场景 1：代码重构决策

```
用户："Cache 类太臃肿了，帮我重构"

Agent → express_intent("refactor", "Cache", {...})
  → 临时层: UserRequest(type=refactor, target=Cache, status=pending)

规则引擎匹配: validate_refactor_request
  → Action: validate_refactor
    Function 1: check_entity_exists("Cache") → ✅ 存在
    Function 2: check_refactor_eligibility("Cache") → ✅ lines=320, 可重构
    Function 3: assess_impact("Cache", depth=3) → 下游8个调用方，风险低
    Function 4: update_request_status(req, "validated")
      → 临时层数据变化: UserRequest.status = validated

规则引擎匹配: execute_validated_refactor
  → Action: execute_refactor
    Function 1: analyze_complexity("Cache") → 圈复杂度12
    Function 2: generate_refactor_plan("Cache") → 拆分为3个方法
    Function 3: create_changeset("Cache", plan) → 创建 ChangeSetEntity
    Function 4: update_request_status(req, "completed")
      → 核心层新增: ChangeSetEntity
      → 规则引擎匹配: notify_on_new_changeset
        → Action: notify_stakeholders
          Function 1: find_affected_owners(changeset)
          Function 2: send_notification(owners)
          Function 3: mark_changeset_notified(changeset)

Agent → query_status("Cache")
  → 告知用户：重构完成，已通知8个下游模块负责人
```

### 场景 2：告警故障诊断闭环

```
外部系统推送: AlertEntity(name=ALM-0042, severity=P0, status=open)

规则引擎匹配: auto_diagnose_alert
  → Action: diagnose_alert
    Function 1: diagnose_by_log("ALM-0042") → NPE 高频模式
    Function 2: diagnose_by_chain("ALM-0042") → OrderService.process 故障
    Function 3: correlate_changeset("ALM-0042") → commit abc123 可疑
    Function 4: write_diagnosis_result(...)
    Function 5: update_alert_status("ALM-0042", "root_cause_confirmed")
      → 数据变化: AlertEntity.status = root_cause_confirmed

规则引擎匹配: auto_rollback_on_confirmed_root_cause
  → Action: rollback (需审批)
    Function 1: find_last_stable("ALM-0042") → abc122
    Function 2: generate_rollback_plan(...)
    → 挂起等待审批
    → 审批通过
    Function 3: execute_rollback(...)
    Function 4: update_alert_status("ALM-0042", "resolved")
```

### 场景 3：变更影响评估

```
开发者提交代码 → 构建 pipeline 检测到变更
→ 创建 ChangeSetEntity(commit=def456, files=["cache.py"])

规则引擎匹配: notify_on_new_changeset
  → Action: notify_stakeholders
    Function 1: find_affected_owners("def456") → [Cache下游8个调用方]
    Function 2: send_notification(owners, "cache.py 变更")
    Function 3: mark_changeset_notified("def456")
```

### 场景 4：代码文档补全

```
用户："Cache 模块缺文档，帮我补上"

Agent → express_intent("document", "Cache", {})
  → 临时层: UserRequest(type=document, target=Cache, status=pending)

规则引擎匹配: auto_document_low_coverage
  → Action: check_and_generate_docs
    Function 1: check_doc_coverage("Cache") → 覆盖率33%
    Function 2: generate_api_doc("Cache", coverage) (条件满足: 33% < 50%)
    Function 3: write_doc_entity("Cache", doc) → 创建 DocEntity + describes 关系
    Function 4: update_request_status(req, "completed")
```

## 四、代码改动范围

### 新增模块

| 模块 | 说明 |
|------|------|
| `src/layerkg/rules.py` | TriggerRule 定义 + 规则引擎 |
| `src/layerkg/orchestrator.py` | Action 编排器（解析 steps、按顺序调 Function） |
| `src/layerkg/functions/general.py` | 通用 Function（check_entity_exists, update_status 等） |
| `src/layerkg/functions/code.py` | 代码分析 Function（从 actions/code.py 迁移+升级） |
| `src/layerkg/functions/alert.py` | 告警诊断 Function（从 actions/alert.py 迁移+升级） |
| `src/layerkg/functions/changeset.py` | 变更管理 Function |
| `src/layerkg/functions/doc.py` | 文档生成 Function |
| `src/layerkg/reasoning_types.py` | FunctionResult 等类型定义 |

### 重写模块

| 模块 | 改动 |
|------|------|
| `schema.py` | 新增 TRIGGER_RULES |
| `ontology_actions.yaml` | 全新结构（actions + functions 分离） |
| `ontology_engine.py` | 从单次执行改为调度规则引擎+编排器 |
| `agent/tools.py` | 砍掉 9 个扁平工具，改为 express_intent + query_status |
| `agent/prompt.py` | 简化为意图识别+状态查询 |

### 删除模块

| 模块 | 原因 |
|------|------|
| `actions/code.py` | 迁移到 functions/code.py |
| `actions/alert.py` | 迁移到 functions/alert.py |

### 不动

| 模块 | 原因 |
|------|------|
| `schema.py` 实体/关系定义 | 复用 |
| `neo4j_store.py` | 需新增 get_node_by_name、监听能力 |
| `chroma_store.py` | 复用 |
| `parser/`、`extractor/` | 构建流程不变 |

## 五、与 V2.0 方案的关键区别

| 维度 | V2.0（Hermes 之前的方案） | V3.0（本方案） |
|------|------------------------|---------------|
| 触发方式 | Agent 主动调用 Action | Schema 规则自动触发 |
| Agent 角色 | 决策者（选工具、定顺序） | 意图转化器（写数据、查状态） |
| Action 内部 | 1 个执行函数 + requires 校验 | 多 Function 顺序编排 + 条件判断 |
| 闭环 | 无 | Function 更新数据 → 触发新规则 → 循环 |
| 安全 | 靠参数类型 | 靠临时层/核心层隔离 + 审批节点 |
| Schema 职责 | 实体 + 关系 | 实体 + 关系 + 触发规则 |

## 九、Claude Code 审核反馈（68/100）后的反思修正

### 反思 1：死循环防护

问题：数据变化→规则匹配→Action→Function→数据又变化，规则之间可能形成环。

解决方案：**注册时静态分析 + 运行时 depth 标记**

- 规则注册时，分析每个规则的"输入数据模式→输出数据模式"，构建依赖图，检测环。有环则拒绝注册。
- 运行时，每个 Action 执行完后在产生的数据上打 `triggered_by: {action_name, depth}` 标记，规则引擎匹配时检查 depth，超过 5 不再触发。

### 反思 2：触发规则

问题：手写 Cypher 性能差、难维护、没类型安全。

解决方案：**Python 条件函数代替 Cypher 字符串**

每个规则就是一个 Python 函数，返回匹配数据就触发，返回空就不触发。有类型提示、IDE 支持、可测试、可调试。不需要另造结构化 DSL。

### 反思 3：Action 编排

问题：YAML 里造工作流语言（steps + condition + parallel + on_failure）越来越复杂，等于重发明 Temporal。

解决方案：**编排逻辑用 Python 函数，YAML 只做声明式注册**

YAML 只声明：Action 名称、绑定实体类型、是否需要审批。编排逻辑（顺序、并行、条件、重试）全部用 Python asyncio 写。有 try/except、asyncio.gather、重试 — 都是 Python 原生能力。

### 反思 4：Agent 角色

问题：一刀切砍成 2 个工具，纯查询场景无法处理。

解决方案：**双层工具架构**

- 查询层：保留 semantic_search、get_context、impact_analysis 等现有工具
- 操作层：新增 express_intent + query_status
- Agent 根据 prompt 判断走哪层，不互斥

### 反思 5：临时层/核心层分离

问题：is_temporary 标签方案有并发冲突和数据隔离问题。

解决方案：**独立标签（TempXxx）+ 乐观锁 + TTL**

- 临时层用独立标签（TempRequest、TempDiagnosis），和核心层完全隔离
- 并发：实体名维度乐观锁
- 清理：created_at + 24h TTL 定时清理

### 反思 6：审批机制

问题：无超时、无通知、无拒绝后补偿。

解决方案：**超时策略 + Agent 主动通知 + 拒绝补偿**

- approval.timeout: 30m，超时自动 reject
- 审批挂起时 Agent 主动告知用户
- on_reject 定义补偿 Action

### 反思 7：迁移路径

解决方案：**3 阶段渐进迁移**

- Phase 1：新增规则引擎+编排器+functions/，不动现有代码
- Phase 2：Agent 新增 express_intent，新旧工具并行
- Phase 3：确认稳定后废弃旧工具和旧 ontology_engine

### 反思 8：测试策略

解决方案：**4 层测试**

- 层级 1：Function 单元测试（mock graph_store）
- 层级 2：Action 编排器测试（mock Function）
- 层级 3：规则引擎测试（内存 Neo4j + fixture）
- 层级 4：闭环集成测试（testcontainers + 真实 Neo4j）

## 十、扩展性设计：Function 分层 + Action 编排

### 核心原则

- **Function 是有限的积木块，Action 是无限的拼装方式**
- 新增场景只需编排已有 Function，不需要造新积木
- 只有进入新领域（如新数据源）才需要新增领域特定 Function

### Function 分层

#### 底层通用 Function（所有场景共用，数量有限）

| Function | 作用 | 自校验 |
|----------|------|--------|
| query_entity | 查实体属性+关系 | 检查实体存在性 |
| update_entity | 更新实体属性 | 检查属性合法性、实体存在性 |
| create_entity | 创建新实体 | 检查类型合法性、不重复 |
| create_relation | 创建关系 | 检查 domain/range 约束 |
| check_condition | 条件判断（属性值+关系组合） | 无副作用，只返回 true/false |
| send_notification | 发通知 | 检查接收人存在性 |

#### 领域特定 Function（按需扩展）

| Function | 作用 | 领域 |
|----------|------|------|
| analyze_complexity | 代码复杂度分析 | 代码分析 |
| analyze_log_pattern | 日志模式分析 | 运维监控 |
| trace_call_chain | 调用链追踪 | 代码分析 |
| semantic_search | 语义搜索 | 通用搜索 |

### 4 个场景全部复用通用 Function

```
场景1：代码重构
  Action: validate_refactor
    1. query_entity(Cache)               ← 通用
    2. check_condition(lines>100)        ← 通用
    3. update_entity(request, validated) ← 通用

场景2：告警诊断
  Action: diagnose_alert
    1. query_entity(ALM-0042)            ← 通用
    2. analyze_log_pattern(...)          ← 领域特定
    3. trace_call_chain(...)             ← 领域特定
    4. create_entity(DiagnosisResult)    ← 通用
    5. update_entity(alert, diagnosed)   ← 通用

场景3：变更通知
  Action: notify_stakeholders
    1. query_entity(ChangeSet)           ← 通用
    2. query_relation(affects→)          ← 通用
    3. send_notification(owners)         ← 通用

场景4：文档补全
  Action: generate_docs
    1. query_entity(Cache)               ← 通用
    2. check_condition(doc_coverage<50%) ← 通用
    3. create_entity(DocEntity)          ← 通用
    4. create_relation(doc, describes, code) ← 通用
```

### 扩展性矩阵

| 新增什么 | 需要做什么 |
|---------|-----------|
| 新场景（通用 Function 能覆盖） | 写触发规则 + Action 编排，**0 个新 Function** |
| 新领域（比如接了新的数据源） | 加 1-2 个领域特定 Function + Action 编排 |
| 新实体类型 | schema.py 加实体定义 + 触发规则 + Action 编排 |

### Function 自校验机制

每个 Function 内部强制执行安全校验，Agent 绕不开：

```python
@register_function("update_entity")
def update_entity(entity_name: str, properties: dict, **kwargs):
    graph_store = kwargs["graph_store"]
    
    # 前置校验 — 绕不开
    node = graph_store.get_node_by_name(entity_name)
    if not node:
        return FunctionResult(success=False, data={"reason": "entity not found"})
    
    # 属性合法性检查
    entity_type = node.get("entity_type")
    for key in properties:
        if not is_valid_property(entity_type, key):
            return FunctionResult(success=False, data={"reason": f"invalid property: {key}"})
    
    # 执行更新
    graph_store.update_node(node["id"], properties)
    return FunctionResult(success=True, data={"updated": properties})
```

## 十、Palantir Ontology 深度对标与修正

> 基于 Palantir Foundry Ontology 架构文章的完整对比，修正 V3.1 设计中的偏差

### 1. Palantir 的两层架构

```
语义层（Semantic Layer）：
  - Object Types + Properties    — "存在什么"
  - Link Types                    — "怎么连"
  - Interfaces                    — 多对象共享属性

动力层（Kinetic Layer）：
  - Action Types                  — "能做什么"（声明 + 参数 + 提交规则 + 审批）
  - Functions                     — "怎么做"（读语义层 + 业务逻辑 + 写语义层 + 外部API + 副作用）
```

**核心原则：语义层定义"存在什么"，动力层定义"能做什么"。两层之间没有交叉。**

### 2. 六要素关系对标

| Palantir 要素 | LayerKG 现状 | 差距 | 修正方向 |
|---|---|---|---|
| Object Types + Properties | ✅ 9实体+属性 | 对齐 | 无需改动 |
| Link Types | ✅ 15关系+约束 | 对齐 | 无需改动 |
| Interfaces（多对象共享属性） | ❌ 无 | 缺失 | 后期补充 |
| Action Types | ⚠️ 有框架 | 缺 Submission Criteria | **必须补** |
| Functions（读写语义层） | ❌ 只读建议 | **核心差距** | **必须补** |
| Signal（触发规则） | ⚠️ 有设计 | 触发规则和提交规则混在一起 | **必须分离** |

### 3. 7 个关键修正

#### 修正 1：触发规则和提交规则分离

Palantir 有两个独立的机制：

```
触发规则（Signal）：什么时候自动调 Action
  → Equipment.temperature > 95 → 触发 CreateWorkOrder

提交规则（Submission Criteria）：调了之后能不能执行
  → Equipment.status == "故障" → 允许执行
  → Equipment.status == "正常" → 拒绝执行
```

V3.1 的错误：把这两个混在 Python 条件函数里。

修正后：

```python
# YAML 声明
actions:
  validate_refactor:
    trigger: layerkg.rules:on_refactor_request        # 触发规则
    submission_criteria: layerkg.rules:can_refactor    # 提交规则
    function: layerkg.functions.refactor:validate      # 执行函数
    approval: false

# 触发规则 — 什么时候触发
def on_refactor_request(event, graph_store) -> list[dict] | None:
    """用户提交重构请求时触发。"""
    if event.event_type != "node.merged": return None
    if event.payload.get("label") != "TempRequest": return None
    if event.payload.get("properties", {}).get("request_type") != "refactor": return None
    return [event.payload["properties"]]

# 提交规则 — 能不能执行
def can_refactor(match_data, graph_store) -> tuple[bool, str]:
    """检查目标实体是否可重构。"""
    target = match_data.get("target")
    node = graph_store.get_node_by_name(target)
    if not node:
        return False, f"实体 {target} 不存在"
    if node.get("lines", 0) <= 50 and node.get("branches", 0) <= 10:
        return False, f"实体 {target} 不满足重构条件（行数≤50且分支≤10）"
    return True, "通过"
```

#### 修正 2：Function 统一为语义层操作，不分"推理/执行"

Palantir 的 Function 是语义层的完整操作接口：
- **读**：查询对象属性、沿 Link 导航、聚合计算
- **写**：创建/修改 Object、建立/删除 Link、更新 Properties
- **外部**：调用 API（ERP、CMMS、钉钉等）
- **副作用**：发通知、Webhook、审计日志

V3.1 的错误：把 Function 拆成"通用"和"领域"两类，暗示有"只读推理"和"变更执行"的区别。

修正后：Function 就是 Function，每个都能读能写，具体做什么由业务逻辑决定。

```python
def execute_refactor(entity_name: str, **kwargs) -> FunctionResult:
    """重构函数 — 读语义层 + 计算方案 + 写语义层 + 通知。"""
    graph_store = kwargs["graph_store"]
    
    # 读语义层
    node = graph_store.get_node_by_name(entity_name)
    deps = graph_store.query("MATCH (c:CodeEntity {name: $name})<-[:CALLS*1..3]-(caller) ...")
    
    # 业务逻辑
    plan = generate_plan(node, deps)
    
    # 写语义层 — 创建变更集
    graph_store.merge_node("ChangeSetEntity", {
        "id": str(uuid4()),
        "target": entity_name,
        "plan": json.dumps(plan),
        "status": "pending",
    })
    
    # 副作用 — 通知下游
    notify_affected_owners(deps, plan)
    
    return FunctionResult(success=True, data={"plan": plan})
```

#### 修正 3：新增 Submission Criteria 机制

每个 Action 定义独立的提交规则，在 Function 执行之前校验。这是 Agent 绕不开的强制关卡。

```yaml
actions:
  rollback:
    trigger: layerkg.rules:on_root_cause_confirmed
    submission_criteria:
      - field: alert.severity
        operator: in
        value: [P0, P1]
      - field: alert.has_suspect_changeset
        operator: eq
        value: true
    function: layerkg.functions.alert:execute_rollback
    approval:
      required: true
      timeout: 1800
      on_timeout: reject
      on_reject: cleanup_rollback_request
```

提交规则可以是：
- **声明式**（如上 YAML，适合简单条件）
- **函数式**（Python 函数，适合复杂逻辑）

简单条件用声明式（可读性好），复杂逻辑用函数式（灵活性强）。

#### 修正 4：新增 Side Effects 机制

Function 执行后可以触发副作用，不直接写在 Function 内部，而是通过事件总线解耦。

```python
# Function 执行完发射副作用事件
@side_effect("notification")
def notify_affected_owners(deps, plan):
    """通知受影响的模块负责人。"""
    for dep in deps:
        send_dingtalk(dep["owner"], f"重构计划：{plan['summary']}")

@side_effect("audit")
def log_refactor(entity_name, plan):
    """记录审计日志。"""
    graph_store.merge_node("AuditEntry", {
        "action": "refactor",
        "entity": entity_name,
        "plan": json.dumps(plan),
        "timestamp": datetime.now(UTC).isoformat(),
    })
```

#### 修正 5：Action 是 Function 的壳，不需要外部编排器

Palantir 的模式：

```
Action = 参数校验 + Submission Criteria + 审批 + 调用 Function
```

Action 不需要外部编排器来决定调用哪些 Function、按什么顺序。Function 内部自己编排：
- 需要并行？用 `asyncio.gather`
- 需要条件判断？用 `if/else`
- 需要重试？用 `try/except`

V3.1 的错误：设计了 `orchestrators/` 目录，把编排逻辑和 Function 分离。

修正后：**删除 orchestrators/ 目录**，编排逻辑就在 Function 内部。Action 只是一个壳。

```yaml
# 简化的 YAML — 只有声明，没有编排
actions:
  validate_refactor:
    trigger: layerkg.rules:on_refactor_request
    submission_criteria: layerkg.rules:can_refactor
    function: layerkg.functions.refactor:validate
    approval: false
    timeout: 300

  rollback:
    trigger: layerkg.rules:on_root_cause_confirmed
    submission_criteria: layerkg.rules:can_rollback
    function: layerkg.functions.alert:execute_rollback
    approval:
      required: true
      timeout: 1800
```

Function 内部自己处理编排：

```python
async def execute_rollback(match_data, graph_store) -> FunctionResult:
    alert_name = match_data["alert_name"]
    
    # 串行编排（不需要外部编排器）
    stable = find_last_stable(alert_name, graph_store)
    plan = generate_rollback_plan(alert_name, stable, graph_store)
    
    # 等审批（Action 壳处理，Function 不感知）
    execute_rollback_in_cmms(plan)
    
    # 写语义层
    graph_store.update_node("AlertEntity", alert_name, {"status": "resolved"})
    
    # 副作用
    notify_team(alert_name, plan)
    
    return FunctionResult(success=True, data={"rollback_to": stable["version"]})
```

#### 修正 6：Function 内部是事务性的

多步写操作要么全成功要么回滚。

```python
def execute_inventory_transfer(sku, from_id, to_id, quantity, **kwargs):
    graph_store = kwargs["graph_store"]
    
    try:
        # Step 1: 扣减源仓
        graph_store.update_node("Warehouse", from_id, 
            {"stock_" + sku: get_stock(from_id, sku) - quantity})
        
        # Step 2: 增加目标仓
        graph_store.update_node("Warehouse", to_id,
            {"stock_" + sku: get_stock(to_id, sku) + quantity})
        
        # Step 3: 记录日志
        graph_store.merge_node("TransferLog", {...})
        
    except Exception as e:
        # 回滚 — 恢复原值
        graph_store.update_node("Warehouse", from_id, original_values)
        graph_store.update_node("Warehouse", to_id, original_values)
        return FunctionResult(success=False, data={"error": str(e)})
```

#### 修正 7：落地优先级调整

Palantir 的落地建议：

```
初期：Object Types + Link Types（建好业务模型）
中期：Action Types + Submission Criteria（实现操作闭环）
后期：Functions（复杂集成和智能逻辑）

MVP：3-5 个核心 Object + 必要 Link + 1-2 个 Action + 简单 Function
```

LayerKG 的当前状态和优先级调整：

| 阶段 | Palantir 建议 | LayerKG 状态 | 下一步 |
|------|-------------|-------------|--------|
| 初期 | 3-5 Object + Link | ✅ 9实体15关系 | **停止加实体** |
| 中期 | 1-2 Action + Submission Criteria | ⚠️ 有框架缺SC | **现在做这个** |
| 后期 | Function 写语义层 | ❌ 只读建议 | 中期完成后做 |

**LayerKG 犯了"重要性错配"：** Schema 层投入过多（9实体15关系），Action/Function 层投入不足。现在应该停下来补 Action 和 Function。

### 4. 修正后的完整架构

```
┌─────────────────────────────────────────┐
│     前端（Workshop Dashboard）            │
│     WebSocket 实时推送 + 用户操作入口      │
└──────────────┬──────────────────────────┘
               │ 用户操作 / API 调用
               ▼
┌─────────────────────────────────────────┐
│          Agent（意图转化器）              │
│     查询层：7 个只读工具                  │
│     操作层：express_intent + query_status │
└──────────────┬──────────────────────────┘
               │ express_intent → 写临时层
               ▼
┌─────────────────────────────────────────┐
│       知识图谱（Neo4j + ChromaDB）        │
│     核心层：可信数据（构建时写入）         │
│     临时层：TempRequest/TempApproval     │
└──────────────┬──────────────────────────┘
               │ 数据变化 → EventBus
               ▼
┌─────────────────────────────────────────┐
│          规则引擎 (RuleEngine)            │
│     1. 接收 EventBus 事件                │
│     2. 匹配触发规则 (trigger)            │
│     3. 调用 Action 壳                    │
└──────────────┬──────────────────────────┘
               │ 触发 Action
               ▼
┌─────────────────────────────────────────┐
│          Action 壳 (ActionShell)          │
│     1. 参数校验                          │
│     2. Submission Criteria 检查          │
│     3. 审批（如需要）                    │
│     4. 调用 Function                     │
│     5. 发射 Side Effects                 │
└──────────────┬──────────────────────────┘
               │ 调用 Function
               ▼
┌─────────────────────────────────────────┐
│          Function 执行层                  │
│     读语义层 + 业务逻辑 + 写语义层        │
│     + 外部API + 事务性写操作              │
│     执行后数据变化 → EventBus → 闭环     │
└─────────────────────────────────────────┘
```

### 5. 修正后的 YAML 结构

```yaml
# ontology_actions.yaml — V3.2（基于 Palantir 对标修正）

# ===== Action 声明 =====
actions:
  # --- 代码重构 ---
  validate_refactor:
    description: "验证重构请求"
    trigger: layerkg.rules:on_refactor_request
    submission_criteria: layerkg.rules:can_refactor
    function: layerkg.functions.refactor:validate
    approval: false
    timeout: 300

  execute_refactor:
    description: "执行重构"
    trigger: layerkg.rules:on_refactor_validated
    submission_criteria: layerkg.rules:refactor_plan_ready
    function: layerkg.functions.refactor:execute
    approval: false
    timeout: 600

  delete_code:
    description: "删除代码实体（高风险）"
    trigger: layerkg.rules:on_delete_request
    submission_criteria: layerkg.rules:can_delete
    function: layerkg.functions.refactor:execute_delete
    approval:
      required: true
      timeout: 1800
      on_timeout: reject
      on_reject: cleanup_delete_request
    timeout: 3600

  # --- 告警处理 ---
  diagnose_alert:
    description: "告警自动诊断"
    trigger: layerkg.rules:on_critical_alert
    submission_criteria: layerkg.rules:alert_is_open
    function: layerkg.functions.alert:diagnose
    approval: false
    timeout: 600

  rollback:
    description: "回滚到稳定版本"
    trigger: layerkg.rules:on_root_cause_confirmed
    submission_criteria: layerkg.rules:has_suspect_changeset
    function: layerkg.functions.alert:execute_rollback
    approval:
      required: true
      timeout: 1800
      on_timeout: reject
      on_reject: cleanup_rollback_request
    timeout: 3600

  notify:
    description: "创建工单通知"
    trigger: layerkg.rules:on_diagnosis_complete
    submission_criteria: layerkg.rules:diagnosis_has_root_cause
    function: layerkg.functions.alert:create_ticket
    approval: false
    timeout: 120

  # --- 文档补全 ---
  generate_docs:
    description: "检查覆盖率并生成文档"
    trigger: layerkg.rules:on_document_request
    submission_criteria: layerkg.rules:entity_has_code
    function: layerkg.functions.doc:generate
    approval: false
    timeout: 300

  # --- 变更通知 ---
  notify_stakeholders:
    description: "通知受变更影响的负责人"
    trigger: layerkg.rules:on_new_changeset
    submission_criteria: layerkg.rules:changeset_has_affected
    function: layerkg.functions.changeset:notify
    approval: false
    timeout: 120

# ===== Function 注册表 =====
functions:
  # --- 重构 ---
  refactor.validate:
    implementation: layerkg.functions.refactor:validate
  refactor.execute:
    implementation: layerkg.functions.refactor:execute
  refactor.execute_delete:
    implementation: layerkg.functions.refactor:execute_delete

  # --- 告警 ---
  alert.diagnose:
    implementation: layerkg.functions.alert:diagnose
  alert.execute_rollback:
    implementation: layerkg.functions.alert:execute_rollback
  alert.create_ticket:
    implementation: layerkg.functions.alert:create_ticket

  # --- 文档 ---
  doc.generate:
    implementation: layerkg.functions.doc:generate

  # --- 变更 ---
  changeset.notify:
    implementation: layerkg.functions.changeset:notify
```

### 6. 修正后的 Python 条件函数

```python
# src/layerkg/rules.py

# === 触发规则（什么时候触发 Action）===

def on_refactor_request(event: GraphEvent, graph_store) -> list[dict] | None:
    """用户提交重构请求时触发。"""
    if event.event_type != "node.merged": return None
    if event.payload.get("label") != "TempRequest": return None
    props = event.payload.get("properties", {})
    if props.get("request_type") != "refactor" or props.get("status") != "pending": return None
    return [{"request_id": props["id"], "target": props["target"]}]

def on_refactor_validated(event: GraphEvent, graph_store) -> list[dict] | None:
    """重构请求验证通过后触发。"""
    if event.event_type != "node.updated": return None
    if event.payload.get("label") != "TempRequest": return None
    props = event.payload.get("properties", {})
    if props.get("request_type") != "refactor" or props.get("status") != "validated": return None
    return [{"request_id": props["id"], "target": props["target"]}]

def on_critical_alert(event: GraphEvent, graph_store) -> list[dict] | None:
    """P0/P1 告警创建时触发。"""
    if event.event_type != "node.merged": return None
    if event.payload.get("label") != "AlertEntity": return None
    props = event.payload.get("properties", {})
    if props.get("severity") not in ("CRITICAL", "HIGH") or props.get("status") != "open": return None
    return [{"alert_id": props["id"], "alert_name": props["name"]}]

def on_root_cause_confirmed(event: GraphEvent, graph_store) -> list[dict] | None:
    """根因确认后触发回滚。"""
    if event.event_type != "node.updated": return None
    if event.payload.get("label") != "AlertEntity": return None
    if event.payload.get("properties", {}).get("status") != "root_cause_confirmed": return None
    changesets = graph_store.query(
        "MATCH (a:AlertEntity {id: $id})<-[:AFFECTS]-(cs:ChangeSetEntity) RETURN cs",
        {"id": event.payload["properties"]["id"]},
    )
    if not changesets: return None
    return [{"alert_id": event.payload["properties"]["id"], "changesets": changesets}]

def on_new_changeset(event: GraphEvent, graph_store) -> list[dict] | None:
    """新变更集创建时触发通知。"""
    if event.event_type != "node.merged": return None
    if event.payload.get("label") != "ChangeSetEntity": return None
    if event.payload.get("properties", {}).get("notified"): return None
    return [{"changeset_id": event.payload["properties"]["id"]}]

# === 提交规则（能不能执行 Action）===

def can_refactor(match_data: dict, graph_store) -> tuple[bool, str]:
    """检查目标实体是否可重构。"""
    target = match_data.get("target")
    node = graph_store.get_node_by_name(target)
    if not node: return False, f"实体 {target} 不存在"
    if node.get("lines", 0) <= 50 and node.get("branches", 0) <= 10:
        return False, f"{target} 不满足重构条件"
    return True, "通过"

def can_delete(match_data: dict, graph_store) -> tuple[bool, str]:
    """检查是否能删除。"""
    target = match_data.get("target")
    deps = graph_store.query(
        "MATCH (c:CodeEntity {name: $name})<-[:CALLS*1..3]-(caller) RETURN count(*) AS cnt",
        {"name": target},
    )
    cnt = deps[0]["cnt"] if deps else 0
    if cnt > 50: return False, f"{target} 有 {cnt} 个下游依赖，风险过高"
    return True, "通过"

def alert_is_open(match_data: dict, graph_store) -> tuple[bool, str]:
    """检查告警是否仍在 open 状态。"""
    alert = graph_store.get_node(match_data["alert_id"])
    if not alert: return False, "告警不存在"
    if alert.get("status") != "open": return False, f"告警状态为 {alert['status']}，非 open"
    return True, "通过"

def has_suspect_changeset(match_data: dict, graph_store) -> tuple[bool, str]:
    """检查是否有关联的可疑变更集。"""
    if not match_data.get("changesets"): return False, "无可疑变更集"
    return True, "通过"
```

### 7. 修正后的代码改动范围

#### 新增模块

| 文件 | 说明 |
|------|------|
| `src/layerkg/rules.py` | 触发规则 + 提交规则 函数 |
| `src/layerkg/action_shell.py` | Action 壳（参数校验 + SC检查 + 审批 + 调 Function） |
| `src/layerkg/functions/refactor.py` | 重构 Function（读+写语义层） |
| `src/layerkg/functions/alert.py` | 告警 Function（读+写语义层+外部API） |
| `src/layerkg/functions/doc.py` | 文档 Function |
| `src/layerkg/functions/changeset.py` | 变更通知 Function |
| `src/layerkg/side_effects.py` | Side Effects 机制（通知、Webhook、审计） |
| `src/layerkg/reasoning_types.py` | GraphEvent, FunctionResult, ActionContext 等类型 |

#### 重写模块

| 文件 | 改动 |
|------|------|
| `ontology_engine.py` | 从单次执行改为 ActionShell 调度 |
| `ontology_actions.yaml` | 全新结构（trigger + submission_criteria + function） |
| `agent/tools.py` | 新增 express_intent + query_status，保留查询层工具 |
| `agent/prompt.py` | 双层工具引导 |

#### 删除模块

| 文件 | 原因 |
|------|------|
| `actions/code.py` | 迁移到 functions/refactor.py |
| `actions/alert.py` | 迁移到 functions/alert.py |
| `orchestrators/` | **删除**（Palantir 模式不需要外部编排器，Function 内部自己编排） |

#### 不动

| 模块 | 原因 |
|------|------|
| `schema.py` | 实体和关系定义不变 |
| `neo4j_store.py` | 需新增事件发射钩子 |
| `chroma_store.py` | 复用 |
| `parser/` `extractor/` | 构建流程不变 |
| `butler/event_bus.py` | 复用，作为规则引擎事件源 |

### 8. 落地路线图（基于 Palantir 落地建议）

```
Phase 1（MVP — 证明闭环跑通）：
  - 1 个 Action：validate_refactor
  - 触发规则 + 提交规则 + 简单 Function（能读能写语义层）
  - EventBus 事件发射 + 规则引擎基础框架
  - Agent 新增 express_intent 工具
  - 前端 WebSocket 推送（可选）

Phase 2（补全核心 Action）：
  - 新增 diagnose_alert、rollback、generate_docs
  - 审批机制完善
  - Side Effects 机制（通知、审计）

Phase 3（复杂集成）：
  - 外部 API 集成（CMMS、ERP 连接器）
  - Function 事务性写操作
  - 前端 Workshop Dashboard 完整闭环
  - 废弃旧 ontology_engine.py
```
