# LayerKG 代码→业务追溯方案 V2.0

> **版本**：V2.0 | **日期**：2026-06-27 | **状态**：待评审
>
> **V1.0 废弃原因**：V1.0 用抽象业务实体 + 别名匹配，假设错误（一台机器多业务、一代码多业务、别名匹配跑不起来）。V2.0 以"接口=业务能力锚点"为核心，用调用链替代猜测。

---

## 一、业务场景

### 核心命题

**"这段代码变更，影响什么业务？"**——代码世界和业务世界之间没有结构化连接，审计员和开发用不同的语言说话。

### 六个具体场景

| 场景 | 问题 | 提问者 | 出发点 |
|------|------|--------|--------|
| 变更影响 | 改了 PhoneValidator，影响什么业务？ | 开发 | 代码（自底向上） |
| 合规审计 | 哪些代码碰了用户手机号？ | 审计员 | 数据（从数据到代码到业务） |
| 漏洞响应 | Log4j 爆了，先修哪些？ | 安全工程师 | 漏洞（从组件到代码到业务） |
| 架构健康 | 哪些技术债最该先还？ | CTO | 架构（复杂度×业务关键度） |
| **业务下线** | **下线"积分商城"，哪些代码能安全删？** | **业务方** | **业务名称（自顶向下）** |
| **能力发现** | **这系统到底有什么业务？** | **新人/产品** | **无（全景图）** |

### 场景详细说明

**场景 1-4：从代码端出发（自底向上）**

开发、审计员、安全工程师、CTO 都从代码侧出发，沿**调用链**追溯，把代码变更翻译成业务影响。这是 LayerKG 的核心能力——把代码世界翻译成业务语言。

**场景 5：业务下线（自顶向下）**

业务方不看代码，说的是业务语言："下线积分商城"。LayerKG 从业务名称反向定位接口入口 → 调用链覆盖的代码 → 区分"可安全删除"和"共享不可删"。

**场景 6：业务能力发现（全景图，无需输入）**

新来的业务方/产品经理打开系统第一个问题："这系统到底有什么？" LayerKG 按业务流程分组展示所有接口入口，标注 P0/P1/P2、生命周期状态、团队归属。**"未分类"本身就是治理信号**——23 个接口没人打过标签 = 23 个业务孤儿。

### 六个场景的统一解法

**双向追溯**：前四个场景从代码→业务（自底向上），后两个场景从业务→代码（自顶向下）。接口入口是双向追溯的交汇点。

---

## 二、核心洞察：接口是代码与业务的唯一自然交汇点

### 为什么接口是正确的锚点

```
HTTP 接口：POST /api/payment/charge
RPC 接口：PaymentService.charge()
定时任务：@Scheduled daily-reconciliation
MQ 消费者：@KafkaListener("payment-events")

→ 这些都是代码里客观存在的入口
→ 调用链从入口出发，覆盖的所有代码天然属于该业务
→ 不需要别名匹配、不需要 LLM 猜、不需要人工填 YAML
```

### 调用链 = 业务边界

```
POST /api/payment/charge（接口入口 = 业务能力锚点）
  ├→ ChargeService.process()
  │    ├→ RiskEngine.check()
  │    ├→ Database.query()
  │    └→ PhoneValidator.validate()（共享代码，同时属于认证业务）
  ├→ OrderService.create()
  └→ NotificationService.send()

→ 这个接口的所有可达代码 = "支付扣款"业务能力的实现范围
→ PhoneValidator 被两个接口都调用 → 自然属于两个业务
```

---

## 三、业务实体设计（极简）

### V1.0 → V2.0 实体对比

| V1.0 方案 | V2.0 方案 | 变化原因 |
|-----------|-----------|---------|
| BusinessCapability（业务能力） | **删除** | 接口入口本身就是锚点，沿调用链自动得到范围 |
| BusinessService（业务服务） | **删除** | ServiceEntity 已有，加字段即可 |
| DataAsset（数据资产） | **保留** | "手机号""交易金额"代码里没有，需人工定义 |
| ComplianceItem（合规要求） | **保留** | "GDPR-17""PCI-DSS"是法律层面，代码里不存在 |

### V2.0 只新增 2 个业务实体

#### DataAsset（数据资产）

```python
@dataclass
class DataAsset:
    """数据资产 — 企业处理什么数据。"""
    name: str                    # "用户手机号"
    description: str
    sensitivity: str             # public / internal / confidential / restricted
    data_type: str               # pii / financial / operational / credentials
    aliases: list[str]           # ["phone", "mobile", "tel", "电话"]
```

**作用**：审计员问"哪些代码碰了手机号"时，沿 processes_data 关系追溯。

**映射范围缩小**：V1.0 要匹配所有代码，V2.0 只匹配接口调用链上的代码（数量少 10 倍，精度高 10 倍）。

#### ComplianceItem（合规要求）

```python
@dataclass
class ComplianceItem:
    """合规要求 — 企业必须遵守的规则。"""
    name: str                    # "GDPR-17"
    description: str
    regulation: str              # "GDPR" / "PCI-DSS" / "数据安全法"
    severity: str                # critical / high / medium / low
    requirement: str             # "必须在30天内删除所有关联数据"
```

**作用**：CodeEntity 通过 subject_to 关系关联合规要求。

### 业务能力不定义实体，用接口标签替代

```
POST /api/payment/charge（接口入口，代码里客观存在）
  ├─ entry_category: http_api
  ├─ entry_metadata: {"route": "/api/payment/charge", "method": "POST"}
  ├─ business_process: 支付扣款    ← 人工标注（业务流程分组，核心导航字段）
  ├─ business_priority: P0        ← 人工标注（优先级）
  ├─ business_owner: 支付团队      ← 人工标注（团队归属）
  └─ business_lifecycle: active    ← 人工标注（active / deprecated / sunset）
```

**人工标注量极小**：只标接口入口的业务标签（一个项目可能几十到几百个接口），不标每个函数。

**`business_process` 是核心导航字段**——新人/产品经理的第一站就是靠它找路。它把散落的接口按业务流程分组，一张图看清系统全貌。**"未分类"本身就是治理信号**——没人打过标签的接口 = 业务孤儿。

---

## 四、Schema 改动

### 4.1 CodeEntity 加字段（不新增 entity_type）

```python
# schema.py CodeEntity 新增
entry_category: str | None = None     # http_api / rpc_service / scheduled / mq_consumer / event_handler
entry_metadata: str | None = None     # JSON: {"route": "/api/payment/charge", "method": "POST", "cron": "0 0 * * *"}
business_process: str | None = None   # 业务流程分组（"支付扣款"/"用户认证"），核心导航字段
business_priority: str | None = None  # P0 / P1 / P2
business_lifecycle: str | None = None # active / deprecated / sunset
business_owner: str | None = None     # 团队归属
```

**为什么不用新 entity_type**：入口点本质是函数，改 entity_type 会破坏 calls/contains 的 domain/range 约束。

### 4.2 新增关系（仅 3 条）

```
桥接关系（代码→业务）：
  processes_data:  CodeEntity → DataAsset       # 代码处理了某数据资产
  subject_to:      CodeEntity → ComplianceItem   # 代码受某合规约束

业务层内部关系：
  governed_by:     DataAsset → ComplianceItem    # 数据资产受合规约束
```

### 4.3 ServiceEntity 加字段（不新增 BusinessService）

```python
# 现有 ServiceEntity 新增
capability_label: str | None = None   # "支付处理" — 业务标签
team: str | None = None               # "支付团队"
```

### 4.4 跨服务 / 异步消息关系（Phase 2）

```
跨服务调用链：
  calls_service:        CodeEntity → ServiceEntity     # 代码调用了某外部服务
  （已有）service_depends_on: ServiceEntity → ServiceEntity

异步消息桥接（用 ConceptEntity 做 topic 中转，不新增实体）：
  publishes_to:  CodeEntity → ConceptEntity(type=message_topic)
  consumed_by:   ConceptEntity(type=message_topic) → CodeEntity
```

### 4.5 改动量

| 文件 | 改动 | 行数 |
|------|------|------|
| schema.py | CodeEntity +2 字段，ServiceEntity +2 字段，DataAsset/ComplianceItem 实体，3 条关系 | ~80 |
| builder_utils.py | entity_to_dict 序列化新字段 | ~6 |
| python_parser.py | 装饰器提取 + 分类规则 | ~75 |
| java_parser.py | 注解提取 + 分类规则 | ~80 |
| 测试 | 5 入口类型 × 2 语言 + 实体/关系 | ~200 |

---

## 五、业务入口点识别

### 5.1 入口类型

| 类型 | Python 模式 | Java 模式 | entry_category |
|------|------------|-----------|----------------|
| HTTP 接口 | `@app.post("/x")`, `@router.get()` | `@PostMapping`, `@GetMapping`, `@RequestMapping`, `@Path` | http_api |
| RPC 接口 | gRPC service 定义 | Dubbo `@Service`, `@RpcService` | rpc_service |
| 定时任务 | `@scheduled_task`, `@celery.task` | `@Scheduled(cron=...)` | scheduled |
| MQ 消费者 | `@kafka_handler`, `@consumer` | `@KafkaListener`, `@RabbitListener`, `@JmsListener` | mq_consumer |
| 事件处理 | `@event_handler` | `@EventListener`, `@TransactionalEventListener` | event_handler |

### 5.2 提取逻辑

**Python**：tree-sitter 的 `decorated_definition` 节点包含 `decorator` 子节点。当前 `_walk` 的递归兜底能触达函数但丢失装饰器上下文。改造：在 `_walk` 分发前捕获装饰器，传给 `_extract_function`。

**Java**：tree-sitter 的 `modifiers` 节点包含 `marker_annotation`（无参）和 `annotation`（带参）。`@Scheduled(cron = "0 0 * * *")` 能直接解析出 `{cron: "0 0 * * *"}`。

### 5.3 分类规则

集中维护在 `src/layerkg/parsing/extractor/entry_point_rules.py`：

```python
# Python 装饰器名 → entry_category
PY_HTTP_PATTERNS = {("app", "get"), ("app", "post"), ("router", "get"), ...}
PY_SCHEDULE_NAMES = {"scheduled_task", "celery.task", "scheduled_job"}

# Java 注解名 → entry_category
JAVA_HTTP_ANNOTATIONS = {"GetMapping", "PostMapping", "PutMapping", ...}
JAVA_SCHEDULE_ANNOTATIONS = {"Scheduled"}
JAVA_MQ_ANNOTATIONS = {"KafkaListener", "RabbitListener", "JmsListener"}
```

---

## 六、三个边界问题的解法

### 6.1 跨服务调用链断裂（ServiceEntity 桥接）

**问题**：服务A通过HTTP/RPC调用服务B，CALLS 关系断裂。

**解法**：利用现有 ServiceEntity + 新增 `calls_service` 关系：

```
服务A:
  PaymentController.charge()
    --calls_service--> ServiceEntity("payment-service")
                          --service_depends_on--> ServiceEntity("order-service")

服务B:
  OrderController.create()  ← 属于 order-service
```

parser 扫描外部调用（`restTemplate.postForObject()` / `httpx.get()`），提取服务名建立关系。

### 6.2 异步消息链路断裂（Topic 中转）

**问题**：代码A发消息到Kafka topic，代码B消费，调用链断裂。

**解法**：用 ConceptEntity（已有）充当消息总线：

```
producer_fn --publishes_to--> ConceptEntity(type=message_topic, name="payment-events")
                                  --consumed_by--> consumer_fn
```

提取方式：
- 发送端：扫描 `kafkaTemplate.send("topic")` → 提取 topic 参数
- 消费端：`@KafkaListener(topics = "payment-events")` → 注解提取

### 6.3 定时任务 / 事件处理器

这两个最简单——就是注解识别，Phase 1 已覆盖：
- `@Scheduled(cron = "0 0 * * *")` → entry_category=scheduled
- `@EventListener` → entry_category=event_handler

---

## 七、完整追溯链路示例

### 场景：变更影响分析

```
输入："我要重构 PhoneValidator.validate()"

Step 1: 反向调用链（自动，已有 BFS）
  → ChargeService.process() 调用了
  → AuthService.register() 也调了
  → SmsService.send_code() 也调了

Step 2: 沿调用链追溯到接口入口（自动）
  → ChargeService.process() ← POST /api/payment/charge（http_api, P0）
  → AuthService.register() ← POST /api/user/register（http_api, P0）
  → SmsService.send_code() ← @Scheduled daily-sms（scheduled, P2）

Step 3: 关联数据资产（如果有）
  → PhoneValidator 处理了 DataAsset("手机号") → sensitivity: confidential

Step 4: 报告
  "影响 3 个业务入口：
   - POST /api/payment/charge（支付扣款, P0）
   - POST /api/user/register（用户认证, P0）
   - @Scheduled daily-sms（通知, P2）
   涉及数据资产：用户手机号（confidential）
   建议：低峰期变更，通知支付团队和认证团队"
```

### 场景：合规审计

```
输入："哪些代码处理了用户手机号？"

Step 1: 查 processes_data 关系
  → DataAsset("手机号").aliases = [phone, mobile, tel]
  → 在接口调用链上的代码中匹配

Step 2: 返回 + 关联业务入口
  → UserService.validate_phone() ← POST /api/user/register（认证, P0）
  → SmsService.send_code() ← @Scheduled daily-sms（通知, P2）
  → Analytics.report_user() ← POST /api/analytics/report（分析, P1）

Step 3: 合规检查
  → DataAsset("手机号") governed_by ComplianceItem("GDPR-17")
  → "Analytics.report_user 缺少脱敏处理，违反 GDPR-17"
```

### 场景：业务下线（自顶向下）

```
输入："我要下线积分商城"

Step 1: 按业务流程名称检索接口入口
  → business_process = "积分商城" 的所有入口
  → GET /api/points/redeem（积分兑换, P2, deprecated）
  → GET /api/points/balance（积分余额查询, P2, deprecated）
  → POST /api/points/adjust（积分调整, P1, deprecated）
  → @Scheduled monthly-points-reset（月度积分重置）

Step 2: 沿正向调用链展开（这些入口调了哪些代码）
  → PointsService.redeem() → PointsCalculator.calculate()
  → NotificationService.send()  ← 共享代码！被支付也调用了

Step 3: 哪些代码可以安全删除？
  → 纯积分商城代码（35 个）：PointsService、PointsCalculator → ✅ 可删
  → 共享代码（12 个）：NotificationService → ⚠️ 不可删，支付还在用

Step 4: 报告
  "积分商城涉及 4 个接口入口、47 个代码实体
   其中 35 个可安全删除，12 个为共享代码（通知/用户服务）
   建议保留 NotificationService 和 UserService 的调用"
```

### 场景：业务能力发现（全景图，无需输入）

```
输入：（不需要输入，直接问"这个系统有什么能力"）

Step 1: 聚合所有接口入口，按 business_process 分组
  → 支付扣款（3 个入口，P0）
     ├─ POST /api/payment/charge
     ├─ POST /api/payment/refund
     └─ PaymentRpcService.refund()
  → 用户认证（5 个入口，P0）
     ├─ POST /api/user/login
     ├─ POST /api/user/register
     └─ ...
  → 积分商城（4 个入口，P2，lifecycle: deprecated）
     ├─ GET /api/points/redeem
     └─ ...
  → 未分类（23 个入口）  ← 没人打过标签的业务孤儿

Step 2: 每个业务流程展示团队归属、代码规模、调用关系
Step 3: 报告
  "本系统共 78 个业务入口，聚合为 6 个业务流程：
   - 2 个 P0（支付、认证）— 核心业务
   - 2 个 P1（订单、通知）— 重要业务
   - 1 个 P2（积分）— 已标记 deprecated
   - 23 个入口未分类 ← 建议补充标签"
```

---

## 八、人工 vs 机器分工

| 环节 | 方式 | 理由 |
|------|------|------|
| 接口入口识别 | **机器**（tree-sitter 装饰器/注解） | 客观事实，代码里写好了 |
| 调用链追溯 | **机器**（已有 BFS） | CALLS 关系已有 |
| 接口→业务标签 | **人工**（标注 business_process / priority / lifecycle / owner） | 业务优先级和流程分组是人的判断 |
| 业务能力发现 | **全自动**（聚合接口入口按 process 分组） | 建好后零输入查询 |
| 数据资产定义 | **人工**（YAML） | 领域知识 |
| 数据资产→代码映射 | **半自动**（缩小到调用链范围） | 范围小了，精度高了 |
| 合规要求定义 | **人工**（YAML） | 法律判断 |
| 合规→数据关联 | **人工** | 法律判断 |
| 查询 | **全自动** | 关系建好后，Agent 沿图追溯 |

---

## 九、实施计划

### Phase 1：接口入口识别 + MVP Demo（1-2 天，~370 行）

| 任务 | 文件 | 行数 |
|------|------|------|
| CodeEntity 加 6 个业务字段 | schema.py | ~12 |
| entity_to_dict 序列化 | builder_utils.py | ~10 |
| Python 装饰器提取 + 分类规则 | python_parser.py | ~75 |
| Java 注解提取 + 分类规则 | java_parser.py | ~80 |
| entry_point_rules.py 规则表 | 新文件 | ~30 |
| 单元测试 | tests/ | ~200 |

**Phase 1 做完就能演示"调用链追溯业务影响"。**

### Phase 2：业务实体 + 桥接关系（2-3 天）

| 任务 | 行数 |
|------|------|
| DataAsset + ComplianceItem 实体 | ~60 |
| processes_data / subject_to / governed_by 关系 | ~30 |
| business_ontology.yaml 配置 + 加载器 | ~100 |
| 数据资产映射（缩小到调用链范围） | ~80 |
| 2 个新 Action（compliance_check / business_impact_analysis） | ~100 |
| 测试 | ~200 |

### Phase 3：跨服务 + 异步消息桥接（3-4 天）

| 任务 | 行数 |
|------|------|
| calls_service 关系 + 外部调用提取 | ~280 |
| publishes_to / consumed_by + topic_linker | ~260 |
| ServiceEntity 加 capability_label + team | ~10 |
| 测试 | ~200 |

---

## 十、比赛叙事（30 秒电梯演讲）

> "企业里代码和业务用两套语言说话——开发说 PaymentService.calculate()，业务方说'积分商城还在跑吗？'。LayerKG 以接口为锚点，支持双向追溯：自底向上，改一个函数立刻知道影响了哪些 P0 业务；自顶向下，新来的产品经理一张图看清系统有什么能力，要下线一个业务立刻知道哪些代码能安全删除。不是搜索工具，是代码与业务之间的结构化翻译层。"

### 差异化壁垒

| 维度 | Sourcegraph | Datadog | GraphRAG | **LayerKG** |
|------|-----------|---------|---------|------------|
| 代码图谱 | ✅ | ❌ | ❌ | ✅ |
| 接口入口识别 | ❌ | ❌ | ❌ | **✅** |
| 调用链追溯 | ⚠️ 基础 | ❌ | ❌ | ✅ |
| 业务影响翻译 | ❌ | ❌ | ❌ | **✅** |
| 合规追溯 | ❌ | ❌ | ❌ | **✅** |
| 本体约束 | ❌ | ❌ | ❌ | **✅ Palantir 式** |
| Agent 交互 | ⚠️ | ❌ | ❌ | ✅ |

**LayerKG 站在空白市场：没有竞品同时做接口识别 + 调用链追溯 + 业务影响翻译 + 合规追溯。**

---

## 十一、与 V1.0 方案的关键差异

| 维度 | V1.0（废弃） | V2.0（当前） |
|------|-------------|-------------|
| 业务能力来源 | 人工 YAML 定义 BusinessCapability | **接口入口（代码客观存在）** |
| 代码→业务映射 | 别名匹配 + LLM 猜测 | **调用链遍历（零猜测）** |
| 多业务共存 | 需要 BusinessService 绑 capability_id | **接口天然区分，自动追溯** |
| 新增实体 | 4 个 + 7 条关系 | **2 个 + 3 条关系** |
| 一台机器多业务 | 无法处理 | **接口天然区分多个业务** |
| 一段代码多业务 | 无法处理 | **调用链交叉天然覆盖** |
| schema.py 压力 | +250 行，踩红线 | **+80 行** |
| 人工标注量 | 每个函数标业务 | **只标接口入口** |
| 技术可行性 | parser 不抽字段，跑不起来 | **CALLS 关系已有，加装饰器解析** |
