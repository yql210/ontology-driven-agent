# OntoAgent 审批系统操作指南

> **版本**: V3.4 | **更新日期**: 2026-06-28 | **字数**: ~3200 字

---

## 1. 审批概览

OntoAgent 审批系统采用**三层架构**，在 Action 执行前进行多级约束检查与审批决策：

```
┌─────────────────────────────────────────────────┐
│  三层审批架构                                     │
│                                                 │
│  Layer 3: FunctionDangerPolicy                  │
│    根据 function danger_level 决定是否审批       │
│    ┌──────────────────────────────────────┐     │
│    │ Layer 2: ActionApprovalPolicy        │     │
│    │   检查 ActionConfig.requires_approval │     │
│    │   ┌────────────────────────────┐     │     │
│    │   │ Layer 1: GuardResultPolicy │     │     │
│    │   │   Guard Pipeline 结果→审批  │     │     │
│    │   └────────────────────────────┘     │     │
│    └──────────────────────────────────────┘     │
└─────────────────────────────────────────────────┘
```

审批分为**两个粒度**：

| 粒度 | 触发点 | 审批令牌位置 | 说明 |
|------|--------|-------------|------|
| **Action 级** | `express_intent` 中 `ApprovalGate.check()` 返回 `PENDING` | `express_intent` 响应中的 `approval_id` | 整个 Action 暂停，等待用户批准后继续执行其 function 链 |
| **Function 级** | `FunctionRunner.run()` 中检测 function `danger_level` 触发 | `FunctionResult.data.approval_token` | Action 执行到特定 function 时暂停，仅该 function 需要审批 |

配置目录：`src/ontoagent/config/`，审批相关共 **5 个 YAML 配置文件** + `pipeline/` 下 **2 个 YAML 配置文件**。

---

## 2. 配置审批策略 (approval_policy.yaml)

**路径**：`src/ontoagent/config/approval_policy.yaml`

此文件控制 `ApprovalGate` 审批门的全部行为。

### 2.1 完整配置结构

```yaml
# 启用的策略（按执行顺序评估）
policies:
  - guard_result       # GuardResultPolicy：根据 guard pipeline 结果
  - action_approval    # ActionApprovalPolicy：根据 ActionConfig.requires_approval
  - function_danger    # FunctionDangerPolicy：根据 function.danger_level

# GuardResultPolicy 配置
guard_result:
  on_block: require_approval    # BLOCK 时行为：require_approval | auto_reject
  on_warn: require_approval     # WARN 时行为：require_approval | auto_allow | auto_reject

# FunctionDangerPolicy 配置
function_danger:
  auto_approve:                 # 自动放行的 danger_level 列表
    - read
  require_approval:             # 必须审批的 danger_level 列表
    - read_sensitive
    - write
    - admin

# 审批令牌配置
token:
  ttl: 600           # 秒，令牌有效期（超时自动失效）
  max_pending: 10    # 同时最多待审批数（超出则自动拒绝）
```

### 2.2 字段详解

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `policies` | `list[str]` | `[guard_result, action_approval, function_danger]` | 策略执行顺序。可移除某项以禁用该策略 |
| `guard_result.on_block` | `str` | `require_approval` | BLOCK 级约束被触发时：`require_approval`=暂停审批，`auto_reject`=直接拒绝 |
| `guard_result.on_warn` | `str` | `require_approval` | WARN 级约束被触发时：`require_approval`=暂停审批，`auto_allow`=自动放行，`auto_reject`=直接拒绝 |
| `function_danger.auto_approve` | `list[str]` | `[read]` | 危险级别为此列表中任一值的 function 自动放行 |
| `function_danger.require_approval` | `list[str]` | `[read_sensitive, write, admin]` | 危险级别为此列表中任一值的 function 需要审批 |
| `token.ttl` | `int` | `600` | 令牌有效期（秒），超时后 `gate.resolve()` 返回 `None` |
| `token.max_pending` | `int` | `10` | 最大待审批数，超出后新审批直接 `DENIED` |

### 2.3 三个 Policy 工作方式

| Policy | 类名 | 评估逻辑 |
|--------|------|---------|
| `guard_result` | `GuardResultPolicy` | 运行 `ActionGuardPipeline.check()`，收集 BLOCK/WARN 结果 → 根据 `on_block`/`on_warn` 配置决定 `PENDING`/`DENIED`/`APPROVED` |
| `action_approval` | `ActionApprovalPolicy` | 读取 `ActionConfig.requires_approval` 字段 → `True` 则 `PENDING`，`False` 则 `APPROVED` |
| `function_danger` | `FunctionDangerPolicy` | 从 `function_danger_levels.yaml` 查找 function 的 `danger_level` → 匹配 `require_approval_levels` 则 `PENDING` |

策略链按 `policies` 顺序依次执行：任一返回 `DENIED` 则立即终止；全部通过则取最高级别（`PENDING` > `APPROVED`）。

---

## 3. 配置 Function 危险级别 (function_danger_levels.yaml)

**路径**：`src/ontoagent/config/function_danger_levels.yaml`

### 3.1 四个危险级别

| 级别 | 含义 | 默认行为 |
|------|------|---------|
| `read` | 纯查询操作，无副作用 | **直接放行**（`auto_approve`） |
| `write` | 修改数据，有持久副作用 | **暂停审批**（`require_approval`） |
| `read_sensitive` | 查询敏感数据（如信用卡号） | **预览+审批**（`require_approval`） |
| `admin` | 系统级操作（如创建实体） | **强制审批**（`require_approval`） |

### 3.2 完整配置

```yaml
# 默认值（未在此文件中的 function 使用此级别）
default: read

functions:
  check_refactor_eligibility: read
  generate_api_doc: write
  extract_interface: write
  check_compliance: read
  trace_call_chain: read
  trace_business_impact: read_sensitive
  query_entity: read
  update_entity: write
  create_entity: admin
  create_relation: write
  check_condition: read
  send_notification: write
```

### 3.3 已注册的 12 个 Function

| # | Function 名 | danger_level | 类别 | 说明 |
|---|-------------|-------------|------|------|
| 1 | `check_refactor_eligibility` | `read` | 内建 | 检查代码重构资格（行数/复杂度分析） |
| 2 | `generate_api_doc` | `write` | 内建 | 生成 API 文档 Markdown |
| 3 | `extract_interface` | `write` | 内建 | 提取类公开接口 |
| 4 | `check_compliance` | `read` | 合规 | 合规性检查 |
| 5 | `trace_call_chain` | `read` | 内建 | 追踪调用链（CALLS 关系） |
| 6 | `trace_business_impact` | `read_sensitive` | 业务 | 追踪业务影响范围（跨本体传播） |
| 7 | `query_entity` | `read` | 通用 | 查询实体属性+关系 |
| 8 | `update_entity` | `write` | 通用 | 更新实体属性 |
| 9 | `create_entity` | `admin` | 通用 | 创建新实体（需白名单校验） |
| 10 | `create_relation` | `write` | 通用 | 创建关系 |
| 11 | `check_condition` | `read` | 通用 | 条件判断（无副作用） |
| 12 | `send_notification` | `write` | 通用 | 发送通知 |

> **注意**：未在 `functions` 中列出的 function 使用 `default` 值（当前为 `read`）。危险级别通过 YAML 加载后覆盖函数装饰器中的声明值，YAML 优先级最高。

---

## 4. 配置约束覆盖 (constraint_overrides.yaml)

**路径**：`src/ontoagent/config/constraint_overrides.yaml`

此文件允许管理员**不修改遍历路径定义**的情况下，调整实体级约束行为。覆盖优先级高于本体自动推导。

### 4.1 三种覆盖类型

#### 类型 1: patch — 局部修改已有约束的值映射

```yaml
overrides:
  - type: patch
    target: data_sensitivity        # 对应 constraints.yaml 中 traversal_constraints 的 key
    modify:                         # 修改已有值的约束级别
      restricted: "warn"            # 将 restricted 从 block 降级为 warn
    remove_values: ["confidential"] # 移除 confidential 约束（等同放行）
    add_values:                     # 新增值的约束
      archived: "block"
```

| 子字段 | 说明 |
|--------|------|
| `target` | 目标遍历约束名称，对应 `constraints.yaml` 中 `traversal_constraints` 的 key |
| `modify` | `{值: GuardLevel}` — 修改已有属性值对应的约束级别 |
| `remove_values` | `list[str]` — 从 value_mapping 中移除的值（该值不再触发约束） |
| `add_values` | `{值: GuardLevel}` — 新增属性值及其约束级别 |

#### 类型 2: allow_all — 操作+实体级白名单

```yaml
overrides:
  - type: allow_all
    target_entity: "CodeEntity:validate_credit_card"  # 格式: {Neo4jLabel}:{entity_name}
    reason: "已脱敏测试数据，安全评审通过"
    expires: "2026-07-15"  # 可选：过期日期
```

`allow_all` 将实体加入 `allow_set`，`WhitelistGuard`（Pipeline 第一个 guard）检测到白名单实体后会跳过后续所有 guard 检查。

| 子字段 | 必须 | 说明 |
|--------|------|------|
| `target_entity` | 是 | 格式 `{Neo4jLabel}:{entity_name}`，如 `CodeEntity:validate_credit_card` |
| `reason` | 否 | 豁免原因（审计用） |
| `expires` | 否 | 过期日期，超期后需手动移除 |

#### 类型 3: add_constraint — 追加额外约束

```yaml
overrides:
  - type: add_constraint
    constraint:
      name: compliance_check
      source_label: "CodeEntity"
      relation_chain: ["SUBJECT_TO"]
      target_label: "ComplianceItem"
      collect_property: "severity"
      aggregation: "max"
```

新增的约束会自动查找 `ONTOLOGY_CONSTRAINT_REGISTRY` 获取 value_mapping。若 registry 中无对应条目，需在 constraint 中显式提供 `value_mapping`。

---

## 5. 配置工具网关 (tool_gateway.yaml)

**路径**：`src/ontoagent/config/tool_gateway.yaml`

工具网关在 Agent 调用 `graph_query` 工具时拦截写操作，防止 Agent 通过 Cypher 直接修改数据库。

### 5.1 完整配置

```yaml
# 是否启用写操作拦截
enabled: true

# 拦截的 Cypher 写操作关键字（不区分大小写）
blocked_keywords:
  - SET
  - DELETE
  - REMOVE
  - CREATE
  - MERGE
  - DROP
  - "DETACH DELETE"
  - FOREACH
  - "CALL apoc"
```

### 5.2 字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用拦截。设为 `false` 则所有 Cypher 查询直接放行 |
| `blocked_keywords` | `list[str]` | 见上表 | 不区分大小写的关键字列表。Cypher 中包含任一关键字则拒绝执行 |

拦截逻辑位于 `agent/tools.py` 的 `graph_query` 函数：

```python
allowed, reason = validate_graph_query(cypher)
if not allowed:
    return json.dumps({"error": reason})
```

---

## 6. 配置约束遍历路径 (constraints.yaml)

**路径**：`src/ontoagent/pipeline/constraints.yaml`

定义本体图中的遍历路径和传播规则，由 `ConstraintEngine` 和 `ConstraintPropagator` 在 guard pipeline 中使用。

### 6.1 遍历约束 (traversal_constraints)

```yaml
traversal_constraints:
  data_sensitivity:
    name: data_sensitivity
    source_label: "CodeEntity"
    relation_chain: ["PROCESSES_DATA"]
    target_label: "DataAsset"
    collect_property: "sensitivity"
    aggregation: "max"
```

| 字段 | 说明 |
|------|------|
| `name` | 约束名称（唯一标识） |
| `source_label` | 源实体 Neo4j Label |
| `relation_chain` | 关系链（沿此路径遍历） |
| `target_label` | 目标实体 Neo4j Label |
| `collect_property` | 在目标实体上收集的属性名 |
| `aggregation` | 聚合策略：`max`（取最严） / `exists`（存在即触发） |

value_mapping 由 `OntologyConstraintLoader` 从 `ONTOLOGY_CONSTRAINT_REGISTRY` 自动填充，例如 `DataAsset.sensitivity` → `{public: allow, internal: warn, restricted: block, confidential: block}`。

### 6.2 传播规则 (propagation_rules)

```yaml
propagation_rules:
  upstream_risk:
    name: upstream_risk
    along: ["CALLS"]
    direction: "backward"
    max_depth: 5
    collect_property: "entryCategory"
    aggregation: "exists"
```

| 字段 | 说明 |
|------|------|
| `name` | 规则名称 |
| `along` | 沿此关系类型传播 |
| `direction` | 传播方向：`forward` / `backward` |
| `max_depth` | 最大传播深度 |
| `collect_property` | 在传播路径节点上收集的属性 |
| `aggregation` | 聚合策略 |

---

## 7. 审批流程详解

### 7.1 完整链路（Action 级审批）

```
用户输入 → Agent 匹配 intent_type
  └→ express_intent(intent_type, target, params)     [agent/tools.py]
       │
       ├── 1. 解析实体：executor._resolve_entity(target)
       ├── 2. 获取 ActionConfig：executor.intent_map[intent_type]
       │
       ├── 3. 运行 Guard Pipeline：
       │      pipeline: WhitelistGuard → EntityExistsGuard → EntityPropertyGuard
       │              → OntologyTraversalGuard → OntologyPropagationGuard
       │      返回：(block_reason | None, warnings: list[str])
       │
       ├── 4. 构建 ApprovalContext：
       │      { intent_type, target, params, entity, guard_checks, session_id }
       │
       ├── 5. ApprovalGate.check(context, config=config, graph_store=neo4j)
       │      │
       │      ├── GuardResultPolicy.evaluate()
       │      │     ├── BLOCK + on_block="require_approval" → PENDING
       │      │     ├── BLOCK + on_block="auto_reject"     → DENIED
       │      │     ├── WARN  + on_warn="require_approval" → PENDING
       │      │     └── 无问题                               → APPROVED
       │      │
       │      ├── ActionApprovalPolicy.evaluate()
       │      │     └── config.requires_approval == True → PENDING
       │      │
       │      └── FunctionDangerPolicy.evaluate()
       │            └── danger_level in require_approval_levels → PENDING
       │
       ├── DENIED  → 返回 {"status": "blocked", ...}
       ├── PENDING → 生成一次性令牌，返回 {"status": "approval_required", ...}
       └── APPROVED → 继续执行
```

### 7.2 前端审批交互

```
express_intent 返回 approval_required
  └→ 前端 chat store detectApprovalData() 检测 status == "approval_required"
       └→ 插入 ApprovalCard 组件
            │
            ├── 用户点击 [批准执行]
            │     └→ POST /api/chat/approval  { approval_id, approved: true }
            │          └→ gate.resolve(token, approved=True)
            │               ├── 验证令牌 (是否在 _pending 中)
            │               ├── 检查 TTL  (is_expired)
            │               ├── 检查 scope (intent_type + target 绑定)
            │               ├── 记录审计
            │               └── 执行：executor.execute(..., bypass_function_approval=True)
            │                    └── 返回执行结果
            │
            └── 用户点击 [拒绝]
                  └→ POST /api/chat/approval  { approval_id, approved: false }
                       └→ gate.resolve(token, approved=False)
                            └→ 返回 {"status": "rejected", ...}
```

### 7.3 Function 级审批（Action 执行中触发）

```
ActionExecutor.execute() → FunctionRunner.run(func_name, ctx)
  └── 检测 bypass_approval=False 且 approval_gate 已接线
       └── FunctionDangerPolicy 评估 func_name 的 danger_level
            └── PENDING → 返回 FunctionResult(success=False, data={"approval_required": True, "approval_token": "..."})
                 └── ActionExecutor 检测到 data.approval_required
                      └── 返回 ActionResult(success=False, error="需要审批")
                           └── express_intent 检测 action 结果中的 approval 标记
                                └── 返回 {"status": "approval_required", "level": "function", ...}
```

### 7.4 express_intent 双模式

| 模式 | 参数 | 行为 |
|------|------|------|
| **正常模式** | `intent_type + target + params` | 约束检查 → 审批门 → 执行 / 返回审批请求 |
| **审批回执模式** | `approval_id + approved` | 验证令牌 → 跳过 guard+审批检查 (`bypass_guard=True`) → 直接执行 |

审批回执模式中，`approval_id` 传入 express_intent，内部调用：

```python
ctx = approval_gate.resolve(approval_id, approved)
# ... 执行时设置 skip_approval=True，绕过二次 guard 检查
result = executor.execute(intent_type, params, bypass_guard=True, bypass_function_approval=True)
```

### 7.5 令牌安全特性

| 特性 | 实现 |
|------|------|
| **一次性使用** | `gate.resolve()` 中 `del self._pending[token]` |
| **TTL 过期** | `PendingApproval.is_expired` 检查 `time.time() > expires_at` |
| **Scope 绑定** | `generate_token(intent_type, target, session_id)` — 令牌与具体操作绑定 |
| **最大并发** | `max_pending` 限制，超出自动拒绝 |

---

## 8. 审批前端操作

### 8.1 ApprovalCard.vue 组件

**路径**：`frontend/src/components/ApprovalCard.vue`

组件接收 `ApprovalData` 接口：

```typescript
interface ApprovalData {
  approval_id: string          // 审批令牌
  level: string                // "action" | "function"
  checks?: Array<{             // 约束检查结果
    guard: string              // Guard 类名
    level: string              // "block" | "warn" | "allow"
    reason: string             // 检查原因
  }>
  policies?: Array<{           // 策略评估结果
    policy: string             // 策略名
    level: string              // "pending" | "approved" | "denied"
    reason: string             // 策略原因
  }>
  summary?: string             // 审批摘要
}
```

#### 组件交互区域

| 区域 | 内容 |
|------|------|
| **头部** | 🛡️ 图标 + "操作审批" 标题 + 级别标签（操作级别/函数级别） |
| **约束检查表** | 每行显示 `guard` 名、级别徽章（🔴BLOCK/🟡WARN/🟢ALLOW）、原因 |
| **审批策略** | 显示触发的 policy 名和级别 |
| **令牌显示** | 蓝色代码块显示 `approval_id` |
| **操作按钮** | ✅ 批准执行（绿色渐变）/ ❌ 拒绝（红色半透明） |

#### 级别染色

- `block` 行背景：`rgba(239,68,68,0.06)` 浅红色
- `warn` 行背景：`rgba(245,158,11,0.04)` 浅黄色
- 卡片边框：`#f59e0b`（橙色）
- 已处理卡片：灰色边框 + 70% 透明度

### 8.2 Chat Store 自动检测

**路径**：`frontend/src/stores/chat.ts`

`detectApprovalData(result)` 在 `express_intent` 工具返回时自动检测：

```typescript
// 如果 result JSON 包含 status === "approval_required" 且有 approval_id
// 则自动插入 ApprovalCard block 并加入 pendingApprovals
const approvalData = detectApprovalData(event.result)
if (approvalData) {
  lastMsg.blocks!.push({ type: 'approval', approval: approvalData })
  pendingApprovals.value.set(approvalData.approval_id, approvalData)
}
```

### 8.3 批准/拒绝流程

用户点击按钮后：

1. `pendingApprovals.delete(approvalId)` — 移除待审批标记
2. 消息中追加 `✅ 已批准执行` / `❌ 已拒绝` 文本
3. 调用 `POST /api/chat/approval` 发送 `{ approval_id, approved }`
4. 回显后端返回的结果（成功/失败消息）

---

## 9. 审计日志

### 9.1 ApprovalGate.audit_log

`ApprovalGate._audit_log` 记录所有审批相关事件：

```python
{
    "timestamp": 1719547200.123,        # Unix 时间戳
    "action": "pending",                 # denied | pending | approved | rejected | resolved
    "intent_type": "refactor",           # 操作类型
    "target": "validate_credit_card",    # 目标实体
    "token": "a1b2c3d4e5f6",            # 审批令牌（pending/rejected/resolved 时有值）
    "approved": False,                   # 是否批准
    "results": [                         # 策略链评估结果
        {"policy": "GuardResultPolicy", "level": "pending", "reason": "restricted 数据"},
        {"policy": "ActionApprovalPolicy", "level": "approved", "reason": "action does not require approval"}
    ]
}
```

### 9.2 Trace 中的审批步骤

Trace 数据模型（`agent/trace.py`）新增两种 step 类型：

```python
@dataclass
class TraceStep:
    type: str  # "approval_required" | "approval_resolved" | ...

@dataclass
class TraceLog:
    approval_token: str = ""         # 当前待审批令牌
    approval_status: str = ""        # "pending" | "approved" | "rejected" | ""
    parent_trace_thread_id: str = "" # 审批回执关联父 trace
```

**Trace 时间线示例**：

```
🧠 thinking: "我需要通过 express_intent 执行 refactor"
🔧 tool_call: express_intent(refactor, validate_credit_card)
🛡️ approval_required: "需要审批才能继续执行"   ← 橙色边框
   > 约束检查: 🔴 WhitelistGuard: 不在白名单中
   >           🔴 OntologyTraversalGuard: restricted 数据
   > 令牌: a1b2c3d4e5f6
✅ approval_resolved: "审批通过: refactor → validate_credit_card"  ← 绿色边框
📋 tool_result: "执行结果..."
```

### 9.3 TraceDetailView.vue 渲染

**路径**：`frontend/src/views/TraceDetailView.vue`

| 步骤类型 | 左边框颜色 | 图标 | 背景色 |
|---------|-----------|------|--------|
| `approval_required` | 🟠 橙色 `#f59e0b` | 🛡️ | `rgba(245,158,11,0.04)` |
| `approval_resolved` | 🟢 绿色 `#10b981` | ✅ | `rgba(16,185,129,0.04)` |

---

## 10. 常见配置场景

### 10.1 降级 BLOCK → WARN

**场景**：某个实体的 `restricted` 数据敏感性太严格，希望降为 WARN 而非直接 BLOCK。

**修改** `constraint_overrides.yaml`：

```yaml
overrides:
  - type: patch
    target: data_sensitivity
    modify:
      restricted: "warn"
```

### 10.2 添加 allow_all 白名单

**场景**：`validate_credit_card` 函数使用脱敏测试数据，无需审批。

```yaml
overrides:
  - type: allow_all
    target_entity: "CodeEntity:validate_credit_card"
    reason: "测试环境使用脱敏数据，安全评审已通过"
    expires: "2026-12-31"
```

效果：`WhitelistGuard` 在 Pipeline 第一步就跳过该实体的所有后续 guard 检查。

### 10.3 新增 Function 约束

**场景**：对新增的 function（如 `migrate_schema`）单独指定危险级别。

**修改** `function_danger_levels.yaml`：

```yaml
functions:
  # ... 已有配置 ...
  migrate_schema: admin   # 新增：数据库迁移 → admin 级别
```

### 10.4 新增实体约束（add_constraint）

**场景**：需要为特定实体关系链追加一个遍历约束。

```yaml
overrides:
  - type: add_constraint
    constraint:
      name: license_check
      source_label: "CodeEntity"
      relation_chain: ["LICENSED_UNDER"]
      target_label: "LicenseEntity"
      collect_property: "license_type"
      aggregation: "max"
```

新约束自动加入 `TraversalConstraint` 列表，由 `OntologyTraversalGuard` 评估。

### 10.5 配置 on_warn 为 auto_allow

**场景**：降低审批敏感度，WARN 级别的约束不再触发审批。

**修改** `approval_policy.yaml`：

```yaml
guard_result:
  on_block: require_approval
  on_warn: auto_allow    # WARN 直接放行，不暂停
```

### 10.6 禁用工具网关

**场景**：开发调试阶段，允许 Agent 自由执行 Cypher。

**修改** `tool_gateway.yaml`：

```yaml
enabled: false
```

---

## 附录 A：配置文件索引

| 文件 | 路径 | 用途 |
|------|------|------|
| `approval_policy.yaml` | `src/ontoagent/config/` | 审批策略、令牌配置 |
| `function_danger_levels.yaml` | `src/ontoagent/config/` | Function 危险级别定义 |
| `constraint_overrides.yaml` | `src/ontoagent/config/` | 约束覆盖（patch/allow_all/add_constraint） |
| `tool_gateway.yaml` | `src/ontoagent/config/` | Cypher 写操作拦截 |
| `constraints.yaml` | `src/ontoagent/pipeline/` | 遍历约束和传播规则路径 |
| `ontology_actions.yaml` | `src/ontoagent/pipeline/` | Action 定义（intent → function 映射） |

## 附录 B：关键 Python 类速查

| 类 | 路径 | 职责 |
|----|------|------|
| `ApprovalGate` | `execution/constraints/approval_gate.py` | 审批门：策略链、令牌管理、审计 |
| `GuardResultPolicy` | `execution/constraints/policies.py` | Guard 结果 → 审批决策 |
| `ActionApprovalPolicy` | `execution/constraints/policies.py` | Action 配置审批 |
| `FunctionDangerPolicy` | `execution/constraints/policies.py` | Function 危险级别审批 |
| `ActionGuardPipeline` | `execution/constraints/guard_pipeline.py` | Guard 链（5 个 guard 顺序执行） |
| `WhitelistGuard` | `execution/constraints/guards.py` | 白名单跳过检查 |
| `EntityExistsGuard` | `execution/constraints/guards.py` | 实体存在性检查 |
| `EntityPropertyGuard` | `execution/constraints/guards.py` | 属性条件检查 |
| `OntologyTraversalGuard` | `execution/constraints/guards.py` | 本体遍历约束检查 |
| `OntologyPropagationGuard` | `execution/constraints/guards.py` | 本体传播约束检查 |
| `ApprovalContext` | `domain/approval.py` | 审批上下文 dataclass |
| `ApprovalDecision` | `domain/approval.py` | 审批决策 dataclass |
| `PendingApproval` | `domain/approval.py` | 待审批记录 dataclass |
| `TraceStep` / `TraceLog` | `agent/trace.py` | Trace 数据模型（含审批字段） |
| `OntologyConstraintLoader` | `execution/constraints/loader.py` | 三层约束加载器 |

---

> **提示**：修改 YAML 配置后无需重启服务，`express_intent` 每次调用时会通过 `_get_action_executor` 和 `_get_approval_gate` 重新加载配置（lazy init 机制）。但在生产环境中建议重启以确保一致性。
