# OntoAgent 架构重对齐方案：从「代码知识图谱」到「本体驱动的业务能力编排引擎」

> **状态**: V5.1 — Claude Code 审查修正版
> **日期**: 2026-07-01
> **基线**: 1569 passed, 3 skipped
> **审查**: Claude Code (GLM 5.1)，7 项修正已并入

---

## 一、诊断：名不副实的"本体驱动"

### 1.1 核实现状（实查结论）

| 维度 | 现状 | 问题 |
|------|------|------|
| **实体建模** | 11 实体全为代码/IT/治理工件 | 无业务能力、无流程模板、无业务契约 |
| **动词位置** | intent→action→function 硬编码在 YAML + Python | 本体里没有"能做什么"的一等公民 |
| **意图路由** | `intent_router.py`（45 行）做关键字匹配 | 不是意图理解，不是规划，不是推理 |
| **执行引擎** | `action_executor.py`（212 行）线性 for 循环 | 无 DAG、无并行、无数据流转 |
| **Agent** | `graph.py`（308 行）标准 ReAct 工具调用 | 单节点循环，无 Planner/Orchestrator 分工 |
| **治理投入** | 三代约束（V3 Guard + V4 Shape + ApprovalGate）≈2600 行 | 治理是最近全部演进方向；能力发现/编排为零 |
| **语义提取** | `semantic.py`（519 行）LLM 标注代码实体 | 标注"是什么标签"，不逆向"能做什么" |

### 1.2 核心矛盾

```
本体建模了"错的世界"。
它知道代码里有 Controller/Service/Entity，但不知道系统能"完成订单履约"。
它能判断"重构这个函数安不安全"，但不能回答"要完成 X 业务目标需要哪些能力"。

"本体驱动" slogan 名不副实——本体被当存储 schema 用，没有驱动任何行为推导。
```

---

## 二、目标：本体驱动的业务能力编排引擎

### 2.1 五条硬原则（每个设计决策用它们自检）

| # | 原则 | 含义 | 交付阶段 |
|---|------|------|:--:|
| **P1** | 单一真理源 | 本体(TBox)是唯一模型；存储/API/校验/行为都是它的投影 | Phase 0 |
| **P2** | 类型先于实例 | 先有类型级规则与推理，实例由类型治理 | Phase 0 |
| **P3** | 行为从本体推导 | "能做什么/允许做什么/如何组合"从本体推导，不是硬编码 | Phase 3 |
| **P4** | 领域中心 | 本体建模业务领域（能力/流程/契约），不是代码工件 | Phase 1 |
| **P5** | 可推理 | 传递蕴含(PRODUCES/CONSUMES链)、分类、一致性检查 | Phase 2 最小切片(传递蕴含) |

### 2.2 端到端目标链路

```
用户业务目标(NL)
  → Planner: 意图理解 + 目标分解为子目标
  → Capability Finder: 对每个子目标，从能力本体语义检索+排序候选 Capability
  → Composer: 基于本体组合规则(PRODUCES/CONSUMES/COMPOSES_INTO)与前后置条件，
              求解出满足总目标的 Capability DAG（规划即推理）
  → Orchestrator: 调度 DAG（并行/分支/条件/数据流转），长在 Saga 上
  → Governance: 复用现有 Shape/ApprovalGate，挂在 Capability 上
  → 业务结果 + 编排追溯
```

对比当前链路：

```
用户目标(NL)
  → Agent(ReAct) 从 prompt 关键字匹配 intent_type
  → express_intent("refactor", "Cache")
  → ActionExecutor 线性 for 循环跑 config.functions
  → 文本结果（无编排追溯、无 DAG、无能力发现）
```

**差距本质**：当前是"关键字→硬编码动作"，目标是"目标→能力发现→规划→编排"。

### 2.3 新增一等公民实体（动词入本体）

```
CapabilityEntity:
  name, business_domain, description
  input_contract(schema), output_contract(schema)
  preconditions, postconditions
  effects(对业务状态的影响)
  non_functional{sync/async, idempotent, sla, side_effect}
  keywords(语义检索)
  realized_by → CodeEntity 或外部接口
  version, enabled

ProcessEntity:
  name, 业务流程模板
  steps: DAG(Capability 引用 + 数据流转 + 分支/并行)
  triggers, completion_criteria

新增本体关系:
  PRODUCES      — Capability → 产出的数据类型
  CONSUMES      — Capability → 消费的数据类型
  COMPOSES_INTO — Capability → 更高级 Capability（组合模式）
  REALIZED_BY   — Capability → CodeEntity（逆向自代码）
  PRECEDES      — Capability → Capability（流程顺序）
  EQUIVALENT_TO — Capability ↔ Capability（语义等价）
```

### 2.4 五个新子系统

| 子系统 | 职责 | 输出 |
|--------|------|------|
| **Capability Extractor** | 从代码逆向 CapabilityEntity | API 入口→输入/输出契约→Capability |
| **Capability Finder** | 语义检索匹配子目标 | top-k 相关 Capability |
| **Planner** | 目标→分解→选能力→DAG | 可执行的 Capability DAG |
| **Orchestrator** | DAG 调度执行 | 执行结果 + 追溯 |
| **Reasoner** | 类型级推理（蕴含/分类/一致性） | 推理结论 |

---

## 三、代码处置

### 3.1 处置矩阵

| 处置 | 范围 | 理由 |
|------|------|------|
| 🟢 **复用不动** | store/全部、parsing/parser/、pipeline 构建管线、butler/、api/、domain/schema 主体 | 基础设施稳定，不重造轮子 |
| 🟡 **保留外壳重写内核** | action_executor、intent_router、agent/tools.py、agent/graph.py、parsing/extractor/semantic.py、pipeline/business_loader.py、execution/saga.py | 接口形状对但内部逻辑要变 |
| 🟠 **搁置不删** | 治理面全部（~2600 行 constraints/shapes/approval/functions） | 等 Capability 落地后挂上去，不删不扩 |
| 🔴 **真正删除** | intent_router 关键字逻辑、ontology_actions.yaml 硬编码映射、纯演示 function | <5% 代码量 |

### 3.2 各文件处置明细

```
🟢 不动:
  store/                  — Neo4j/ChromaDB 适配器、迁移
  parsing/parser/         — tree-sitter Python/Java/Doc 解析器
  pipeline/builder.py     — 多阶段构建管线
  pipeline/incremental*   — 增量更新
  pipeline/change_detector.py
  pipeline/impact_propagator.py
  pipeline/aligner.py
  pipeline/module_clustering.py
  butler/                 — 事件驱动引擎
  api/cli.py, mcp_server.py, web/
  domain/schema.py 主体   — 现有实体定义保留，追加新实体
  domain/provenance.py, exceptions.py

🟡 重写内核:
  execution/action_executor.py   — 线性循环 → Capability DAG 编排
  execution/intent_router.py     — 关键字路由 → 被 Planner 取代
  agent/tools.py                 — ~50% 重写(1030行)：新工具集(discover/plan/run)
  agent/graph.py                 — ReAct → Planner/Orchestrator 多节点图
  parsing/extractor/semantic.py  — 浅标注 → 逆向能力契约
  pipeline/business_loader.py    — 移除其职责（真正承载硬编码映射的是 ontology_actions.yaml 和 entry_point_rules.py）

🔴 从零重写（非"升格"，现有代码一行用不上）:
  execution/saga.py              — 当前是顺序for循环，无并行/分支/条件/DAG拓扑。
                                   需新写 DAGOrchestrator，复用 FunctionRunner/TransactionManager。

🟠 搁置:
  execution/constraints/全部      — V3 Guard Pipeline
  execution/shape_*               — V4 Shape 系统（shape_registry/shape_evaluator/
                                    path_compiler/decision_fuser — V4 才落地，紧贴 CodeEntity 维度，
                                   治理迁移到 Capability 是语义重设计，Phase 5a 处理）
  domain/shapes.py, constraints.py, approval.py
  execution/functions/builtin.py, check_compliance, trace_business_impact
  pipeline/entry_point_rules.py   — Phase 1.2 复用其 API 入口识别（读不改），Phase 5 后评估是否重构

🔴 删除:
  移入搁置区的 intent_router 关键字逻辑
  ontology_actions.yaml 的 intent→action 硬编码映射
```

---

## 四、分阶段计划

### Phase 0: 基线与本体扩展

**目标**: 本体能描述业务能力

| 任务 | 内容 |
|------|------|
| **0.1** | 跑通测试基线（1569 passed ✅ 已完成） |
| **0.2** | 新增 `CapabilityEntity` dataclass（domain/schema.py） |
| **0.3** | 新增 `ProcessEntity` dataclass |
| **0.4** | 新增 6 条关系类型（PRODUCES/CONSUMES/COMPOSES_INTO/REALIZED_BY/PRECEDES/EQUIVALENT_TO） |
| **0.5** | Neo4j 迁移脚本（v_next） |
| **0.6** | 更新 VALID_ENTITY_LABELS / RELATION_TYPE_TO_NEO4J |

**DoD**: `uv run python -c "from ontoagent.domain.schema import CapabilityEntity; print(CapabilityEntity(name='订单履约'))"` 能跑通 + 单测覆盖所有新 dataclass 和关系约束。

### Phase 1: 能力逆向

**目标**: 从代码自动产出 CapabilityEntity

| 任务 | 内容 |
|------|------|
| **1.1** | 强化 `parsing/extractor/semantic.py`：从函数签名/docstring/注解逆向输入输出契约 |
| **1.2** | 复用 `entry_point_rules.py` 的 API 入口识别 |
| **1.3** | 在 builder 管线挂载 Phase 1.5: Capability Extraction |
| **1.4** | REALIZED_BY 关系自动建立（Capability → CodeEntity） |

**DoD**: 对一个真实服务（如 demo-service），自动产出 N 个 CapabilityEntity 并写入 Neo4j。
自动化度量（非人工抽检）：
- 字段级 precision ≥ 85%（产出字段中正确比例）
- 字段级 recall ≥ 70%（应有字段中被抽取的比例）
- 关系 REALIZED_BY 正确建立（Capability → CodeEntity）

### Phase 2: 能力发现

**目标**: 给定子目标文本，找回相关 Capability + 类型级推理

| 任务 | 内容 |
|------|------|
| **2.1** | ChromaDB 索引 CapabilityEntity 的 description + keywords |
| **2.2** | 实现 `CapabilityFinder.find(sub_goal, top_k)` |
| **2.3** | **Reasoner 最小切片（P5 交付）**: 传递蕴含 — 若 A PRODUCES X 且 B CONSUMES X，自动推导 A→B 数据流依赖 |
| **2.4** | 评测集：**≥50 个子目标** + 标准答案 |

**DoD**:
- 评测集 top-3 召回率 ≥ 80%
- 传递蕴含：给定 10 个 Capability + PRODUCES/CONSUMES 关系，自动推导出所有数据流依赖链

### Phase 3: 规划器

**目标**: 目标→分解→选能力→DAG

| 任务 | 内容 |
|------|------|
| **3.1** | LLM-assisted Planner：目标→子目标列表 |
| **3.2** | Composer：基于 PRODUCES/CONSUMES/COMPOSES_INTO + 前后置条件求解 DAG |
| **3.3** | **降级路径**: Composer 拒绝 LLM 出的 DAG 时，降级策略为"扩展搜索空间重试→向用户提问澄清→返回部分可达子图"三步，不静默失败 |
| **3.4** | 评测集：≥5 个业务目标 + 标准答案 DAG |

**DoD**: 给定"完成订单履约"，自动产出可执行的 Capability DAG。
Phase 1 契约质量直接决定此阶段可行性——若 Phase 1 字段级 precision < 85%，此阶段暂停并回溯 Phase 1。

### Phase 4: 编排引擎

**目标**: DAG→调度→执行→结果（从零重写，现有 saga.py 顺序 for 循环无法复用）

| 任务 | 内容 |
|------|------|
| **4.1** | DAGOrchestrator：DAG 拓扑排序 + 并行/分支/条件调度 |
| **4.2** | 数据流转：Capability 间按 PRODUCES/CONSUMES 传递 payload |
| **4.3** | 补偿：DAG 节点失败时反向补偿已完成节点（新写，复用 TransactionManager） |
| **4.4** | 端到端集成：Planner→Finder→Composer→DAGOrchestrator |

**DoD**: 端到端跑通"目标→编排→执行→结果"，含一个含并行+补偿的流程。
编排引擎实现为独立模块 `execution/dag_orchestrator.py`，不与现有 action_executor 耦合。

### Phase 5: 治理回归（拆三个子阶段，非"挂上去"而是语义重设计）

**背景**: 三代约束（Guard Pipeline + Shape + ApprovalGate）当前语义假设 `entity + operation` 二元组和线性步骤。Capability DAG 的 operations 是动态推导的，治理切面需要重做。

| 子阶段 | 目标 | DoD |
|--------|------|-----|
| **5a** | Shape 适配 Capability：`ShapeEvaluator` 的 entity 参数从 CodeEntity 切换到 CapabilityEntity；`path_compiler` 语义模型更新 | 一个写类 Capability 操作被 Shape BLOCK 并给出替代建议 |
| **5b** | ApprovalGate 适配 DAG：DAG 并行节点的审批触发点设计；动态 operations 下的多级审批路由 | 含审批节点的 DAG 跑通一次审批流转 |
| **5c** | Guard Pipeline 退役：确认所有 Guard 规则已被 Shape 覆盖后，移除 Guard Pipeline，统一约束入口 | 删除 Guard Pipeline 后全量测试无回归 |

---

## 五、关键设计决策

### 5.1 为什么 CapabilityEntity 不能是 ConceptEntity 的子类型？

ConceptEntity 当前语义是"标签"（business_concept/design_pattern/api_contract/data_model/process），它描述"这是什么种类"。CapabilityEntity 描述"能做什么"——它有 input_contract/output_contract/preconditions/effects，这是完全不同的维度。两者可以关联（Capability REALIZES Concept），但不能合并。

### 5.2 为什么 Planner 先 LLM-assisted 而非纯求解器？

Capability 本体在 Phase 1-2 才建立，初期数据稀疏，纯符号推理（PDDL/SHACL-SPARQL）会因缺少前置条件声明而大量失败。LLM 在数据稀疏期做语义兜底，同时本体积累类型级规则后逐步切换为符号求解器。Planner 接口设计为可替换：`decompose(goal) → list[SubGoal]` + `compose(sub_goals) → DAG`。

### 5.3 为什么不删治理代码——但要承认迁移代价

三代约束（Guard Pipeline + Shape + ApprovalGate）是 2600 行经过验证的生产代码。但它们的语义假设（entity+operation 二元组、线性步骤序号）在 Capability DAG 的"动态 operations + 并行节点"模型下不成立。Phase 5 拆为 5a/5b/5c 三个子阶段，每个独立处理一个治理切面的语义重设计——不是"搬家"，而是重做切面。

### 5.4 为什么构建管线不拆？

builder/incremental/change_detector/impact_propagator/aligner/clustering 这条管线是项目最成熟的资产。新增的 Capability Extraction 作为 Stage 1.5 挂进去（在 Structural Write 之后、Semantic 之前），不改造现有管线逻辑。

---

## 六、风险与对策

| 风险 | 等级 | 对策 |
|------|:--:|------|
| **Phase 1 契约逆向准确率是整个计划的地基** | 🔴 | 字段级 precision/recall 自动化度量。若 precision < 85% 回溯 Phase 1，不进入 Phase 3 |
| **编排引擎从零重写**（saga.py 现有代码无法复用 DAG 拓扑/并行/数据流转） | 🔴 | 新模块 `dag_orchestrator.py`，只复用 TransactionManager。Phase 4 独立验收 |
| **治理迁移是语义重设计**（Shape/ApprovalGate 的 entity+operation 假设在 Capability DAG 下不成立） | 🔴 | Phase 5 拆 5a/5b/5c 三个子阶段，每个独立 DoD |
| Capability 提取准确率低 | 🟠 | Phase 1 只做"API 入口→契约"一类，不做泛化 |
| LLM Planner 幻觉 | 🟠 | DAG 输出强制经 Composer 本体规则校验；拒绝时有三级降级策略 |
| Phase 2 评测集过小导致指标不可信 | 🟠 | ≥50 条样本 |
| 演进期破坏现有功能 | 🟡 | 每 Phase 独立提交、保持 main 可跑、CLI 不变 |
| 范围蔓延 | 🟡 | 五 Phase 严格按 DoD 验收，治理面搁置不碰直到 Phase 5 |

---

## 七、下一步

1. **评审本方案** — 确认方向、实体设计、阶段划分
2. **进入 Phase 0** — TDD: 先写 CapabilityEntity 的失败测试
3. **每 Phase 停下汇报** — DoD 达成 + 测试通过后等确认
