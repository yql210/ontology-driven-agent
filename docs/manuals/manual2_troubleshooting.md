---
title: OntoAgent 运维排查手册
date: 2026-06-28
lastmod: 2026-06-28
---

# OntoAgent 运维排查手册

本文档面向 OntoAgent 运维人员与开发者，系统梳理从诊断命令、外部服务、约束审批、Trace、构建流水线到 Agent 对话的常见故障场景与排查方法。

---

## 1. 诊断命令速查

### 1.1 CLI 诊断命令

```bash
# 查看 OntoAgent 版本与配置摘要
uv run ontoagent version
uv run ontoagent info

# 全量测试（单元 + 集成）
uv run pytest tests/ -v --tb=short

# 仅单元测试（无需 Neo4j）
uv run pytest tests/ -v -m "not integration"

# 代码质量检查
uv run ruff check src/ tests/
uv run ruff check --fix src/ tests/     # 自动修复
uv run ruff format --check src/ tests/   # 格式校验

# 类型检查
uv run pyright src/
```

### 1.2 数据库状态速查

```bash
# Neo4j 节点与关系统计
uv run python -c "
from ontoagent.store import Neo4jGraphStore
import os

store = Neo4jGraphStore(
    uri=os.environ.get('ONTOAGENT_NEO4J_URI', 'bolt://localhost:7687'),
    user=os.environ.get('ONTOAGENT_NEO4J_USER', 'neo4j'),
    password=os.environ.get('ONTOAGENT_NEO4J_PASSWORD', ''),
)
with store:
    for label in ['CodeEntity','ConceptEntity','DocEntity','ResourceEntity','ModuleEntity','ChangeSetEntity']:
        r = store.query(f'MATCH (n:{label}) RETURN count(n) AS cnt')
        print(f'{label}: {r[0][\"cnt\"]}')
    r = store.query('MATCH ()-[r]->() RETURN count(r) AS cnt')
    print(f'Relations: {r[0][\"cnt\"]}')
"

# ChromaDB 统计
uv run python -c "
import os
from ontoagent.store.chroma_store import ChromaStore

cs = ChromaStore(
    persist_dir=os.environ.get('ONTOAGENT_CHROMA_DIR', '.chroma'),
    ollama_url=os.environ.get('ONTOAGENT_OLLAMA_URL', 'http://localhost:11434'),
    embedding_model=os.environ.get('ONTOAGENT_EMBEDDING_MODEL', 'qwen2.5-coder:0.5b'),
)
print(f'Total entities: {cs.count()}')
cs.close()
"
```

---

## 2. 外部服务检查

OntoAgent 依赖 **Neo4j**（图数据库）和 **Ollama**（本地 LLM/Embedding）。

### 2.1 Neo4j 连通性

**常见错误**: `neo4j.exceptions.ServiceUnavailable: Connection refused`

**排查步骤**:

```bash
# 1. 检查 Neo4j 进程
ps aux | grep neo4j

# 2. 检查 Bolt 端口（默认 7687）
ss -tlnp | grep 7687

# 3. HTTP API 探测
curl -s http://localhost:7474 | head -5

# 4. Python 驱动连接测试
uv run python -c "
from neo4j import GraphDatabase
import os
d = GraphDatabase.driver(
    os.environ.get('ONTOAGENT_NEO4J_URI', 'bolt://localhost:7687'),
    auth=(os.environ.get('ONTOAGENT_NEO4J_USER', 'neo4j'),
          os.environ.get('ONTOAGENT_NEO4J_PASSWORD', ''))
)
with d.session() as s:
    print(s.run('RETURN 1 AS test').single())
d.close()
"
```

**解决方案**:
- Docker 启动：`docker run -d -p 7474:7474 -p 7687:7687 neo4j:5`
- 裸机启动：`neo4j console`
- 首次使用需通过 `http://localhost:7474` 初始化密码

**认证错误** `AuthError`: 检查 `.env` 中 `ONTOAGENT_NEO4J_PASSWORD` 是否与 Neo4j 设置一致。

### 2.2 Ollama 连通性

**常见错误**: `httpx.ConnectError: Connection refused`

```bash
# 检查服务可用性
curl -s http://localhost:11434/api/tags | python -m json.tool

# 测试 Embedding
curl -s http://localhost:11434/api/embed \
  -d '{"model":"qwen2.5-coder:0.5b","input":["test"]}'
```

**解决方案**:
```bash
ollama serve                                 # 启动服务
ollama pull qwen2.5-coder:0.5b               # 拉取嵌入模型
ollama pull qwen3.5:9b                        # 拉取语义 LLM
ollama list                                   # 查看已安装模型
```

> **离线/降级模式**: 构建时加 `--skip-semantic` 跳过语义提取阶段，无需 Ollama。

---

## 3. 约束系统故障排查

约束系统位于 `execution/constraints/` 目录，核心文件为 `engine.py`（`ConstraintEngine`）、`loader.py`（加载）、`guards.py`（Guard Pipeline）。YAML 配置分散在：

| 文件 | 路径 | 作用 |
|------|------|------|
| 遍历约束 | `src/ontoagent/pipeline/constraints.yaml` | `traversal_constraints` 定义 |
| 覆盖配置 | `src/ontoagent/config/constraint_overrides.yaml` | 局部覆盖与白名单 |

### 3.1 约束不生效？

**现象**: 预期被 BLOCK 的操作却通过了。

**原因排查**:

1. **约束名拼写错误** — 检查 `constraints.yaml` 中 `traversal_constraints` 的 key 名称与代码中引用的名称一致。
2. **遍历路径无匹配** — 如果 `relation_chain` 在图中找不到对应关系，`ConstraintEngine._traverse()` 返回空结果，默认返回 `ALLOW`。
3. **value_mapping 未覆盖目标值** — `value_mapping` 根据 `collect_property` 的实际值映射到 GuardLevel，如果某个值未在映射中出现，默认也是 `ALLOW`。

**诊断命令**:

```bash
# 直接通过 Python 测试约束
uv run python -c "
from ontoagent.execution.constraints.engine import ConstraintEngine
from ontoagent.execution.constraints.loader import load_constraints
from ontoagent.store.neo4j_store import Neo4jGraphStore
import os

store = Neo4jGraphStore(
    uri=os.environ['ONTOAGENT_NEO4J_URI'],
    user=os.environ['ONTOAGENT_NEO4J_USER'],
    password=os.environ['ONTOAGENT_NEO4J_PASSWORD'],
)
constraints = load_constraints()
engine = ConstraintEngine(store, constraints)
decision = engine.evaluate('<entity_id>', 'data_sensitivity')
print(f'Level: {decision.level.value}, Reason: {decision.reason}')
store.close()
"
```

### 3.2 BLOCK/WARN 异常 — 所有操作都被 BLOCK

**症状**: 无论执行什么操作，Guard Pipeline 都返回 `BLOCK`。

**常见原因**:

1. **`constraints.yaml` 的 `traversal_constraints` 配置错误** — 例如 `relation_chain` 使用了不存在的 Neo4j 关系类型、`source_label` 或 `target_label` 拼写错误。`ConstraintEngine.__init__` 在初始化时会对 `relation_chain` 做校验，非法关系类型直接抛出 `ValueError`。如果初始化成功但运行时全部 BLOCK，说明遍历路径匹配到了实体但 `value_mapping` 将所有值映射为 BLOCK。
2. **图中大量实体被标记为敏感** — 检查 DataAsset 实体的 `sensitivity` 属性值是否被错误批量写入 `restricted` 或更高等级。

**应急处理**: 使用 `constraint_overrides.yaml` 的 `allow_all` 白名单临时放行：

```yaml
overrides:
  - type: allow_all
    target_entity: "CodeEntity:your_function_name"
    reason: "紧急排查临时放行"
```

> `target_entity` 格式：`{Neo4jLabel}:{entity_name}`。

### 3.3 `constraint_overrides.yaml` 配置错误

- **`patch` 类型**: 确保 `target` 字段匹配 `constraints.yaml` 中 `traversal_constraints` 的 key 名称。
- **`modify` / `remove_values` / `add_values`**: 值名必须精确匹配图中实体的实际属性值（区分大小写）。
- **`allow_all` 类型**: `target_entity` 格式 `Label:name` 中的 Label 必须是 Neo4j 中实际存在的标签。
- **`expires` 字段**: 可选，格式 `YYYY-MM-DD`，到期后覆盖自动失效（需配合定期检查机制）。

---

## 4. 审批系统故障排查

审批系统由 `execution/constraints/approval_gate.py`（`ApprovalGate`）和 `config/approval_policy.yaml` 配置驱动。

### 4.1 审批单不出现

**排查流程**:

1. **确认策略链是否包含该场景** — 检查 `approval_policy.yaml` 的 `policies` 列表：

   ```yaml
   policies:
     - guard_result      # Guard Pipeline 结果触发审批
     - action_approval   # ActionConfig.requires_approval 触发
     - function_danger   # Function.danger_level 触发
   ```

   如果某个策略被注释掉，对应的审批场景就不会触发。

2. **检查 `guard_result` 配置** — 确认 `on_block` 和 `on_warn` 的值：

   ```yaml
   guard_result:
     on_block: require_approval   # 改为 auto_reject 则直接拒绝不生成审批单
     on_warn: require_approval    # 改为 auto_allow 则直接放行
   ```

3. **检查 `function_danger` 配置** — 确认操作的 danger_level 是否在 `require_approval` 列表中：

   ```yaml
   function_danger:
     auto_approve:
       - read
     require_approval:
       - read_sensitive
       - write
       - admin
   ```

4. **检查 Function 的 `danger_level`** — 在 `execution/functions/` 目录找到对应 Function，确认其 `danger_level` 属性值是否正确设置。

### 4.2 令牌过期

**症状**: 用户拿到审批令牌后，系统提示"令牌未找到或已过期"。

**原因**: `approval_policy.yaml` 中 `token.ttl` 设置过短（默认 600 秒 = 10 分钟）。

```yaml
token:
  ttl: 600           # 令牌有效期（秒）
  max_pending: 10    # 同时最多待审批数
```

**解决方案**: 增大 `ttl` 值（例如 1800 = 30 分钟），或加快审批响应速度。

### 4.3 `approval_policy.yaml` 配置错误

- **`policies` 列表** — 策略名称必须与引擎中注册的策略类名一致：`guard_result`、`action_approval`、`function_danger`。拼写错误会导致策略被静默跳过。
- **`function_danger.auto_approve` / `require_approval`** — 危险等级值必须是 `read`、`read_sensitive`、`write`、`admin` 中的一个。
- **JSON 缩进** — YAML 对缩进敏感，确保使用 2 空格缩进且不含 Tab。

---

## 5. Trace 故障排查

Trace 系统位于 `agent/trace.py`，使用 SQLite 持久化（`.traces.db`）。

### 5.1 审批步骤不显示在 Trace 中

**症状**: 对话 Trace 中缺少 `approval_required` / `approval_resolved` 步骤。

**原因**: Trace 步骤类型包括 `"approval_required"` 和 `"approval_resolved"`，只有当 Agent 在对话流程中显式调用了审批相关工具并记录步骤，Trace 中才会出现。如果审批在后台执行（如 Guard Pipeline 直接 BLOCK 且 `on_block: auto_reject`），则不会产生 Trace 步骤。

**排查**:
1. 确认 `approval_policy.yaml` 中 `on_block` / `on_warn` 设为 `require_approval`（而非 `auto_reject` / `auto_allow`）。
2. 检查 Agent 工具链是否正确集成了 `express_intent` 工具和审批流程。

### 5.2 数据库损坏

**症状**: Trace 页面加载失败、丢失历史记录，日志中出现 `sqlite3.DatabaseError`。

**原因**: `.traces.db` 文件损坏（异常关机、磁盘满、并发写入冲突）。

**解决方案**:

```bash
# 备份后重建
cp .traces.db .traces.db.bak
rm -f .traces.db
# 重启服务后自动初始化新数据库
```

> 注意：重建后历史 Trace 将丢失。如需保留部分数据，可用 `sqlite3 .traces.db.bak ".dump"` 查看并手动恢复。

---

## 6. 构建流水线故障

构建流水线入口为 `pipeline/builder.py::OntoAgentBuilder.build`，分 5 个阶段（Parse → Structural Write → Doc-Code Link → Semantic → Clustering → Vector Index）。

### 6.1 构建结果实体数为 0

**症状**: `uv run ontoagent build <path>` 完成后 Neo4j 中没有任何实体。

**排查**:

1. **路径是否正确** — 确认目标路径存在且包含支持的文件类型（`.py`、`.java`、`.md`、`.rst`）：
   ```bash
   find /your/target/path -name "*.py" | head -5
   ```

2. **检查 `ONTOAGENT_BUILD_SKIP_DIRS`** — 是否过滤了所有文件。默认跳过目录包括 `__pycache__`、`.git`、`node_modules` 等。如果目标目录恰好被这些规则覆盖，需要调整环境变量。

3. **检查 Stage 1 日志** — 开启详细模式查看解析结果：
   ```bash
   uv run ontoagent build /path --verbose-build
   ```

### 6.2 构建卡住不动

**常见原因**:

1. **Ollama 超时** — Stage 3（语义提取）中每个函数/类需调 LLM 生成语义描述，大文件可能导致长时间等待。解决方案：
   ```bash
   uv run ontoagent build /path --skip-semantic
   ```

2. **Neo4j 写入阻塞** — 检查 Neo4j 是否有长时间运行的事务：
   ```cypher
   SHOW TRANSACTIONS
   ```

3. **内存不足** — 大仓库 Stage 4（模块聚类）计算量大。跳过聚类：
   ```bash
   uv run ontoagent build /path --skip-clustering
   ```

### 6.3 Ollama 超时

**症状**: Stage 3 或 Stage 5 长时间等待无输出，最终抛出 `httpx.ReadTimeout`。

```bash
# 快速验证 Ollama 响应时间
time curl -s http://localhost:11434/api/generate \
  -d '{"model":"qwen2.5-coder:0.5b","prompt":"test","stream":false}'
```

**解决方案**:
1. 使用更小的嵌入模型（如 `qwen2.5-coder:0.5b` 仅 390MB）。
2. GPU 加速：`ollama serve` 在有 GPU 的环境下自动启用。
3. 批量构建大项目时加 `--skip-semantic --skip-clustering` 只保留结构化数据。

---

## 7. Agent 对话故障

### 7.1 401 / 400 错误

**症状**: Agent 对话请求返回 `401 Unauthorized` 或 `400 Bad Request`。

**排查**:

1. **API Key 无效** — 检查 `.env` 中 `ONTOAGENT_AGENT_API_KEY` 是否填写正确。
2. **Base URL 错误** — 确认 `ONTOAGENT_AGENT_BASE_URL` 格式正确（包含完整路径，如 `https://open.bigmodel.cn/api/anthropic`）。
3. **模型名不存在** — `ONTOAGENT_AGENT_LLM_MODEL` 必须与 API 提供商支持的模型名一致。
4. **Provider 设置** — `ONTOAGENT_AGENT_LLM_PROVIDER`（默认 `zhipu`）需与 Base URL 对应。

### 7.2 DeepSeek `reasoning_content` 兼容问题

**症状**: 使用 DeepSeek API 进行多轮对话时报 `400` 错误，错误信息涉及 `reasoning_content` 字段。

**根本原因**: DeepSeek 的 `deepseek-reasoner`（deepseek-v4-pro）模型返回的 `reasoning_content` 字段在 LangChain `ChatOpenAI` 适配层中不被保留。当第二轮对话把上轮 assistant 消息回传时，`reasoning_content` 导致 API 校验失败。

**解决方案**: **使用 `deepseek-chat` 模型（映射到 deepseek-v4-flash）而不是 `deepseek-reasoner`**。

```bash
# .env 配置
ONTOAGENT_AGENT_LLM_PROVIDER=openai          # DeepSeek 使用 OpenAI 兼容 API
ONTOAGENT_AGENT_LLM_MODEL=deepseek-chat
ONTOAGENT_AGENT_API_KEY=sk-your-deepseek-key
ONTOAGENT_AGENT_BASE_URL=https://api.deepseek.com/v1
```

> 如果必须使用 reasoning 模型，需要在 Agent 代码层过滤掉 assistant 消息中的 `reasoning_content` 字段后再回传。

---

## 8. 数据维护

### 8.1 完全重置 Neo4j

```bash
uv run python -c "
import os
from ontoagent.store import Neo4jGraphStore

store = Neo4jGraphStore(
    uri=os.environ['ONTOAGENT_NEO4J_URI'],
    user=os.environ['ONTOAGENT_NEO4J_USER'],
    password=os.environ['ONTOAGENT_NEO4J_PASSWORD'],
)
with store:
    # 清空所有节点和关系
    store.query('MATCH (n) DETACH DELETE n')
    print('Neo4j cleared.')
"
```

### 8.2 完全重置 ChromaDB

```bash
rm -rf .chroma/
```

### 8.3 完全重置所有数据

```bash
# 1. 清空 Neo4j（参考 8.1 的脚本）
# 2. 清空 ChromaDB
rm -rf .chroma/
# 3. 清空 Trace 数据库
rm -f .traces.db
# 4. 重新构建
uv run ontoagent build /your/repo/path
```

### 8.4 备份

```bash
# Neo4j 备份（需要 neo4j-admin 工具）
neo4j-admin database dump neo4j --to=/backup/neo4j-$(date +%Y%m%d).dump

# ChromaDB 备份
tar czf /backup/chroma-$(date +%Y%m%d).tar.gz .chroma/

# Trace 数据库备份
cp .traces.db /backup/traces-$(date +%Y%m%d).db
```

### 8.5 数据一致性检查

```bash
# 检查孤立实体（没有出入关系的节点）
uv run python -c "
import os
from ontoagent.store import Neo4jGraphStore

store = Neo4jGraphStore(
    uri=os.environ['ONTOAGENT_NEO4J_URI'],
    user=os.environ['ONTOAGENT_NEO4J_USER'],
    password=os.environ['ONTOAGENT_NEO4J_PASSWORD'],
)
with store:
    # 查找没有任何关系的 CodeEntity
    r = store.query('MATCH (n:CodeEntity) WHERE NOT (n)--() RETURN count(n) AS cnt')
    print(f'Orphan CodeEntities: {r[0][\"cnt\"]}')

    # 检查重复节点（相同 id）
    r = store.query('MATCH (n) WITH n.id AS nid, collect(n) AS nodes WHERE size(nodes) > 1 RETURN nid, size(nodes)')
    for row in r:
        print(f'Duplicate: {row[\"nid\"]} x{row[\"size(nodes)\"]}')
"

# 重新创建约束和索引
uv run python -c "
import os
from ontoagent.store import Neo4jGraphStore

store = Neo4jGraphStore(
    uri=os.environ['ONTOAGENT_NEO4J_URI'],
    user=os.environ['ONTOAGENT_NEO4J_USER'],
    password=os.environ['ONTOAGENT_NEO4J_PASSWORD'],
)
with store:
    store.ensure_constraints()
    print('Constraints verified.')
"
```

---

## 附录：日志分析

### 启用详细日志

```bash
# CLI 详细模式
uv run ontoagent build . --verbose-build

# Python 环境变量方式
export LOGLEVEL=DEBUG
uv run ontoagent build .
```

### 关键日志关键词

| 关键词 | 含义 | 建议 |
|--------|------|------|
| `Stage 1: Parse` | 开始 AST 解析 | 正常 |
| `Stage 2: Structural Write` | 写入 Neo4j | 如卡住检查 Neo4j 连接 |
| `Stage 3: Semantic` | LLM 语义提取 | 如跳过检查 Ollama |
| `Ollama unavailable` | Ollama 连接失败 | 可忽略，Stage 3 自动降级 |
| `Error in batch embedding` | Embedding 批次失败 | 检查 Ollama 模型是否拉取 |
| `Traversal query failed` | 约束遍历查询异常 | 检查 Neo4j 连接或索引 |
| `Approval token not found or expired` | 审批令牌过期 | 增大 token.ttl 或加速审批 |
| `ConstraintViolationError` | 本体约束校验失败 | 检查 constraints.yaml 配置 |
