# OntoAgent 图数据库多后端适配方案 D1：多 Prompt 方案

> **状态：待用户确认**
> **日期：2026-07-26**
> **前置决策：生产环境强制 NebulaGraph，LLM 必须保留动态图查询能力**
> **核心策略：查询语言差异通过 Prompt 模板隔离，通过配置切换后端，同时支持 Neo4j + NebulaGraph**

---

## 一、核心洞察

之前所有方案（V2/CQRS/C-IM）都在**代码层**解决查询语言差异——要么写 nGQL 编译器，要么写内存图引擎。

但 NebulaGraph 实查文档证明：**它的 MATCH 语法跟 Cypher 90% 相同**。差异只有几个点：
- `=` → `==`（相等比较）
- `labels(n)` → `tags(v)`
- 属性访问 `n.name` → `n.tag.name`（需带 tag 名）
- 无 MERGE（但 LLM 的 graph_query 是只读查询，不用 MERGE）

**结论：查询语言差异应该在 Prompt 层解决，而不是代码层。** 一套代码，多套 Prompt，配置切换。

---

## 二、方案架构

```
┌─────────────────────────────────────────────────┐
│              OntoAgent Agent 层                   │
│                                                  │
│   LangGraph ReAct + graph_query 工具              │
│                                                  │
│   Prompt 模板（按配置加载）：                       │
│   ├─ prompt_cypher.yaml   (Neo4j 开发环境)         │
│   └─ prompt_ngql.yaml     (NebulaGraph 生产环境)   │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│              GraphStore 抽象层                     │
│                                                   │
│   query(query_string, params) → list[dict]        │
│                                                   │
│   ┌──────────────┐     ┌────────────────────┐     │
│   │ Neo4jStore   │     │ NebulaGraphStore   │     │
│   │ (Cypher)     │     │ (nGQL MATCH)       │     │
│   └──────────────┘     └────────────────────┘     │
└───────────────────────────────────────────────────┘
```

**数据流：**
1. 配置 `GRAPH_BACKEND=nebula` → 加载 `prompt_ngql.yaml`
2. LLM 根据 prompt 生成 nGQL 查询字符串
3. `graph_query(query_string)` → `NebulaGraphStore.query()` → NebulaGraph 执行
4. 结果统一返回 `list[dict]`

---

## 三、三层改造

### 第 1 层：Prompt 模板（核心创新）

在 `agent/` 下新增 prompt 模板目录，每个后端一套。**LLM 根据当前后端的 prompt 语法生成对应查询语言。**

**prompt_cypher.yaml（现有，Neo4j 开发环境）：**
```yaml
graph_query_syntax: cypher
graph_query_description: |
  执行 Cypher 查询。
  属性访问：n.fieldName（直接访问）
  相等比较：WHERE n.name = "value"
  获取标签：labels(n)

examples:
  - name: 函数调用关系
    query: |
      MATCH (a)-[:CALLS]->(b) WHERE a.name CONTAINS 'xxx' RETURN a.name, b.name
  - name: 模块依赖
    query: |
      MATCH (m:ModuleEntity)-[:CONTAINS]->(c) RETURN m.name, c.name
```

**prompt_ngql.yaml（新增，NebulaGraph 生产环境）：**
```yaml
graph_query_syntax: ngql
graph_query_description: |
  执行 nGQL 查询（NebulaGraph）。
  重要语法差异（与 Cypher 不同）：
  - 属性访问：v.tagName.fieldName（必须带 Tag 名前缀）
    错误：RETURN v.name
    正确：RETURN v.CodeEntity.name
  - 相等比较：WHERE v.tagName.field == "value"（用 == 不是 =）
  - 获取标签：tags(v)（不是 labels(v)）
  - 其他语法（MATCH/WHERE/WITH/RETURN/ORDER BY/LIMIT/变长路径）与 Cypher 完全一致

examples:
  - name: 函数调用关系
    query: |
      MATCH (a)-[:CALLS]->(b) WHERE a.CodeEntity.name CONTAINS "xxx" RETURN a.CodeEntity.name, b.CodeEntity.name
  - name: 模块依赖
    query: |
      MATCH (m:ModuleEntity)-[:CONTAINS]->(c) RETURN m.ModuleEntity.name, c.CodeEntity.name
  - name: 变长路径（与 Cypher 完全一致）
    query: |
      MATCH (n)-[:CALLS*1..3]->(callee) WHERE n.CodeEntity.id == "xxx" RETURN callee.CodeEntity.name
```

**Prompt 加载逻辑（`agent/prompt.py`）：**
```python
# 根据 GRAPH_BACKEND 配置加载对应模板
backend = config.graph_backend  # "neo4j" | "nebula"
template = load_yaml(f"agent/prompts/graph_query_{backend}.yaml")
# 注入到 system prompt
```

### 第 2 层：NebulaGraphStore（写入 + 查询）

新增 `store/nebula_store.py`，实现 `NebulaGraphStore(GraphStore)`：
- 使用 `nebula-python` 客户端
- `query()` 直接透传 nGQL 给 NebulaGraph 执行
- 写入操作（merge_node 等）内部转换为 NebulaGraph 的 INSERT/UPSERT
- Schema 初始化：从 `schema.py` 自动生成 CREATE TAG/EDGE DDL

### 第 3 层：配置切换

**`config.py`：**
```python
graph_backend: str = "neo4j"  # "neo4j" | "nebula"
```

**环境变量：**
```bash
# 开发环境
ONTOAGENT_GRAPH_BACKEND=neo4j

# 生产环境
ONTOAGENT_GRAPH_BACKEND=nebula
ONTOAGENT_NEBULA_GRAPH_HOST=...
ONTOAGENT_NEBULA_GRAPH_PORT=9669
ONTOAGENT_NEBULA_USER=...
ONTOAGENT_NEBULA_PASSWORD=...
ONTOAGENT_NEBULA_SPACE=ontoagent
```

---

## 四、为什么这个方案是最优的

| 维度 | V2（nGQL 全适配） | C-IM（内存图） | **D1（多 Prompt）** |
|---|---|---|---|
| 工作量 | 13-21 天 | 8-11 天 | **5-7 天** |
| 代码改动 | 22 个文件 | 8 个文件 | **3-5 个文件** |
| LLM 出错率 | 高（原估） | N/A | **低**（MATCH 90% 兼容 Cypher） |
| 多后端支持 | 仅 NebulaGraph | 仅 NebulaGraph+内存图 | **Neo4j + NebulaGraph 同时支持** |
| 加新数据库 | 重写编译器 | 重写内存图 | **加一套 prompt 即可** |
| ShapeEvaluator 改动 | 大手术 | 大手术 | **不改**（见下方说明） |
| 性能 | 50-200ms | μs 级 | 与 V2 相同（NebulaGraph 原生） |

### 关键问题：ShapeEvaluator 和内部模块的 Cypher 怎么办？

这是 C-IM 审查发现的核心难题。D1 的解法是**分两条路**：

**路径 A：LLM 动态查询（graph_query 工具）→ 多 Prompt**
- LLM 根据后端配置生成对应查询语言
- 内置 Cypher→nGQL 差异已在 prompt 中说明

**路径 B：内部模块的 Cypher（ShapeEvaluator 等）→ store 内部适配**
- ShapeEvaluator 的 Cypher 是**固定模式的**（`MATCH (n)-[:REL*1..3]->(collected:Label) WHERE n.id=$entity_id RETURN collected.field AS val`）
- 这部分在 `NebulaGraphStore` 内部做 Cypher→nGQL 的简单转换（因为模式固定，转换规则简单）
- 不需要通用编译器，只需针对 ShapeEvaluator 的固定模式做适配

---

## 五、实施计划

### Phase 1：NebulaGraphStore 基础（2 天）
- 新增 `store/nebula_store.py`（连接 + query 执行）
- 新增 `store/nebula_schema.py`（从 schema.py 自动生成 DDL）
- 实现 merge_node / merge_relation（INSERT + UPSERT）
- 单元测试

### Phase 2：多 Prompt 模板（1 天）
- 新增 `agent/prompts/graph_query_nebula.yaml`
- 修改 `agent/prompt.py` 按后端配置加载模板
- 验证 LLM 生成 nGQL 的质量（手工测试 20 条查询）

### Phase 3：内部 Cypher 适配（2 天）
- ShapeEvaluator 固定模式的 Cypher→nGQL 转换（在 NebulaGraphStore 内部）
- 图可视化 API（graph.py）的 Cypher→nGQL 转换
- 其他高频内部查询适配

### Phase 4：集成测试（1 天）
- 真实 NebulaGraph 实例端到端测试
- L3 运行时评估在 NebulaGraph 上执行
- 双后端对比测试

**总预估：6 天**

---

## 六、风险与应对

| 风险 | 严重度 | 应对 |
|---|---|--- |
| LLM 生成 nGQL 出错 | 🟡 中 | MATCH 90% 兼容 Cypher；关键差异（==/tags/属性前缀）在 prompt 明确强调；graph_query 有错误兜底 |
| NebulaGraph 强 Schema | 🟡 中 | NebulaSchemaInitializer 从 schema.py 自动生成 DDL；自定义实体用通用 Tag |
| 无 MERGE | 🟡 中 | merge_node 用 UPSERT VERTEX（NebulaGraph 支持） |
| 性能未验证 | 🟡 中 | Phase 4 benchmark；ShapeEvaluator 可选走内存缓存 |
| 属性访问需带 Tag 前缀 | 🟡 中 | prompt 明确示例；store 内部查询自动补全 |

---

## 七、新增/修改文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `store/nebula_store.py` | 新增 | NebulaGraphStore 实现 |
| `store/nebula_schema.py` | 新增 | DDL 自动生成 |
| `store/factory.py` | 新增 | 按 config 选择后端 |
| `agent/prompts/graph_query_nebula.yaml` | 新增 | NebulaGraph nGQL prompt 模板 |
| `agent/prompt.py` | 修改 | 按后端加载 prompt |
| `config.py` | 修改 | 新增 graph_backend 配置 |
| `pyproject.toml` | 修改 | 新增 nebula-python 依赖 |

---

## 八、关键代码位置参考

| 关注点 | 文件 |
|---|---|
| graph_query 工具 | `agent/tools.py:51-93` |
| prompt 构建 | `agent/prompt.py` |
| graph_query 工具（MCP） | `api/mcp_server.py:117-127` |
| GraphStore 抽象 | `store/graph_store.py` |
| 现有 Neo4j 实现 | `store/neo4j_store.py` |
| Schema 定义 | `domain/schema.py:506-644` |
| 配置 | `config.py` |
