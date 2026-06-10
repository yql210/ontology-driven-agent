# LayerKG 四层架构审核请求

## 架构设计：四层分离

### 第 1 层：意图层（Agent + 路由）
Agent 不硬编码 Action 列表，从 ontology_actions.yaml 自动生成 prompt。
每个 Action 的 trigger_hint 就是路由规则。
扩展：加 Action 时改 YAML，prompt 自动更新。

### 第 2 层：控制层（Action）
Action = Submission Criteria + Function 引用 + 审批配置 + 超时/重试
Action 是 Function 的壳：参数校验、Submission Criteria 验证、审批机制、调用 Function、审计日志。
扩展：YAML 声明，不写编排代码。

### 第 3 层：能力层（Function）
通用 Function（有限稳定）：query/create/update/delete/link/unlink/check/notify
领域 Function（按需扩展）：每个对接一个外部系统或一种分析能力，装饰器注册热加载。
Function 可以读语义层、写语义层、调外部API、触发副作用。不分推理和执行。
扩展：写一个 Python 函数加装饰器注册。

### 第 4 层：数据层（Connector）
Connector 接口：fetch(params) -> list[dict], sync(graph_store) -> None
不同外部系统实现不同 Connector。
扩展：实现 Connector 接口。

### 语义层（Schema + 图谱）= 不改的基础

### 关键约束
1. 每层只依赖下一层，不跨层依赖
2. Action 只引用 Function 名，不引用 Function 实现
3. Function 只通过 graph_store 操作数据，不直接调 Connector
4. Connector 只负责数据搬运，不触发 Action
5. 触发规则和提交规则（Submission Criteria）是两个独立机制
6. 事件总线驱动闭环

### Agent 触发链路
用户提问 -> Agent 识别意图 -> express_intent 写临时层 -> 事件总线 -> 规则引擎 -> Action 检查 Submission Criteria -> Function 执行 -> 数据变化 -> 新事件 -> 闭环

## 请审核
1. 四层分离是否合理？有没有层间职责混淆？
2. Action 作为 YAML 声明能否覆盖复杂场景（多 Function 并行+条件分支）？
3. Function 不分推理/执行是否正确？安全审计怎么保证？
4. Connector 接口设计是否足够？Connector 和领域 Function 的边界？
5. 闭环事件驱动有没有遗漏？死循环防护够不够？
6. Agent 意图识别靠 trigger_hint 是否可靠？
7. 从当前代码迁移的风险最大的点？
8. 总体评分（0-100）和最关键的 3 个改进建议。
