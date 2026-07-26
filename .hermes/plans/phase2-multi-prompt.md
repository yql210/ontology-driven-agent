# Phase 2 实施计划：多 Prompt 模板 + tool_gateway 更新

> **给 Claude Code 执行。TDD 方式。**
> **项目根目录：/opt/data/workspace/ontology-driven-agent**

## 背景

OntoAgent 的 graph_query 工具当前对 LLM 暴露 Cypher。生产环境用 NebulaGraph，需要让 LLM 生成 nGQL 而非 Cypher。核心策略：按 graph_backend 配置加载不同的 prompt 片段。

## 改动范围

### 新建文件
1. `src/ontoagent/agent/prompts/graph_query_nebula.md` — nGQL 语法规则 + Few-shot 示例
2. `src/ontoagent/agent/prompts/graph_query_cypher.md` — Cypher 语法规则 + 示例（整理现有）

### 修改文件
1. `src/ontoagent/agent/prompt.py` — 按 graph_backend 加载对应 prompt 片段，注入到 AGENT_SYSTEM_PROMPT
2. `src/ontoagent/config/tool_gateway.yaml` — 增加 NebulaGraph 写操作关键字（INSERT/UPSERT/DELETE VERTEX 等）
3. `src/ontoagent/agent/tool_gateway.py` — validate_graph_query 根据 backend 调整错误提示

### 测试
1. `tests/unit/agent/test_prompt_backend.py` — 测试 prompt 按后端切换

---

## Task 1: 创建 prompt 片段文件

### `src/ontoagent/agent/prompts/graph_query_nebula.md`

内容包含三部分（Schema-aware + 语法规则 + Few-shot）：

```markdown
## graph_query 查询语言：nGQL（NebulaGraph）

当前后端是 NebulaGraph，graph_query 工具接收 nGQL 查询语句。

### 关键语法规则（与 Cypher 的差异）

1. **属性访问必须带 Tag 前缀**：`v.TagName.fieldName`，不是 `v.fieldName`
   - ✅ 正确：RETURN n.CodeEntity.name
   - ❌ 错误：RETURN n.name

2. **相等比较用 ==**：WHERE n.CodeEntity.name == "value"，不是 =

3. **获取节点的 Tag 列表**：用 tags(n)，不是 labels(n)

4. **边的起点和终点**：直接在 MATCH pattern 中绑定变量
   - ✅ 正确：MATCH (a)-[r]->(b) RETURN id(a), id(b)
   - ❌ 错误：RETURN startNode(r), endNode(r)

5. **变长路径语法与 Cypher 完全一致**：-[:EDGE*1..3]->

### Schema（Tag 和 Edge 定义）

Tag（属性访问必须带 Tag 前缀）：
- CodeEntity(name, filePath, entityType, language, lines, docstring)
- ConceptEntity(name, description, category)
- DocEntity(name, filePath, docType)
- ResourceEntity(name, resourceType, description)
- ModuleEntity(name, description, moduleType)
- ChangeSetEntity(name, commitHash, author)
- LogEntity(name, level, message)
- AlertEntity(name, severity, message)
- ServiceEntity(name, description, endpoint)
- DataAsset(name, description, classification)
- ComplianceItem(name, regulation, requirement)
- CapabilityEntity(name, description)
- ProcessEntity(name, description)

Edge（关系类型，大写）：
CALLS, EXTENDS, IMPLEMENTS, IMPORTS, CONTAINS, SEMANTIC_IMPACT, DESCRIBES, ILLUSTRATES, DERIVED_FROM, CHANGED_IN, AFFECTS, TRIGGERED_BY, LOGS_FROM, RUNS_AS, SERVICE_DEPENDS_ON, PROCESSES_DATA, SUBJECT_TO, GOVERNED_BY, CALLS_SERVICE, PUBLISHES_TO, CONSUMED_BY, PRODUCES, CONSUMES, COMPOSES_INTO, REALIZED_BY, PRECEDES, EQUIVALENT_TO

### 查询示例

查找名为 xxx 的函数：
MATCH (n:CodeEntity) WHERE n.CodeEntity.name == "xxx" RETURN n.CodeEntity.name AS name, n.CodeEntity.filePath AS file_path

查找函数调用关系（3跳内）：
MATCH (n)-[:CALLS*1..3]->(callee) WHERE n.CodeEntity.name == "xxx" RETURN callee.CodeEntity.name AS callee_name

查找谁调用了 xxx：
MATCH (caller:CodeEntity)-[:CALLS]->(callee:CodeEntity) WHERE callee.CodeEntity.name == "xxx" RETURN caller.CodeEntity.name AS caller_name

查找所有 CALLS 边：
MATCH (a:CodeEntity)-[r:CALLS]->(b:CodeEntity) RETURN a.CodeEntity.name AS caller, b.CodeEntity.name AS callee

查找模块包含的实体：
MATCH (m:ModuleEntity)-[:CONTAINS]->(c) RETURN m.ModuleEntity.name AS module, c.CodeEntity.name AS entity

统计每种实体类型数量：
MATCH (n) RETURN tags(n) AS label, count(*) AS cnt

查找处理了数据资产的代码及合规约束：
MATCH (c:CodeEntity)-[:PROCESSES_DATA]->(d:DataAsset)-[:GOVERNED_BY]->(ci:ComplianceItem) WHERE d.DataAsset.name == "xxx" RETURN c.CodeEntity.name AS code, ci.ComplianceItem.name AS compliance
```

### `src/ontoagent/agent/prompts/graph_query_cypher.md`

```markdown
## graph_query 查询语言：Cypher（Neo4j）

当前后端是 Neo4j，graph_query 工具接收 Cypher 查询语句。

### Schema

实体标签：CodeEntity, ConceptEntity, DocEntity, ResourceEntity, ModuleEntity, ChangeSetEntity, LogEntity, AlertEntity, ServiceEntity, DataAsset, ComplianceItem, CapabilityEntity, ProcessEntity

关系类型：CALLS, EXTENDS, IMPLEMENTS, IMPORTS, CONTAINS, SEMANTIC_IMPACT, DESCRIBES, ILLUSTRATES, DERIVED_FROM, CHANGED_IN, AFFECTS, TRIGGERED_BY, LOGS_FROM, RUNS_AS, SERVICE_DEPENDS_ON, PROCESSES_DATA, SUBJECT_TO, GOVERNED_BY, CALLS_SERVICE, PUBLISHES_TO, CONSUMED_BY, PRODUCES, CONSUMES, COMPOSES_INTO, REALIZED_BY, PRECEDES, EQUIVALENT_TO

### 查询示例

查找名为 xxx 的函数：
MATCH (n:CodeEntity) WHERE n.name CONTAINS 'xxx' RETURN n.name, n.filePath LIMIT 10

查找函数调用关系：
MATCH (a)-[:CALLS]->(b) WHERE a.name CONTAINS 'xxx' RETURN a.name, b.name LIMIT 10

统计每种实体类型数量：
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt
```

---

## Task 2: 修改 prompt.py

在 AGENT_SYSTEM_PROMPT 中注入后端相关的查询语言提示。

**改动逻辑：**
1. 从 OntoAgentConfig 读取 graph_backend
2. 加载对应的 prompts/graph_query_{backend}.md 文件内容
3. 替换 AGENT_SYSTEM_PROMPT 中现有的 graph_query 描述行（第 82 行附近）

**当前代码（第 82 行）：**
```python
| graph_query | 自定义 Cypher 查询 | cypher(必填) |
```

**改为动态：**
```python
| graph_query | 自定义{QUERY_LANG}查询 | query(必填) |
```

并在 Schema 段之后追加 `{GRAPH_QUERY_GUIDE}` 占位符，注入完整 prompt 片段。

**实现方式：**
- 新增函数 `_load_graph_query_guide() -> str`，从文件加载 prompt 片段
- 新增函数 `_get_query_lang_name() -> str`，返回 "Cypher" 或 "nGQL"
- 在 AGENT_SYSTEM_PROMPT 模板中注入

**重要：** prompt.py 是模块级常量（AGENT_SYSTEM_PROMPT），在 import 时构建。config 也在 import 时读取。确保不引入循环依赖。

---

## Task 3: 更新 tool_gateway 配置

### `src/ontoagent/config/tool_gateway.yaml`

在 blocked_keywords 中增加 NebulaGraph 写操作关键字：

```yaml
enabled: true
blocked_keywords:
  # Neo4j/Cypher 写操作
  - "SET"
  - "DELETE"
  - "REMOVE"
  - "CREATE"
  - "MERGE"
  - "DROP"
  - "DETACH DELETE"
  - "FOREACH"
  - "CALL apoc"
  # NebulaGraph 写操作
  - "INSERT VERTEX"
  - "INSERT EDGE"
  - "UPSERT VERTEX"
  - "UPSERT EDGE"
  - "DELETE VERTEX"
  - "DELETE EDGE"
  - "UPDATE VERTEX"
  - "UPDATE EDGE"
  - "CREATE TAG"
  - "CREATE EDGE"
  - "CREATE SPACE"
  - "ALTER TAG"
  - "ALTER EDGE"
  - "DROP TAG"
  - "DROP EDGE"
  - "DROP SPACE"
  - "SUBMIT JOB"
```

### `src/ontoagent/agent/tool_gateway.py`

validate_graph_query 的错误消息改为后端无关：

```python
def validate_graph_query(query: str) -> tuple[bool, str]:
    if not _ENABLED:
        return True, "ok"
    if is_write_cypher(query):
        return False, "写操作被拦截。写操作请使用 express_intent。"
    return True, "ok"
```

把参数名从 `cypher` 改为 `query`（后端无关），但保持向后兼容。

---

## Task 4: 测试

### `tests/unit/agent/test_prompt_backend.py`

- test_nebula_backend_prompt_contains_ngql_rules
- test_neo4j_backend_prompt_contains_cypher_rules
- test_prompt_includes_tag_prefix_rule (验证 nGQL prompt 包含 "Tag 前缀" 规则)
- test_prompt_includes_schema_section

### `tests/unit/agent/test_tool_gateway_nebula.py`（或扩展现有测试）

- test_blocked_nebula_insert_vertex
- test_blocked_nebula_upsert_vertex
- test_blocked_nebula_delete_vertex
- test_blocked_nebula_create_tag
- test_allowed_match_query_not_blocked

---

## 实施顺序（TDD）

1. 先创建两个 prompt 片段 .md 文件
2. 修改 prompt.py（加载 + 注入）
3. 写 prompt 测试 → 跑测试
4. 更新 tool_gateway.yaml + tool_gateway.py
5. 写 tool_gateway 测试 → 跑测试
6. ruff check + ruff format
7. 全量回归

---

## 编码规范

- `from __future__ import annotations`
- 类型注解
- 行宽 120
- 测试 markers: unit
- 不破坏现有 AGENT_SYSTEM_PROMPT 的其他内容
