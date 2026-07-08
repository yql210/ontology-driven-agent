# 修复计划：8 项审核问题

> **日期**: 2026-07-08
> **基线**: 1714 unit tests + 32 E2E tests green

---

## P0-1: LICENSE 不一致

**问题**: LICENSE = MPL 2.0，README badge = MIT

**修复**: README badge 改为 MPL 2.0

**改动**:
- `README.md`: badge URL 从 `License-MIT-yellow` → `License-MPL_2.0-brightgreen`
- 不动 LICENSE 文件本身（MPL 2.0 是正确的）

## P0-2: docker-compose 环境变量名不匹配

**问题**: docker-compose 注入 `NEO4J_URI`，config.py 读 `ONTOAGENT_NEO4J_URI`

**修复**: docker-compose.yml 里给 OntoAgent 容器的环境变量加 `ONTOAGENT_` 前缀

**改动**:
- `docker-compose.yml`: `NEO4J_URI` → `ONTOAGENT_NEO4J_URI`（仅 OntoAgent 服务的 environment 段，Neo4j 自身的 `NEO4J_AUTH` 等不动）

## P0-3: README badge 数据过期

**问题**: 硬编码 "1274 passed"、"54 files 12K LOC"

**修复**: 更新为实际数据；tests badge 去掉具体数字改为 "1714+ passed"

**改动**:
- `README.md`: 更新 badge URL 中的数字

## P0-4: 版本号混乱

**问题**: pyproject=0.2.0, CLAUDE.md=V3.4, 旧名 LayerKG 残留

**修复**: 统一——pyproject.toml 版本号是权威源，文档中统一引用

**改动**:
- `README.md`: 标题保持 OntoAgent，不写 V3.4
- `CLAUDE.md`: 标题保持 OntoAgent，不写 V3.4
- `docs/design/DESIGN_V34.md`: 标题 LayerKG → OntoAgent（不动正文，只改标题）
- `docs/manuals/manual1_quickstart.md`: v0.1.0 → v0.2.0（对齐 pyproject.toml）

## P0-5: .ontoagent/*.db 提交到仓库

**问题**: 两个 SQLite 文件入库，.gitignore 未排除

**修复**: 从 git 移除 + 加入 .gitignore

**改动**:
- `git rm --cached .ontoagent/butler_audit.db .ontoagent/butler_skills.db`
- `.gitignore`: 追加 `.ontoagent/`

## P0-6: Cypher 泄露（部分有效）

**问题**: api/web/router/graph.py 和 execution/functions/ 里有直接 Cypher

**修复范围**: 本轮不重构 Cypher 分层（改动量大，需要设计 store 层抽象 API）

**改动**: 无代码改动。在文档中标记为已知技术债。

**理由**: graph_query 工具暴露 Cypher 给 LLM 是设计意图（Text2Graph 模式）；pipeline/ 里的 Cypher 是构建阶段代码。真正的分层违反在 api/web/router/graph.py，但重构它需要设计新的 GraphStore 查询接口，不适合在修复轮做。

## P0-7: 死代码（部分有效）

**问题**: ~1542 行死代码未接入生产路径

**修复范围**: 本轮不删除死代码（它们有测试覆盖，删除需要清理对应测试）

**改动**: 无代码改动。在文档中标记为已知技术债。

**理由**: saga.py/dag_orchestrator.py/planner/reasoner 是 V5 架构演进的预留代码，有对应的单元测试。删除它们需要同时清理测试文件，改动面大。CLAUDE.md 中"写操作走 TransactionManager/SAGA"的描述需要修正为实际情况。

**修正 CLAUDE.md 描述**: 将"写操作走 TransactionManager/SAGA 保证原子性与补偿"改为"写操作线性执行（TransactionManager/SAGA 为预留代码，尚未接入）"

## P0-8: 假写操作（最严重）

**问题**: update_entity/create_entity/create_relation 标记为 write/admin 但不执行写入

**修复**: 两个选择——

**方案 A（推荐）**: 让这些函数真正执行写入
- update_entity: 调用 `ctx.graph_store.merge_node()`
- create_entity: 调用 `ctx.graph_store.merge_node()`
- create_relation: 调用 `ctx.graph_store.merge_relation()`
- 需要 ActionContext 暴露 graph_store（检查是否已有）

**方案 B**: 标记为 stub，返回 `success=False, error="Not implemented"`
- 防止假成功，但不增加功能

**选择方案 B**: 因为方案 A 需要改 ActionContext 的接口和 Function 签名，影响面大。方案 B 是最小改动且消除了欺骗性。

**改动**:
- `execution/functions/general.py`: update_entity/create_entity/create_relation 返回 `FunctionResult(success=False, error="Function not implemented: ...")`

---

## 改动文件清单

| 文件 | 改动类型 | P0 |
|------|---------|-----|
| README.md | badge 修正 + 数据更新 | P0-1, P0-3 |
| docker-compose.yml | 环境变量名修正 | P0-2 |
| CLAUDE.md | 版本号 + 描述修正 | P0-4, P0-7 |
| docs/design/DESIGN_V34.md | 标题改名 | P0-4 |
| docs/manuals/manual1_quickstart.md | 版本号对齐 | P0-4 |
| .gitignore | 追加 .ontoagent/ | P0-5 |
| .ontoagent/*.db | git rm --cached | P0-5 |
| execution/functions/general.py | stub 返回失败 | P0-8 |

## 测试验证

1. `pytest tests/ -x -q` → 1714 passed（回归）
2. general.py 修改后对应测试需要同步更新（test_general_functions.py）
3. E2E 脚本重跑确保审批链路不受影响
