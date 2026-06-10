# LayerKG 知识图谱平台 — 架构深度分析与反思

> 本文档梳理 LayerKG 平台的完整架构、技术选型、设计决策、踩坑记录和改进方向。

---

## 一、系统全景

LayerKG 不只是一个"构建管线"，而是一个完整的**本体驱动的知识图谱平台**，包含 7 个核心子系统：

```
                          ┌─────────────────────────────────────┐
                          │         用户交互层                   │
                          │  CLI (Click)  │  Web (FastAPI+SSE)  │
                          │  MCP Server   │  Agent Chat         │
                          └───────┬──────────────┬──────────────┘
                                  │              │
                    ┌─────────────▼──────────────▼───────────────┐
                    │           Agent 编排层 (LangGraph ReAct)    │
                    │  9 Tools │ TraceCollector │ MemorySaver    │
                    └─────┬──────────┬──────────┬───────────────┘
                          │          │          │
          ┌───────────────▼──┐  ┌────▼────┐  ┌──▼──────────────────┐
          │  本体操作引擎     │  │ 影响传播 │  │  概念对齐器          │
          │  OntologyEngine  │  │ Impact  │  │  ConceptAligner     │
          │  (Palantir-style)│  │BFS+衰减 │  │  4层对齐策略         │
          └────────┬─────────┘  └────┬────┘  └─────────┬──────────┘
                   │                 │                  │
    ┌──────────────▼─────────────────▼──────────────────▼──────────┐
    │                     知识图谱存储层                             │
    │   Neo4j (结构+语义图)  │  ChromaDB (向量索引)                  │
    └───────────────────────┬──────────────────────────────────────┘
                            │
    ┌───────────────────────▼──────────────────────────────────────┐
    │                     知识构建层                                │
    │  Builder (5阶段管线) │ Butler (事件驱动增量) │ Migrations    │
    └─────────────────────────────────────────────────────────────┘
```

### 7 个子系统一览

| 子系统 | 核心文件 | 职责 | 技术选型 |
|--------|----------|------|----------|
| 知识构建 | `builder.py` (1269行) | 5 阶段管线：解析→写入→语义→聚类→向量化 | tree-sitter, Neo4j, Ollama |
| 图谱存储 | `neo4j_store.py` + `chroma_store.py` | 图数据库 + 向量数据库双层存储 | Neo4j 5.x, ChromaDB, HNSW |
| Agent 编排 | `agent/graph.py` + `tools.py` | ReAct 循环 + 9 工具 + 流式输出 | LangGraph, ChatOpenAI |
| 本体操作 | `ontology_engine.py` + YAML | Palantir 风格的 Action 定义与执行 | 动态导入, 审批流, 审计日志 |
| 影响传播 | `impact_propagator.py` | 双向 BFS + 权重衰减的变更影响分析 | 自定义 BFS, 权重矩阵 |
| 事件驱动 | `butler/` (6 文件) | Git 监听 → 自动增量更新 → 反思学习 | asyncio pub/sub, SQLite |
| 概念对齐 | `aligner.py` (371行) | 4 层策略的术语标准化 | 精确→别名→向量→图结构 |

---

## 二、构建管线（5 阶段详解）

### Stage 1: Parse — AST 解析

**技术：** tree-sitter + tree_sitter_python / tree_sitter_java

**核心逻辑：**

| 解析器 | 行数 | 提取实体 | 提取关系 | 特殊处理 |
|--------|------|----------|----------|----------|
| Python | 709 | module/function/class | CALLS/IMPORTS/CONTAINS | 过滤 120 个内置名；`self.method()` 只取方法名 |
| Java | 1098 | class/interface/enum/record/method/field/constructor | CALLS/IMPORTS/EXTENDS/IMPLEMENTS/CONTAINS | 方法签名含参数类型；Javadoc 提取；过滤 90 个 JDK 类型 |
| Doc | 194 | DocEntity (按标题切分) | — | Markdown/RST 正则切分；>5MB 跳过 |

**关键技术决策：**

1. **为什么选 tree-sitter 而不是 ast/pyjavaparser？**
   - tree-sitter 是 C 库的 Python 绑定，解析速度远快于纯 Python 的 `ast` 模块
   - 统一的多语言接口 — Python 和 Java 用同一套 API（`Language`, `Parser`, `Node`），新增语言只需添加 grammar
   - 增量解析能力（虽然当前未利用）— 这是未来增量构建的基础
   - 容错解析 — 即使源码有语法错误，也能提取部分 AST（`ERROR` 节点）

2. **名称解析策略 — "只取最后一段"**
   - `from os.path import join` → `join`（丢弃 `os.path`）
   - `self.process_data()` → `process_data`（丢弃 `self`）
   - 这是有意的简化：完整的名称解析需要类型推断，代价太高。但副作用是产生大量误匹配（同名函数在不同类中）

3. **关系解析器 (`relation.py`) 的分文件优先策略**
   - `imports`/`contains`/`calls` 关系优先在**同一文件内**解析目标实体
   - 这是合理的 — 大多数调用和导入发生在同一文件内
   - 但对跨文件的 `from foo import bar` 解析率较低

**踩过的坑：**

- **源码截断链** — 解析器截断到 500 字符 → Stage 3 LLM prompt 只展示 200 字符预览 → Stage 5 截断到 800 字符。三级截断导致信息逐级丢失
- **tree-sitter 的 ERROR 节点静默处理** — 语法错误的文件不报错，只返回 module 实体。排查"为什么某个类没出现在图谱中"非常困难
- **Doc 解析器误切分** — Markdown 代码块中的 `#` 注释、URL 片段的 `#` 会被误识别为标题

---

### Stage 2: Structural Write — 图写入

**技术：** neo4j-python-driver v6.2+ + tenacity

**批量写入模式：**
```cypher
-- 节点写入（每批 200 条）
UNWIND $batch AS props
MERGE (n:CodeEntity {id: props.id})
SET n += props

-- 关系写入（按 source_label + target_label + rel_type 分组共享模板）
UNWIND $batch AS item
MATCH (source:CodeEntity {id: item.source_id})
MATCH (target:CodeEntity {id: item.target_id})
MERGE (source)-[r:CALLS]->(target)
SET r += item.properties
```

**关键技术决策：**

1. **UNWIND + MERGE 而非 CREATE**
   - MERGE 是幂等的 — 多次 build 不会产生重复节点（基于 `id` 唯一约束）
   - 这是整个系统可以重复 build 而不产生脏数据的基础

2. **关系按三元组分组**
   - `(source_label, target_label, rel_type)` 相同的关系共享一条 Cypher 模板
   - 减少了 Cypher 解析开销和模板数量
   - 但分组本身有内存和 CPU 开销

3. **虚拟外部节点 (`file_path="__external__"`)**
   - 未解析的外部依赖（如 `import json`、`from django.db import models`）创建虚拟节点
   - 保证 IMPORTS 关系的完整性 — 每条 import 都有目标
   - 但虚拟节点没有真实语义，且跨次 build 会累积（因为每次 build 的外部依赖可能不同）

**tenacity 重试的局限：**

```python
retry=retry_if_exception_type((OSError, ConnectionError)) | stop=stop_after_attempt(3) | wait=wait_fixed(5)
```

只捕获 `OSError`/`ConnectionError`，遗漏了：
- `neo4j.exceptions.TransientError` — 事务冲突，可重试
- `neo4j.exceptions.DatabaseError` — 服务端错误
- `neo4j.exceptions.ServiceUnavailable` — 集群切换

---

### Stage 2.5: Doc-Code Link — 文档关联

**三层启发式匹配：**

```
DocEntity.content
    │
    ├─ 1. 路径匹配: "src/layerkg/parser/python_parser.py" in content（带边界检查）
    │
    ├─ 2. 文件名匹配: "python_parser.py" in content（降级策略，跳过根目录文件）
    │
    └─ 3. 标识符匹配: 从 ```python 代码块提取标识符 → 匹配 entity.name（length > 3）
```

**为什么不用 LLM 做文档关联？** Stage 3 的 LLM 语义提取已经包含了 `describes` 关系，但 Stage 3 是可降级的。Stage 2.5 用启发式作为保底，确保即使 LLM 不可用也有基本的文档-代码关联。

**局限：** 只识别 Markdown 中的 `python` 代码块；`"config"` 等通用词产生误匹配；最多 50 条关系/文档。

---

### Stage 3: Semantic Extraction — 语义提取

**技术：** 双 LLM 后端（Ollama qwen3.5:9b / OpenAI 兼容 API）

**提取流程：**
```
Entities (分批 20 个)
    │
    ▼ _build_prompt() — 构建结构化 prompt
    │   包含：实体名称、类型、源码预览 (200字符)、文件路径
    │   要求：返回 JSON，confidence ≥ 0.5
    │
    ▼ _call_llm() — 带重试的 LLM 调用
    │   指数退避：1s, 2s, 4s（最多 3 次）
    │   批次间隔：1s（释放 Ollama VRAM）
    │
    ▼ _parse_response() — 解析 LLM 输出
    │   1. 剥离 <think...>...</think > 标签（qwen3.5 特有）
    │   2. 从 ```json 代码块提取 JSON
    │   3. 校验 relation_type 和 confidence
    │
    ▼ _process_semantic_relations() — 写入 Neo4j
        Path A (概念目标): ConceptAligner 4层对齐 → 新建或复用 ConceptEntity
        Path B (代码目标): 模糊名称查找 → 创建 SEMANTIC_IMPACT 关系
```

**概念对齐的 4 层策略：**

| 层级 | 方法 | 置信度 | 适用场景 |
|------|------|--------|----------|
| 1 | 精确匹配 (term == concept.name) | 1.0 | 已有概念的精确引用 |
| 2 | 别名匹配 (term in concept.aliases) | 1.0 | 同义词（如 "KG" = "知识图谱"） |
| 3 | 向量匹配 (ChromaDB cosine) | 1/(1+distance) | 语义相似但名称不同 |
| 4 | 图结构匹配 (Jaccard 相似度) | Jaccard 系数 | 共享相同的代码实体邻居 |

**第 4 层的精妙之处**：即使两个概念的名称完全不同（如 "Repository Pattern" 和 "数据访问层"），如果它们关联的代码实体高度重叠，仍然能被识别为同一概念。这是图结构信息的独特优势。

**踩过的坑：**

- **qwen3.5 的 thinking 标签** — `<think...>...</think >` 会污染 JSON 解析，需要正则剥离
- **LLM 幻觉** — 编造不存在的实体名作为 source/target，必须在 `_process_semantic_relations()` 中做名称解析
- **prompt 中 "roughly equal numbers" 的副作用** — LLM 为凑数而降低质量，产生无意义的语义关系

---

### Stage 4: Module Clustering — 模块聚类

**算法：** 确定性 Label Propagation

```
Neo4j CodeEntity + CALLS/IMPORTS/EXTENDS/IMPLEMENTS
    │
    ▼ _load_graph() — 构建无向邻接表
    │   关键创新：同文件内实体全连接 (itertools.combinations)
    │   解决问题：纯关系图太稀疏（很多文件内的类/函数之间没有显式关系）
    │
    ▼ _label_propagation() — 迭代传播
    │   初始化：每个节点的标签 = 自身 ID
    │   每轮：节点采用邻居中最多数的标签（平局按字典序）
    │   收敛：标签不再变化时停止（最多 100 轮）
    │   确定性：所有排序用 sorted()
    │
    ▼ 后处理
        ├─ 按标签分组 → 社区
        ├─ os.path.commonpath → 模块名
        └─ 内聚度 = 实际内部边数 / 最大可能边数
```

**为什么选 Label Propagation？**
- 无需预设社区数量（对比 K-Means 需要指定 K）
- 近线性时间复杂度 O(V+E)，适合大规模图
- 天然适配 Neo4j 导出的邻接表
- 代价：结果依赖初始化顺序；对星型图（hub-spoke）效果差；没有层次结构

**虚拟文件内边的设计意图：**
代码文件中的实体（如一个类和它的方法）之间往往只有 CONTAINS 关系（有向），没有 CALLS/IMPORTS 等横向连接。如果只用真实关系构建图，同文件内的实体可能被分到不同的社区。虚拟边确保了"物理 proximity → 逻辑 proximity"的映射。

---

### Stage 5: Vector Index — 向量索引

**技术：** ChromaDB (HNSW cosine) + Ollama 嵌入

```
所有实体 (CodeEntity + DocEntity + ConceptEntity + ModuleEntity)
    │
    ▼ _entity_to_text() — 实体转文本
    │   CodeEntity: source[:800] 或 "function parse_file in src/foo.py"
    │   DocEntity:   content[:800]
    │   ConceptEntity: description 或 name
    │
    ▼ OllamaEmbeddingFunction — 批量嵌入
    │   每批 10 条 → /api/embed
    │   失败回退：逐条重试 → 零向量兜底
    │
    ▼ ChromaDB put_entities_batch
        每批 20 条 → upsert 到 layerkg_entities collection
```

**嵌入模型的选型困境：**

| 模型 | 维度 | 上下文 | 优点 | 缺点 |
|------|------|--------|------|------|
| qwen2.5-coder:0.5b | 可变 | ~2K | 代码理解好 | VRAM 占用大 |
| all-minilm-l6-v2 | 384 | 256 tokens | 轻量快速 | 上下文太短，代码截断严重 |
| bge-m3 | 1024 | 8K tokens | 长上下文，多语言 | VRAM 需求大 |

当前用 `all-minilm-l6-v2`（256 token 限制）是性能和资源的妥协。800 字符截断是经验值 — 测试发现 1600 字符 OK，1700 字符触发 400 错误。

---

## 三、Agent 编排层

### 架构：LangGraph ReAct 循环

```
START ──→ "agent" (LLM 节点) ──条件分支──→ "tools" (ToolNode) ──→ 回到 "agent"
                │                              │
                │ (无 tool_calls)               │ (执行工具)
                ▼                              ▼
              END                          返回工具结果
```

**LLM 配置：** 智谱 GLM-4-flash（通过 Anthropic 兼容 API），180s 超时，3 次重试

**注意双 LLM 策略：**
- **构建阶段**（Stage 3）：Ollama qwen3.5:9b 或 OpenAI 兼容 API — 做语义提取
- **Agent 阶段**：智谱 GLM-4-flash — 做推理和工具调用
- 两者独立配置，可以各自选择最优模型

### 9 个工具的技术实现

| 工具 | 数据源 | 算法/策略 | 关键限制 |
|------|--------|-----------|----------|
| `semantic_search` | ChromaDB | 余弦相似度 Top-K | 嵌入模型质量决定天花板 |
| `graph_query` | Neo4j | 用户生成的 Cypher | 100 行截断；Agent 可能写错 Cypher |
| `impact_analysis` | Neo4j + ImpactPropagator | 双向 BFS + 权重衰减 | 最大深度 3；权重矩阵需要调优 |
| `get_context` | Neo4j + ChromaDB | 节点属性 + 双向关系 + 语义相似 | 单实体视角 |
| `list_concepts` | ConceptAligner | Neo4j 全量扫描 | 无过滤/分页 |
| `get_module_tree` | ModuleClustering | **每次重新计算**聚类 | 无缓存，大图可能慢 |
| `detect_changes` | Git CLI | `git diff --name-status` | 只支持 Git |
| `export_graph` | Neo4j | 节点+边导出 | 有 limit 参数 |
| `ontology_action` | OntologyEngine | Action 解析 → 函数选择 → 执行 | 高危操作需审批 |

### 流式输出机制

```
Agent LLM (astream_events)
    │
    ▼ LangGraph 事件流
    │   on_chat_model_stream → token 事件
    │   on_tool_start → tool_start 事件
    │   on_tool_end → tool_end 事件
    │
    ▼ FastAPI EventSourceResponse (SSE)
    │   data: {"type": "token", "content": "..."}
    │   data: {"type": "tool_start", "tool": "...", "args": {...}}
    │   data: {"type": "tool_end", "tool": "...", "result": "..."}
    │   data: {"type": "done", "thread_id": "..."}
    │
    ▼ 前端 @microsoft/fetch-event-source
    │   实时解析 SSE 事件
    │
    ▼ Pinia Store (chat.ts)
    │   blocks[] 数组按序追加
    │   text block + tool_call block 交错排列
    │   ensureTextBlock() 只看最后一个 block
    │
    ▼ MessageBubble.vue
        v-for 遍历 blocks，text→MarkdownRenderer，tool_call→ToolCallBlock
```

**关键修复历史：**
- 最初用 `v-if/v-else-if` 链 → tool_call 不渲染 → 改为独立 `v-if`
- `ensureTextBlock()` 向后搜索所有 block → token 追加到旧的 text block，打乱顺序 → 改为只看最后一个 block
- DeepSeek API 断连 → 不完整 AIMessage(tool_calls) 残留在 checkpointer → 后续请求 400 错误 → 添加异常清理逻辑

---

## 四、本体操作引擎

### 设计理念：Palantir Foundry 风格

```
ontology_actions.yaml 定义 Action
    │
    ▼ OntologyEngine.execute()
    │
    ├─ ActionResolver: entity_id → entity_type (查询 Neo4j label)
    ├─ 查找 ActionDef: entity_type → Action 列表
    ├─ FunctionSelector: 7 条优先级规则 → 选择具体函数
    ├─ ApprovalManager: 高危操作需要审批 (pending → approved → executed)
    └─ AuditLogger: 所有执行记录（内存审计日志）
```

**当前定义的 Action：**

| 实体类型 | Action | 审批 | 实现函数 |
|----------|--------|------|----------|
| code_entity | refactor | 不需要 | split_large_function, extract_interface, reduce_complexity |
| code_entity | document | 不需要 | generate_api_doc, annotate_complex_logic |
| code_entity | analyze_impact | 不需要 | trace_call_chain, find_dependent_modules |
| code_entity | delete | **需要审批** | find_dependent_modules |
| alert_entity | diagnose | 不需要 | analyze_by_log_pattern, analyze_by_call_chain |
| alert_entity | rollback | **需要审批** | find_last_stable |
| alert_entity | notify | 不需要 | create_ticket |

**FunctionSelector 的规则引擎（7 条优先级规则）：**

1. 显式指定 `function_name` → 直接使用
2. `doc_type: "api"` → 选择 API 文档生成
3. `doc_type: "comment"` → 选择注释生成
4. 有 `trace_depth` → 选择链路追踪
5. 有 `method_list` → 选择模块依赖分析
6. reason 包含 "lines"/"large" → 选择拆分函数
7. 代码行数 > 100 → 选择复杂度降低

这种设计允许同一 Action 在不同上下文中选择不同的执行策略。

---

## 五、影响传播引擎

### 算法：双向 BFS + 关系类型权重 × 变更类型权重 × 深度衰减

**权重矩阵（关系 × 变更类型）：**

| | ADDED | DELETED | SIGNATURE | BODY | DOC_ONLY |
|---|---|---|---|---|---|
| calls | 0.7 | **1.0** | 0.8 | 0.5 | 0.0 |
| implements | 0.6 | 0.9 | 0.7 | 0.4 | 0.0 |
| extends | 0.6 | 0.9 | 0.7 | 0.4 | 0.0 |
| imports | 0.3 | 0.5 | 0.0 | 0.0 | 0.0 |
| semantic_impact | 0.5 | 0.7 | 0.5 | 0.3 | 0.0 |
| describes | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| affects | 0.8 | **1.0** | 0.9 | 0.6 | 0.0 |

**衰减策略：** Depth 1=1.0, Depth 2=0.6, Depth 3=0.3, Depth 4+=0（停止传播）

**传播公式：**
```
impact_score = weight(relation_type, change_type) × decay(depth)
severity = CRITICAL(≥0.8) / HIGH(≥0.5) / MEDIUM(≥0.2) / LOW(<0.2)
```

**变更类型分类（`change_detector.py`）：**
通过分析 git diff hunks 自动分类：
- **SIGNATURE** — `def`/`class`/`async def` 行被修改
- **BODY** — 其他代码行被修改
- **DOC_ONLY** — 只有注释/文档字符串被修改
- **ADDED/DELETED** — 文件级别

这是整个系统最精细的部分之一 — 它不是简单的"改了哪些文件"，而是"改了什么类型的内容，对其他代码的影响权重是多少"。

---

## 六、事件驱动引擎（Butler）

### 架构：Async Pub/Sub + 反思学习

```
GitWatcher (轮询 git rev-parse HEAD)
    │  检测到变更
    ▼
EventBus.publish(code.changed)
    │
    ├─→ KnowledgeUpdateHandler → IncrementalUpdater.update()
    │    (增量更新知识图谱)
    │
    ├─→ ReflectionHandler (监听 handler.completed)
    │    ├─ 生成事件签名: "code.changed:.py"
    │    ├─ 搜索已有 Skill (SQLite)
    │    ├─ 匹配 → increment_hit_count
    │    └─ 未匹配 → 创建 candidate Skill (confidence=0.5)
    │         hit_count 累积 → confidence 递增 0.1
    │         confidence ≥ 0.8 → 状态提升为 "active"
    │
    └─→ 级联保护：不递归超过一层
```

**三层 Skill 体系：**

| 层级 | 含义 | 来源 |
|------|------|------|
| RULE | 预定义规则 | 手动编写 |
| META | 模式发现 | 反思学习自动提升 |
| HARNESS | 自动化流水线 | 多步骤编排 |

**反思学习的工作原理：**
每次 handler 执行完成后，ReflectionHandler 会：
1. 生成事件签名（`event_type:file_extension`）
2. 在 SQLite 中搜索匹配的 Skill
3. 命中 → 增加计数和置信度（+0.1）
4. 未命中 → 创建候选 Skill（confidence=0.5）
5. 当 confidence ≥ 0.8 且 hit_count 足够多时 → 自动提升为 active

这意味着系统会**自动学习**哪些事件模式是常见的，并逐步提高对常见模式的响应优先级。

---

## 七、数据模型设计

### 本体 Schema：9 实体 + 15 关系

```
                    ┌──────────────┐
          ┌────────▶│ ConceptEntity│◀─derived_from──┐
          │         │ (概念/模式)   │                │
          │         └──────────────┘                │
     semantic_impact                                │
          │                                         │
┌─────────┴──────┐    CONTAINS    ┌──────────────┐ │
│  CodeEntity    │◀───────────────│ ModuleEntity │ │
│ (函数/类/接口)  │                │ (聚类模块)    │ │
└───┬───┬───┬────┘                └──────────────┘ │
    │   │   │  CALLS/IMPORTS/                      │
    │   │   │  EXTENDS/IMPLEMENTS                   │
    │   │   ▼                                      │
    │   │ ┌──────────────┐                         │
    │   └─│  DocEntity   │◀──describes─────────────┘
    │     │ (文档)        │    (Stage 2.5 启发式)
    │     └──────────────┘
    │
    │  ┌──────────────┐    ┌──────────────┐
    └──▶│ChangeSetEntity│    │ ResourceEntity│
       │ (变更集)       │    │ (资源文件)     │
       └──────────────┘    └──────────────┘

       ┌──────────────┐    ┌──────────────┐
       │  LogEntity   │    │ AlertEntity  │
       │ (日志条目)     │    │ (监控告警)     │
       └──────────────┘    └──────────────┘

       ┌──────────────┐
       │ServiceEntity │
       │ (运行时服务)   │
       └──────────────┘
```

### 关系约束系统

`RELATION_CONSTRAINTS` 定义了每对关系的 domain/range 约束：
- `calls` 只能是 CodeEntity → CodeEntity
- `contains` 可以是 (CodeEntity|ModuleEntity) → (CodeEntity|DocEntity|ResourceEntity)
- `semantic_impact` 可以是 (CodeEntity|ConceptEntity) → (CodeEntity|ConceptEntity)

`validate_relation_constraint()` 在每次 `merge_relation()` 时检查，违反约束抛出 `ConstraintViolationError`。

### Provenance 系统

每条数据和关系都附带来源追踪：

```python
{
    "provenance_source": "ast_parser",     # ast_parser / llm_extraction / clustering / imported / manual
    "confidence": 1.0,                      # 0.0-1.0，AST=1.0，LLM=0.5-0.95，聚类=0.8
    "extracted_at": "2026-06-09T12:00:00Z"  # ISO 8601
}
```

这允许下游消费者（Agent、影响分析）根据数据来源调整信任度。

---

## 八、四层 API 接口

系统提供 4 种访问方式，共享同一套核心逻辑：

| 接口 | 框架 | 用途 | 入口 |
|------|------|------|------|
| CLI | Click | 开发者命令行 | `layerkg build/query/ask/web/serve` |
| Web REST | FastAPI + uvicorn | 前端页面 | `POST /api/chat/stream`, `GET /api/graph` |
| MCP Server | FastMCP | IDE/工具集成 | `layerkg serve` (stdio/http) |
| Agent | LangGraph | AI 对话式查询 | `layerkg ask` 或 Web Chat |

**MCP Server 的 8 个工具**（与 Agent 工具几乎一一对应）：
`semantic_search`, `graph_query`, `impact_analysis`, `get_context`, `list_concepts`, `get_module_tree`, `detect_changes`, `export_graph`

这种设计意味着知识图谱的能力可以通过多种渠道访问 — CLI 用于脚本化，Web 用于可视化，MCP 用于 IDE 集成，Agent 用于自然语言交互。

---

## 九、前端架构

### 技术栈

Vue 3 + TypeScript + Pinia + Vue Router + `@microsoft/fetch-event-source`

### 路由与视图

| 路由 | 组件 | 功能 |
|------|------|------|
| `/` | ChatView | Agent 对话（SSE 流式） |
| `/graph` | GraphView | 知识图谱可视化（D3.js 力导向图） |
| `/traces` | TracesView | 执行追踪列表 |
| `/traces/:id` | TraceDetailView | 单次追踪详情（Mermaid 图） |

### 核心数据流

```
ChatInput.vue
    │ sendMessage(content)
    ▼
chat.ts (Pinia Store)
    │ sendChatStream() → SSE 连接
    │ addUserMessage() + addAssistantMessage()
    │
    │ SSE 事件处理：
    │   token → ensureTextBlock().block.content += content
    │   tool_start → blocks.push({type:'tool_call', toolCall})
    │   tool_end → 更新 toolCall.status
    │   done → threadId = event.thread_id
    │
    ▼
MessageBubble.vue
    │ v-for block in message.blocks
    │   text → MarkdownRenderer
    │   tool_call → ToolCallBlock
    │
    ▼
MarkdownRenderer.vue — marked + highlight.js 代码高亮
ToolCallBlock.vue — 工具名/参数/结果折叠展示
```

### SSE 容错

- `JSON.parse` 包裹 try/catch（DeepSeek API 有时返回不完整 JSON）
- 连接错误时在最后一条消息中追加错误提示
- 断连后 Agent 清理不完整的 AIMessage(tool_calls) 防止后续 400 错误

---

## 十、Schema 迁移系统

**设计：** 类似 Django/alembic 的版本化迁移

```
MigrationBase (抽象基类)
    │ version_from, version_to, description
    │ upgrade(store) — 必须幂等
    │ downgrade(store)

MigrationRegistry
    │ 按版本排序的迁移列表
    │ get_migration_path(from, to) — 计算迁移链

MigrationRunner
    │ fcntl.flock 文件锁 → 防止并发迁移
    │ run_pending() — 检查 SchemaVersion → 逐个执行
    │ rollback() — 反向执行
```

**并发安全：** `~/.layerkg/migrate.lock` 文件锁确保只有一个进程在执行迁移。

---

## 十一、错误处理与容错体系

### 分级容错策略

| 级别 | 适用阶段 | 行为 |
|------|----------|------|
| 致命 | Stage 2 (结构写入) | `RuntimeError` 中止构建 |
| 可降级 | Stage 3/4/5 (语义/聚类/向量) | 异常捕获，跳过阶段，继续执行 |
| 可重试 | Neo4j 批量写入、LLM 调用 | tenacity/指数退避重试 3 次 |
| 回退 | 向量嵌入 | 批量失败 → 逐条重试 → 零向量兜底 |
| 静默 | Stage 1 文件解析 | 语法错误/IO 错误跳过单个文件 |

### Agent 层的容错

- LLM 超时 180s + 3 次重试
- 异常时清理 checkpointer 中的不完整消息序列
- 友好的中文错误提示（连接错误/发送失败）
- TraceCollector 记录完整的执行过程

---

## 十二、异常层级

```
LayerKGError (基类)
  ├── SchemaValidationError    — Schema 校验失败（字段类型/值范围）
  ├── ConstraintViolationError — 本体约束违反（domain/range）
  ├── StoreError               — 存储操作失败
  ├── EmbeddingError           — 向量嵌入失败
  ├── ExtractionError          — 语义提取失败
  └── SchemaMigrationError     — Schema 迁移失败（含已执行列表）
```

---

## 十三、配置管理

**零依赖 .env 加载器** — 不依赖 `python-dotenv`，自实现 `_load_dotenv()`：
- 查找项目根目录的 `.env` 文件
- 跳过注释和空行
- 不覆盖已存在的环境变量（`key not in os.environ`）

**19 个环境变量**，按功能分组：

| 组 | 变量 | 默认值 |
|----|------|--------|
| Neo4j | URI/USER/PASSWORD | bolt://localhost:7687 / neo4j / "" |
| Ollama | URL/MODEL/EMBEDDING_MODEL | localhost:11434 / qwen3.5:9b / qwen2.5-coder:0.5b |
| 构建语义 | LLM_PROVIDER/API_KEY/BASE_URL | ollama / "" / openai.com |
| Agent | LLM_PROVIDER/MODEL/API_KEY/BASE_URL | zhipu / glm-4-flash / "" / bigmodel.cn |
| 构建 | INCLUDE_DOCS/DOC_MAX_LENGTH/SOURCE_MAX_LENGTH | true/800/800 |

---

## 十四、技术栈全景

| 层 | 技术 | 版本 | 用途 |
|----|------|------|------|
| 语言 | Python | 3.13+ | 全部后端 |
| 构建 | hatchling + uv | latest | 包管理和构建 |
| AST | tree-sitter | 0.24+ | Python/Java 语法解析 |
| 图数据库 | Neo4j | 5.x | 结构+语义图存储 |
| 向量库 | ChromaDB | 1.0+ | HNSW 余弦相似度 |
| 嵌入 | Ollama | — | all-minilm-l6-v2 / qwen2.5-coder:0.5b |
| 语义 LLM | Ollama / OpenAI | — | qwen3.5:9b 概念提取 |
| Agent LLM | 智谱 GLM | — | glm-4-flash 推理+工具调用 |
| Agent 框架 | LangGraph | 0.2.x | ReAct 循环 + 状态管理 |
| CLI | Click | 8.1+ | 命令行接口 |
| Web | FastAPI + uvicorn | 0.136+ | REST API + SSE |
| MCP | FastMCP | 3.2+ | IDE 工具集成 |
| 前端 | Vue 3 + Pinia | — | SPA 界面 |
| SSE | @microsoft/fetch-event-source | — | 前端 SSE 客户端 |
| 重试 | tenacity | 9.1+ | Neo4j 批量写入重试 |
| 审计 | SQLite (WAL) | — | Butler 操作日志 + Skill 存储 |
| 格式化 | ruff | latest | 代码格式化+检查 |
| 类型检查 | pyright | latest | 静态类型检查 |
| 测试 | pytest + pytest-asyncio | latest | 单元/集成/E2E 测试 |

---

## 十五、深度反思

### 做得好的设计决策

1. **可降级管线** — 结构阶段（AST+Neo4j）是核心，不可降级；语义阶段（LLM+聚类+向量）是增强，可跳过。这保证了即使 Ollama 宕机也能构建基础图谱

2. **Provenance 系统** — 每条数据的来源和置信度可追溯。这为下游消费（Agent 推理、影响分析权重）提供了信任度基础，是"可解释的知识图谱"的关键

3. **概念对齐的 4 层策略** — 从确定性到概率性逐层递进，兼顾了精确性和召回率。第 4 层（图结构 Jaccard）利用了图谱本身的结构信息

4. **双 LLM 策略** — 构建时用本地大模型（qwen3.5:9b）做批量语义提取，运行时用云端模型（GLM-4-flash）做实时推理。各自优化，互不影响

5. **虚拟文件内边** — 用物理位置（同文件）推断逻辑关联（同模块），解决了代码图谱稀疏性这一核心问题

6. **Palantir 风格本体操作** — Action → Function 的间接映射，加上规则引擎选择执行策略，实现了"同一意图在不同上下文中选择不同策略"的灵活性

7. **Butler 反思学习** — 自动从重复事件中学习模式，逐步提升响应置信度。这是从"被动工具"到"主动助手"的关键一步

### 核心问题与改进方向

1. **无增量构建 — 最大的架构缺陷**
   - 每次全量重建，大仓库（10K+ 文件）耗时严重
   - tree-sitter 本身支持增量解析，但完全未利用
   - Neo4j 的 MERGE 语义上支持幂等更新，但缺少"只更新变化部分"的路径
   - **改进**：文件内容哈希 → 变更检测 → 只重新解析变化的文件 → 差量写入 Neo4j

2. **嵌入模型是语义搜索的天花板**
   - 256 token 上下文 = 一个函数签名 + 几行代码，无法表示完整语义
   - 零向量兜底会污染搜索结果
   - 切换模型需要重建整个 ChromaDB（维度不兼容）
   - **改进**：引入模型版本号 → 自动检测维度变化 → 触发重建；支持长上下文模型（bge-m3）

3. **LLM 幻觉没有闭环验证**
   - Stage 3 的语义关系完全由 LLM 生成，缺少质量评估
   - `confidence >= 0.5` 的阈值形同虚设（LLM 总是给出高置信度）
   - 没有人工反馈回路来纠正错误关系
   - **改进**：引入验证层 — 抽样 LLM 输出 → 人工审核 → 反馈修正 prompt

4. **Agent Cypher 注入风险**
   - `graph_query` 工具直接执行 LLM 生成的 Cypher
   - 虽然有 `LIMIT` 建议和 100 行截断，但没有实质性的安全检查
   - LLM 可能生成 `MATCH (n) DETACH DELETE n` 等破坏性操作
   - **改进**：Cypher 白名单/只读模式；或改为结构化查询接口

5. **单线程全流程**
   - 5 个阶段串行执行
   - Stage 1 的文件扫描可以多线程并行
   - Stage 3 的 LLM 批次之间可以流水线化
   - **改进**：`concurrent.futures.ThreadPoolExecutor` 或 `asyncio` 并行化

6. **模块聚类每次重新计算**
   - `get_module_tree()` 每次调用都从 Neo4j 加载全图 → 跑 Label Propagation
   - 没有缓存，没有增量更新
   - **改进**：聚类结果持久化到 Neo4j；变更时只更新受影响的社区

7. **缺少图谱质量评估体系**
   - 没有量化指标：完整性（该有的实体/关系有没有）、准确性（关系是否正确）、一致性（约束是否满足）
   - **改进**：定义指标（节点覆盖率、关系召回率、约束违反率），build 完成后自动评估

8. **前端状态管理的复杂性**
   - SSE 事件 → blocks 数组的操作涉及 `ensureTextBlock`、`tool_start`/`tool_end` 状态同步
   - 三次迭代才解决 block 排序问题，说明这个数据结构设计不够直观
   - **改进**：考虑用状态机（状态 → 事件 → 新状态）替代命令式的 block 操作

---

## 十六、关键文件索引

### 后端

| 文件 | 路径 | 行数 | 职责 |
|------|------|------|------|
| Builder | `src/layerkg/builder.py` | 1269 | 5 阶段构建管线主编排 |
| Config | `src/layerkg/config.py` | 151 | 配置管理 + 零依赖 .env |
| Schema | `src/layerkg/schema.py` | 534 | 9 实体 + 15 关系 + 约束 |
| Neo4j Store | `src/layerkg/neo4j_store.py` | 528 | 图数据库批量读写 |
| Chroma Store | `src/layerkg/chroma_store.py` | 413 | 向量存储 + Ollama 嵌入 |
| Python Parser | `src/layerkg/parser/python_parser.py` | 709 | tree-sitter Python 解析 |
| Java Parser | `src/layerkg/parser/java_parser.py` | 1098 | tree-sitter Java 解析 |
| Doc Parser | `src/layerkg/parser/doc_parser.py` | 194 | Markdown/RST 解析 |
| Relation Extractor | `src/layerkg/extractor/relation.py` | 161 | 名称→ID 关系解析 |
| Semantic Extractor | `src/layerkg/extractor/semantic.py` | 520 | LLM 概念/关系提取 |
| Module Clustering | `src/layerkg/module_clustering.py` | 435 | Label Propagation 社区检测 |
| Concept Aligner | `src/layerkg/aligner.py` | 371 | 4 层概念对齐 |
| Ontology Engine | `src/layerkg/ontology_engine.py` | — | Palantir 风格 Action 引擎 |
| Impact Propagator | `src/layerkg/impact_propagator.py` | — | 双向 BFS + 权重衰减 |
| Butler Engine | `src/layerkg/butler/engine.py` | — | 事件驱动知识管理 |
| Butler Handlers | `src/layerkg/butler/handlers/` | — | 事件处理器（更新/反思） |
| Migration | `src/layerkg/migrations/` | — | Schema 版本迁移 |
| Agent Graph | `src/layerkg/agent/graph.py` | — | LangGraph ReAct Agent |
| Agent Tools | `src/layerkg/agent/tools.py` | — | 9 个 Agent 工具 |
| Agent Prompt | `src/layerkg/agent/prompt.py` | — | 系统提示词 + 本体 schema |
| Agent Trace | `src/layerkg/agent/trace.py` | — | SQLite 执行追踪 |
| Agent Helpers | `src/layerkg/agent/_helpers.py` | — | 懒加载单例工厂 |
| Web App | `src/layerkg/web/app.py` | — | FastAPI 应用工厂 |
| Chat Router | `src/layerkg/web/router/chat.py` | — | SSE 流式聊天 |
| Graph Router | `src/layerkg/web/router/graph.py` | — | 图数据 REST API |
| Trace Router | `src/layerkg/web/router/trace.py` | — | 追踪数据 API |
| MCP Server | `src/layerkg/mcp_server.py` | — | FastMCP 工具服务器 |
| CLI | `src/layerkg/cli.py` | 400 | Click 命令行 |
| Provenance | `src/layerkg/provenance.py` | 68 | 来源追踪 + 置信度 |
| Exceptions | `src/layerkg/exceptions.py` | 30 | 6 类异常层级 |
| Actions YAML | `src/layerkg/ontology_actions.yaml` | — | Action 定义 |

### 前端

| 文件 | 路径 | 职责 |
|------|------|------|
| App | `frontend/src/App.vue` | 导航栏 + 路由出口 |
| Router | `frontend/src/router/index.ts` | 4 条路由 |
| Chat Store | `frontend/src/stores/chat.ts` | Pinia 聊天状态 + SSE |
| Graph Store | `frontend/src/stores/graph.ts` | 图谱状态 |
| Chat API | `frontend/src/api/chat.ts` | SSE + REST 客户端 |
| Graph API | `frontend/src/api/graph.ts` | 图谱 REST API |
| Trace API | `frontend/src/api/trace.ts` | 追踪 REST API |
| Types | `frontend/src/api/types.ts` | TypeScript 类型定义 |
| MessageBubble | `frontend/src/components/MessageBubble.vue` | 消息渲染（blocks 按序） |
| ToolCallBlock | `frontend/src/components/ToolCallBlock.vue` | 工具调用展示 |
| MarkdownRenderer | `frontend/src/components/MarkdownRenderer.vue` | Markdown 渲染 + 代码高亮 |
| ChatInput | `frontend/src/components/ChatInput.vue` | 输入框 |
| NodeDetail | `frontend/src/components/NodeDetail.vue` | 节点详情 |
