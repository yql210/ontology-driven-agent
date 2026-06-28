# OntoAgent 操作手册编写计划

## 现状诊断

当前 `docs/review/` 下有 4 本手册，全部基于旧版 LayerKG 编写，严重过时：

| 手册 | 行数 | 过时程度 |
|------|:---:|------|
| `manual1_quickstart.md` | 312 | 项目名/CLI/环境变量全部错误，缺少约束/审批系统 |
| `manual2_troubleshooting.md` | 424 | 引用旧路径/旧导入，无约束/审批故障排查 |
| `manual3_source.md` | 1038 | 目录结构基于 `src/layerkg/`，无 execution/constraints/，无审批引擎 |
| `manual4_api.md` | 559 | 无 POST /api/chat/approval，无新 trace 类型，无 check_operation |

**核心问题**：项目从 LayerKG 更名为 OntoAgent，CLI 从 `layerkg` 改为 `ontoagent`，环境变量从 `LAYERKG_*` 改为 `ONTOAGENT_*`，目录从 `src/layerkg/` 改为 `src/ontoagent/`，新增了约束框架 + 审批系统 + 配置化等大量功能。4 本手册全部需要重写。

---

## 重写方案：5 本新手册

### 手册 1：快速上手指南（替代 manual1）

| 章节 | 内容 | 关键点 |
|------|------|--------|
| 系统要求 | Python 3.13+, uv, Neo4j 5.x, Ollama（可选） | 从旧版迁移 |
| 安装 | `git clone`, `uv sync` | 更新仓库地址和命令 |
| 配置 | `.env` 文件，`ONTOAGENT_*` 环境变量 | 全部更新为 ONTOAGENT 前缀 |
| 5分钟构建 | `ontoagent build`, `--skip-semantic`, `--clear` | 更新 CLI 命令 |
| 知识图谱查询 | `ontoagent query`, `ontoagent ask` | 更新 |
| **约束配置** | `constraints.yaml`, `constraint_overrides.yaml` | 🆕 |
| **审批配置** | `approval_policy.yaml`, `function_danger_levels.yaml`, `tool_gateway.yaml` | 🆕 |
| Web 界面 | `ontoagent web`, `cd frontend && npm run dev` | 更新 |
| Schema 速查 | 10 实体 + 21 关系 | 从 6+11 更新到当前版本 |

### 手册 2：运维排查手册（替代 manual2）

| 章节 | 内容 | 关键点 |
|------|------|--------|
| 诊断命令 | `ontoagent info`, `pytest`, `ruff check` | 更新 |
| 外部服务检查 | Neo4j, Ollama | 保持 |
| **约束系统故障** | 约束不生效？BLOCK/WARN 行为异常？ | 🆕 |
| **审批系统故障** | 审批单不出现？令牌过期？ | 🆕 |
| **Trace 故障** | 审批步骤不显示？数据库损坏？ | 🆕 |
| 构建流水线故障 | 实体数为0、卡住、Ollama超时 | 保持 |
| Agent 对话故障 | 401/400错误、DeepSeek reasoning_content | 保持 |
| 数据维护 | 重置、备份、一致性检查 | 更新 |

### 手册 3：源码解读手册（替代 manual3）

| 章节 | 内容 | 关键点 |
|------|------|--------|
| 项目结构 | `src/ontoagent/` 目录树 | 🆕 全部重写 |
| 架构分层 | Intent → Control → Capability → Semantic | 对齐 DESIGN_V34 |
| 领域模型 | `schema.py`（10 实体），`constraints.py`，`ontology_constraints.py`，`approval.py` | 🆕 |
| 约束框架 | `execution/constraints/` 子包（Loader, Engine, Propagator, Guards, Pipeline, ApprovalGate, Policies） | 🆕 |
| 审批引擎 | `approval_gate.py`（策略链、令牌管理、审计），`policies.py`（3种策略） | 🆕 |
| 构建流水线 | `pipeline/` | 保持，更新 |
| 存储层 | `store/` | 保持 |
| Agent 层 | `agent/`（graph, tools, prompt, trace, tool_gateway） | 🆕 |
| Web API | `api/web/`（chat/approval端点、trace） | 🆕 |
| 配置系统 | 7 个 YAML 文件的关系 | 🆕 |

### 手册 4：API 与集成参考手册（替代 manual4）

| 章节 | 内容 | 关键点 |
|------|------|--------|
| CLI 命令 | `ontoagent build/query/update/ask/serve/web/info` | 全部更新 |
| **审批配置 CLI** | 7 个 YAML 文件的配置说明 | 🆕 |
| Agent 工具 | 10 个 LangChain Tool | 新增 check_operation |
| REST API | Chat、Graph、Trace 端点 | 保持 |
| **POST /api/chat/approval** | 审批决策接口 | 🆕 |
| SSE 事件 | token, tool_start, tool_end, error, done | 保持 |
| Trace API | 新增 approval_status 字段、approval_required/resolved 步骤 | 🆕 |
| MCP 集成 | `ontoagent serve` | 更新 |

### 手册 5：审批系统操作指南（全新）

| 章节 | 内容 |
|------|------|
| 审批概览 | 三层审批架构：action级 + function级 |
| 配置审批策略 | `approval_policy.yaml` 详解（policies, on_block, on_warn, token） |
| 配置 function 危险级别 | `function_danger_levels.yaml` 详解（read/write/read_sensitive/admin） |
| 配置约束覆盖 | `constraint_overrides.yaml` 详解（patch/allow_all/add_constraint） |
| 配置工具网关 | `tool_gateway.yaml` 详解（enabled, blocked_keywords） |
| 配置约束遍历路径 | `constraints.yaml` 详解（traversal_constraints, propagation_rules） |
| 审批流程详解 | 完整流转：Agent→approval→用户→执行 |
| 审批前端操作 | 批准卡片、拒绝、Token管理 |
| 审计日志 | ApprovalGate.audit_log 格式、Trace 中的审批步骤 |
| 常见配置场景 | 降级 BLOCK→WARN、添加白名单、新增 function 约束、新增实体约束 |

---

## 实施计划

| 序号 | 手册 | 原始参考 | 字数估计 | 依赖 |
|:---:|------|---------|:---:|------|
| 1 | 快速上手指南 | manual1（重写） | ~2500 | 无 |
| 2 | 运维排查手册 | manual2（重写） | ~2000 | 无 |
| 3 | 源码解读手册 | manual3（重写） | ~3500 | 无 |
| 4 | API 参考手册 | manual4（重写） | ~2500 | 无 |
| 5 | 审批系统指南 | 🆕 全新 | ~3000 | 无（但建议最后写） |

**5 本可并行编写**，预估总字数 ~13,500。

---

## 对比：旧 vs 新

| 项目 | 旧手册 | 新手册 |
|------|--------|--------|
| 项目名 | LayerKG | OntoAgent |
| CLI | `layerkg` | `ontoagent` |
| 环境变量 | `LAYERKG_*` | `ONTOAGENT_*` |
| 实体数 | 6 | 10 |
| 关系数 | 11 | 21 |
| 约束系统 | 无 | 三层架构 |
| 审批系统 | 无 | action + function 双级审批 |
| YAML 配置 | 1 个 | 7 个 |
| API 端点 | 3 组 | 4 组（+approval） |
| Tool 数量 | 8 | 10 |
| 手册数量 | 4 | 5 |
