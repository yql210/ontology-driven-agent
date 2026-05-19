---
title: LayerKG 源码解读手册
date: 2026-05-17T16:41:08+08:00
lastmod: 2026-05-17T16:44:17+08:00
---

# LayerKG 源码解读手册

# LayerKG 源码解读手册

## 1. 项目总览

### 1.1 技术栈

|层级|技术|版本|
| ----------| -------------------------------| --------|
|语言|Python|3.13+|
|包管理|uv + hatchling|0.11+|
|AST 解析|tree-sitter|latest|
|图数据库|Neo4j|5.x|
|向量库|ChromaDB|1.0+|
|嵌入|Ollama (qwen2.5-coder:0.5b)|—|
|Agent|LangGraph + LangChain|0.2.x|
|Web|FastAPI + Uvicorn|0.136+|
|前端|Vue3 + Cytoscape.js + Mermaid|—|
|MCP|FastMCP|3.2+|
|CLI|Click|8.1+|
|代码质量|ruff + pyright|—|

### 1.2 项目结构

```
src/layerkg/
├── schema.py              # 数据模型：6实体 + 11关系 (277行)
├── config.py              # 配置管理，从 .env 加载 (128行)
├── exceptions.py          # 自定义异常层次 (21行)
├── graph_store.py         # 图存储抽象接口 (122行)
├── neo4j_store.py         # Neo4j 实现 (346行)
├── chroma_store.py        # ChromaDB 向量存储 (338行)
├── builder.py             # 5阶段构建流水线 (1172行)
├── change_detector.py     # Git变更检测+SHA256缓存 (443行)
├── impact_propagator.py   # 双向BFS影响传播 (436行)
├── incremental_updater.py # 4阶段增量更新 (670行)
├── aligner.py             # 概念对齐器 (370行)
├── module_clustering.py   # 社区检测模块聚类 (424行)
├── cli.py                 # Click CLI入口 (369行)
├── mcp_server.py          # FastMCP MCP服务器 (360行)
│
├── parser/                # 代码解析层
│   ├── base.py            # 解析器抽象基类 (82行)
│   ├── python_parser.py   # Python tree-sitter解析器 (708行)
│   ├── java_parser.py     # Java tree-sitter解析器 (1068行)
│   └── doc_parser.py      # Markdown/RST文档解析器 (193行)
│
├── extractor/             # 关系提取层
│   ├── relation.py        # 名称→UUID关系解析器 (160行)
│   └── semantic.py        # LLM语义关系提取器 (475行)
│
├── agent/                 # Agent 层
│   ├── graph.py           # LangGraph ReAct 状态图 (227行)
│   ├── tools.py           # 8个 LangChain Tool (326行)
│   ├── prompt.py          # Agent 系统提示词 (—)
│   ├── _helpers.py        # 懒加载单例工厂 (—)
│   └── trace.py           # Trace 可观测性 (290行)
│
├── web/                   # Web API 层
│   ├── app.py             # FastAPI 应用工厂 (—)
│   └── router/
│       ├── chat.py        # 对话 API（同步+SSE流式）(—)
│       ├── graph.py       # 图可视化 API (161行)
│       └── trace.py       # Trace 查看 API (—)
│
└── butler/                # Butler 引擎层
    ├── engine.py          # 事件驱动主引擎 (329行)
    ├── event_bus.py       # 异步事件总线 (—)
    ├── scheduler.py       # Handler调度器 (—)
    ├── consistency/
    │   └── guard.py       # 审计日志（SQLite） (202行)
    ├── skills/
    │   └── store.py       # 技能模式存储 (470行)
    ├── handlers/
    │   ├── base.py        # Handler抽象基类 (—)
    │   ├── knowledge_update.py  # 增量/全量构建Handler (—)
    │   └── reflection.py  # 反思归纳Handler (—)
    └── watchers/
        └── git_watcher.py # Git仓库轮询监控 (171行)
```

### 1.3 架构分层

```
┌─────────────────────────────────────────────┐
│              用户接入层                        │
│  CLI (Click)  │  Web (FastAPI)  │  MCP Server │
├─────────────────────────────────────────────┤
│              智能层                           │
│  Agent (LangGraph ReAct)  │  Butler Engine   │
│  8 Tools + Trace          │  EventBus+Handler │
├─────────────────────────────────────────────┤
│              业务逻辑层                        │
│  Builder (5阶段流水线)  │  IncrementalUpdater  │
│  ImpactPropagator      │  ConceptAligner      │
│  ModuleClustering      │  ChangeDetector      │
├─────────────────────────────────────────────┤
│              解析层                           │
│  PythonParser  │  JavaParser  │  DocParser   │
│  RelationExtractor  │  SemanticExtractor     │
├─────────────────────────────────────────────┤
│              存储层                           │
│  Neo4jGraphStore  │  ChromaStore (Ollama)    │
├─────────────────────────────────────────────┤
│              数据模型                         │
│  schema.py (6实体 + 11关系)  │  config.py     │
│  exceptions.py (4异常类)                      │
└─────────────────────────────────────────────┘
```

## 2. 数据模型层

### 2.1 schema.py — 核心数据模型

#### 实体类继承关系

所有实体使用 `@dataclass` 定义，无继承层次（平铺结构）。

#### CodeEntity

```python
@dataclass
class CodeEntity:
    name: str                          # 必填，实体名称
    entity_type: str                   # 必填，见下表
    id: str = field(default_factory=...)  # UUID
    file_path: str = ""                # 源文件路径
    start_line: int = 0                # 起始行号
    end_line: int = 0                  # 结束行号
    source: str = ""                   # 源码片段（截断至500字符）
    language: str = ""                 # python / java
    docstring: str = ""                # 文档字符串（截断至500字符）
    parameters: str = ""               # 参数列表（JSON字符串）
    created_at: str = ""               # ISO 8601 时间戳
```

**entity_type 可选值**: `function`​, `class`​, `interface`​, `module`​, `file`​, `enum`​, `record`​, `field`

**验证规则**: `__post_init__`​ 中检查 name 非空、entity_type 在允许集合内，否则抛 `SchemaValidationError`。

#### ConceptEntity

```python
@dataclass
class ConceptEntity:
    name: str                          # 必填
    entity_type: str                   # 必填
    # entity_type: business_concept, design_pattern, api_contract, data_model, process
    description: str = ""
    aliases: list[str] = field(default_factory=list)
```

#### DocEntity

```python
@dataclass
class DocEntity:
    name: str
    entity_type: str
    # entity_type: readme, module_doc, api_doc, comment, wiki, architecture_doc
    content: str = ""                  # 文档内容（截断至 build_doc_max_length）
    file_path: str = ""
    language: str = ""
```

#### ModuleEntity

```python
@dataclass
class ModuleEntity:
    name: str                          # 无 entity_type 字段
    description: str = ""
    id: str = field(default_factory=...)
```

#### ChangeSetEntity

```python
@dataclass
class ChangeSetEntity:
    commit_hash: str                   # 必填
    message: str                       # 必填
    author: str = "unknown"
    branch: str = "main"
    files_changed: list[str] = field(default_factory=list)
```

#### Relation

```python
@dataclass
class Relation:
    source_id: str                     # 必填，源实体 UUID
    target_id: str                     # 必填，目标实体 UUID
    relation_type: str                 # 必填，必须在 VALID_RELATION_TYPES 中
    weight: float = 1.0                # [0, 1] 范围
    metadata: dict = field(default_factory=dict)
```

**VALID_RELATION_TYPES** (11种):

- 结构关系: `calls`​, `extends`​, `implements`​, `imports`​, `contains`
- 语义关系: `semantic_impact`​, `describes`​, `illustrates`​, `derived_from`
- 变更关系: `changed_in`​, `affects`

#### Neo4j 名称映射

​`RELATION_TYPE_TO_NEO4J` 将 snake_case 映射为 UPPER_SNAKE:

- ​`calls`​ → `CALLS`​, `extends`​ → `EXTENDS`​, `semantic_impact`​ → `SEMANTIC_IMPACT` 等

### 2.2 config.py — 配置管理

```python
@dataclass
class LayerKGConfig:
    # 通过 LayerKGConfig.from_env() 从环境变量加载
    # 支持自定义 .env 文件路径
    # 环境变量前缀: LAYERKG_
```

关键设计:

- 内置 `_load_dotenv()` 实现（无 python-dotenv 依赖）
- 环境变量不覆盖已存在的（`if key not in os.environ`）
- ​`build_skip_dirs` 用逗号分隔的字符串解析

### 2.3 exceptions.py — 异常体系

```
LayerKGError (基类)
├── SchemaValidationError    # 实体字段验证失败
├── StoreError               # 图/向量存储操作失败
├── EmbeddingError           # Ollama 嵌入生成失败
└── ExtractionError          # 语义关系提取失败
```

## 3. 存储层

### 3.1 graph_store.py — 图存储抽象

```python
class GraphStore(ABC):
    @abstractmethod merge_node(label, properties) -> dict
    @abstractmethod get_node(node_id) -> dict | None
    @abstractmethod delete_node(node_id) -> bool
    @abstractmethod merge_relation(source_id, target_id, rel_type, properties=None,
                                    *, source_label=None, target_label=None) -> dict
    @abstractmethod delete_relation(source_id, target_id, rel_type) -> bool
    @abstractmethod get_relations(source_id=None, target_id=None, rel_type=None) -> list[dict]
    @abstractmethod query(cypher, params=None) -> list[dict]
    @abstractmethod cleanup_orphan_nodes() -> int
```

**设计要点**: `merge_relation`​ 的 `source_label`​/`target_label` 为 keyword-only 参数，帮助 Neo4j 利用索引加速 MERGE。

### 3.2 neo4j_store.py — Neo4j 实现

**连接管理**: 使用 `GraphDatabase.driver` 创建驱动，支持上下文管理器自动关闭。

**Cypher 注入防护**: `merge_node`​ 验证 label 匹配 `^[A-Za-z_]\w*$`​，`merge_relation`​ 验证关系类型匹配 `^[A-Z_]+$`。

**MERGE 策略**: `merge_relation` 将 MATCH 拆分为两个独立子句（分别 MATCH 源节点和目标节点），避免 UNIQUE 约束冲突。

**约束初始化**: `ensure_constraints()`​ 为所有 6 种实体标签创建 `id` 字段的 UNIQUE 约束:

```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (n:CodeEntity) REQUIRE n.id IS UNIQUE
```

**关键方法实现细节**:

- ​`delete_node`​: 使用 `DETACH DELETE` 同时删除节点和所有关系
- ​`get_node`​: `MATCH (n {id: $id})` 跨标签搜索
- ​`cleanup_orphan_nodes`​: `MATCH (n) WHERE labels(n) = [] DETACH DELETE n`

### 3.3 chroma_store.py — ChromaDB 向量存储

**OllamaEmbeddingFunction**: 实现 ChromaDB 的 `EmbeddingFunction` 协议:

- 通过 `POST /api/embed` 批量生成嵌入
- ​`dimension` 属性自动检测并缓存维度
- 30 秒 HTTP 超时

**ChromaStore**:

- ​`persist_dir=None` → 内存模式（测试用）
- ​`persist_dir="/path"` → 持久化模式
- 集合创建使用 `hnsw:space: "cosine"` 距离度量
- ​`put_entities_batch` 自动分批（batch_size=50），跳过空文本
- ​`_sanitize_metadata`​ 过滤只保留 `str|int|float|bool` 类型

## 4. 解析层

### 4.1 parser/base.py — 解析器抽象

```python
@dataclass
class ExtractedRelation:
    source_name: str        # 源实体名称（非UUID）
    source_type: str        # 源 entity_type
    target_name: str        # 目标实体名称
    target_type: str        # 目标 entity_type
    relation_type: str      # 关系类型
    file_path: str          # 所在文件

@dataclass
class ParseResult:
    file_path: str
    entities: list[CodeEntity]
    relations: list[ExtractedRelation]
    language: str = "python"
    error: str | None = None

class BaseParser(ABC):
    @abstractmethod parse_file(file_path: Path) -> ParseResult
    @abstractmethod parse_source(source: bytes, file_path: str) -> ParseResult
    @property @abstractmethod language -> str
```

**设计要点**: 使用名称引用（非UUID）作为中间表示，后续由 `RelationExtractor` 统一解析为UUID关系。这避免了解析器需要了解全局实体索引。

### 4.2 parser/python_parser.py — Python 解析器

**核心技术**: tree-sitter Python 语法解析

**解析流程**:

1. 读取文件 → tree-sitter 解析为 AST
2. 创建 `module` 类型 CodeEntity（source 截断至500字符）
3. 递归 `_walk` 遍历 AST:

   - ​`function_definition`​ → `_extract_function`
   - ​`class_definition`​ → `_extract_class`
   - ​`import_statement`​ / `import_from_statement`​ → `_extract_import`
4. 函数体中 BFS 查找 `call`​ 节点 → `_extract_calls`

**命名规则**: 方法名前缀类名 → `ClassName.method_name`

**调用过滤**: `_BUILTIN_NAMES`（~100个内置函数/异常）和长度 < 3 的名称被过滤

**参数提取**: 支持 typed parameter、default parameter、`*args`​、`**kwargs`，输出 JSON 字符串

**错误处理**: 语法错误时返回已解析部分，不中断构建

### 4.3 parser/java_parser.py — Java 解析器

**核心技术**: tree-sitter Java 语法解析

**两遍扫描**:

1. ​`_extract_package_first_pass`​: 提取 `package` 声明
2. ​`_walk`: 递归提取实体

**支持的 Java 结构**:

|结构|entity_type|提取的关系|
| -----------------| -------------| ------------------------------------|
|class|​`class`|contains, extends, implements|
|interface|​`interface`|contains, extends (多继承)|
|enum|​`enum`|contains, implements + 常量作为 `field`|
|record|​`record`|contains, implements|
|annotation_type|—|跳过|
|method|​`function`|contains, calls|
|constructor|​`function`|contains, calls（命名 `<init>`）|
|field|​`field`|contains|

**Javadoc 提取**: `_get_javadoc`​ 检查前一个兄弟节点是否为 `/** ... */` 注释

**JDK 类型过滤**: `_JDK_COMMON_TYPES`（~90个常见 JDK 类型/方法）从 extends、implements、imports、calls 中过滤

### 4.4 parser/doc_parser.py — 文档解析器

**注意**: `DocParser`​ **不继承** `BaseParser`​（返回类型不同: `DocParseResult`​ vs `ParseResult`）

**支持格式**: `.md`​（Markdown）和 `.rst`（reStructuredText）

**解析策略**:

- **Markdown**: 按标题（`#`​/`##`​/`###`）分割，每个段落 → 一个 DocEntity
- **RST**: 按下划线风格标题分割（`===`​, `---`​, `~~~` 等）
- 无标题 → 整个文件作为一个 DocEntity

**文档类型检测** (`_detect_doc_type`):

- 文件名含 `README`​ → `readme`
- 文件名含 `API`​/`api`​ → `api_doc`
- 路径含 `docs/architecture`​ → `architecture_doc`
- 路径含 `docs/`​ → `module_doc`
- 其他 → `comment`

**限制**: 文件大小 > 5MB 跳过，内容截断至 `build_doc_max_length`（默认2000字符）

## 5. 关系提取层

### 5.1 extractor/relation.py — 名称解析

```python
class RelationExtractor:
    def add_parse_result(entities, relations)  # 累积一个文件的解析结果
    def resolve(all_entities) -> list[Relation]  # 转换为 UUID 关系
    def resolve_with_unresolved(...) -> tuple[list[Relation], list[ExtractedRelation]]
```

**解析策略** — 三级优先级:

1. **同文件匹配**: `_build_file_index`​ 构建 `{file_path: {name: id}}`，优先同文件
2. **全局名称匹配**: `_build_name_map`​ 构建 `{name: [id1, id2, ...]}`
3. **未解析收集**: 无法匹配的 import 关系单独返回（用于外部依赖追踪）

**同名冲突处理**: 同名实体取第一个匹配。

### 5.2 extractor/semantic.py — LLM 语义提取

```python
class SemanticExtractor:
    def __init__(ollama_url, model, batch_size=10, max_retries=3, timeout=120.0, temperature=0.1)
    def extract(entities, doc_entities=None, concept_entities=None) -> ExtractionResult
    def close()
```

**提取流程**:

1. 实体分批（每批 `batch_size` 个）
2. 构建英文提示词 → 调用 `POST {ollama_url}/api/chat`​（`think: False`）
3. 解析响应：去 `<think/>` 标签 → 提取 JSON → 验证每个关系
4. 置信度过滤: `< 0.5` 的关系被丢弃

**跨类型关系** (启发式):

- 如果 doc/concept 的 `name`​ 出现在 code 的 `source` 中:

  - doc → code: `describes` 关系（置信度 0.7）
  - code → concept: `derived_from` 关系（置信度 0.7）

**错误恢复**: 指数退避重试（`2^attempt` 秒），超时不中断整体提取

## 6. 业务逻辑层

### 6.1 builder.py — 5阶段构建流水线

```python
class LayerKGBuilder:
    def __init__(config: LayerKGConfig)
    def build(repo_path, *, skip_semantic=False, skip_clustering=False, clear=False) -> BuildResult
    def query(text, n_results=10, entity_type=None) -> list[dict]
    def info() -> dict
```

**5阶段流水线**:

```
Stage 1: Parse (必须)
  ├── _scan_files: 递归扫描代码(.py,.java)和文档(.md,.rst)文件
  ├── PythonParser / JavaParser: AST 解析 → entities + relations
  ├── DocParser: 文档解析 → doc_entities
  └── RelationExtractor: 名称→UUID 关系解析

Stage 2: Write (必须，失败终止)
  ├── merge_node: 写入 CodeEntity/DocEntity 节点到 Neo4j
  ├── merge_relation: 写入结构关系
  └── 创建外部模块占位节点（未解析的 import 目标）

Stage 2.5: Doc Link (必须，降级)
  └── _link_docs_to_code: 三策略匹配
      ├── 路径匹配（文件路径包含关系）
      ├── 文件名匹配（去扩展名后相同）
      └── 代码块标识符匹配（Markdown 中的标识符）
      限制: 每个 DocEntity 最多 50 个 describes 关系

Stage 3: Semantic (可选，Ollama 不可用时跳过)
  ├── _check_ollama: 健康检查 GET /api/tags（5秒超时）
  ├── SemanticExtractor: LLM 语义关系提取
  ├── ConceptAligner: 概念去重对齐
  └── _process_semantic_relations: 双路径解析
      ├── Path A: 概念目标（通过 ConceptAligner 对齐）
      └── Path B: 代码目标（通过 _fuzzy_lookup_entity 模糊查找）

Stage 4: Clustering (可选)
  ├── ModuleClustering.detect_modules: Label Propagation 社区检测
  ├── 添加虚拟同文件边（对抗图稀疏性）
  └── 写入 ModuleEntity 节点 + contains 关系

Stage 5: Vector (可选)
  └── _write_all_vectors: 批量写入 ChromaDB
      ├── CodeEntity → source 片段
      ├── DocEntity → content
      ├── ConceptEntity → description
      └── ModuleEntity → description
```

**关键属性映射**: `CodeEntity.parameters`​ → Neo4j 属性 `code_parameters`​（避免与 driver 内部 `parameters` 冲突）

**降级策略**: Stage 3-5 失败不终止构建，错误记录到 `BuildResult.errors`

### 6.2 change_detector.py — 变更检测

```python
class GitChangeDetector:
    def __init__(repo_path, cache=None, supported_extensions=None)
    def detect_changes(since="HEAD~1") -> list[ChangedFile]  # Git diff
    def full_scan() -> list[ChangedFile]                      # 文件系统遍历
    def update_cache(changes)                                 # 更新 SHA256 缓存
```

**变更分类** (`ChangeType` 枚举):

|类型|含义|检测方法|
| ------| -------------| -----------------------------|
|​`ADDED`|新增文件|Git status `A`|
|​`DELETED`|删除文件|Git status `D`|
|​`SIGNATURE`|签名变更|diff 中 `def`​/`class`​/`async def` 行改变|
|​`BODY`|函数体变更|diff 中非签名行改变|
|​`DOC_ONLY`|仅文档/注释|diff 中只有注释和 docstring|

**SHA256Cache**: JSON 文件持久化的哈希缓存，用于 `full_scan` 模式（不依赖 Git）。

### 6.3 impact_propagator.py — 影响传播

```python
class ImpactPropagator:
    def __init__(graph_store, weight_matrix=None, decay_schedule=None,
                 max_depth=3, impact_threshold=0.05)
    def propagate(changes: list[ChangedFile]) -> ImpactReport
    def compute_impact(node_ids, change_type) -> list[ImpactedNode]
```

**双向 BFS 算法**:

- **FORWARD** (谁依赖我): 查询 `get_relations(target_id=source)` — 找指向源的节点
- **BACKWARD** (我依赖谁): 查询 `get_relations(source_id=source)` — 找源指向的节点

**权重矩阵** (`DEFAULT_WEIGHT_MATRIX`):

- ​`calls` + DELETED = 1.0（影响最大）
- ​`imports` + DOC_ONLY = 0.0（文档变更不影响 import 关系）
- ​`derived_from` + * = 0.0（占位，未实现）

**深度衰减**: `DEFAULT_DECAY_SCHEDULE = {1: 1.0, 2: 0.6, 3: 0.3}`

- 影响分数 = `权重 × 衰减`
- 深度 > 3 或衰减 = 0 时提前终止

### 6.4 incremental_updater.py — 增量更新

```python
class IncrementalUpdater:
    def __init__(config, repo_path=None)
    def update(since="HEAD~1", full_scan=False, dry_run=False) -> UpdateReport
```

**4阶段增量更新流水线**:

```
Stage 1: Detect Changes
  └── GitChangeDetector.detect_changes / full_scan

Stage 2: Propagate Impact
  └── ImpactPropagator.propagate → ImpactReport
  └── dry_run 模式到此为止

Stage 3: Selective Regeneration（逐文件处理）
  ├── ADDED: _apply_added → 解析 → 写 Neo4j → 写 ChromaDB
  ├── DELETED: _apply_deleted → 按 file_path 查节点 → DETACH DELETE → 删向量
  └── MODIFIED:
      ├── DOC_ONLY: _update_vectors_only → 只更新 ChromaDB
      └── SIGNATURE/BODY: 删旧关系 → 重新解析 → merge → 重索引向量

Stage 4: Record & Validate
  ├── 创建 ChangeSetEntity（id: cs-{12hex}）
  ├── _flag_concept_reextraction: 标记受影响 ConceptEntity 需重新提取
  ├── _flag_doc_regeneration: 标记受影响 DocEntity 需重新生成
  └── _validate_graph_integrity: 检查孤立 CodeEntity 节点
```

### 6.5 aligner.py — 概念对齐器

```python
class ConceptAligner:
    def __init__(chroma_store, concepts=None, vector_threshold=0.7,
                 neo4j_store=None, graph_overlap_threshold=0.8)
    def align(term: str) -> AlignResult       # 4步对齐流水线
    def align_batch(terms) -> list[AlignResult]
    def add_concept(concept: ConceptEntity)    # 动态添加
```

**4步对齐** (按优先级):

1. **精确匹配**: 名称完全相同 → confidence=1.0
2. **别名匹配**: 别名列表大小写不敏感匹配 → confidence=1.0
3. **向量匹配**: ChromaDB 语义搜索，`1/(1+distance)` 转换置信度，需 > 0.7
4. **图结构匹配**: Neo4j 中 Jaccard 相似度（代码邻居重叠），需 > 0.8

### 6.6 module_clustering.py — 模块聚类

```python
class ModuleClustering:
    def __init__(neo4j_store, algorithm="label_propagation")
    def detect_modules() -> list[ModuleCluster]
    def save_modules(clusters, all_entities=None) -> int
    def get_module_tree() -> dict
```

**Label Propagation 算法**:

1. ​`_load_graph`: 从 Neo4j 加载 CodeEntity + CALLS/IMPORTS/EXTENDS/IMPLEMENTS 关系
2. **添加虚拟同文件边**: 同文件内所有实体两两连接 → `combinations(entities, 2)`（对抗稀疏图）
3. LP 迭代: 确定性排序，邻居标签投票，平票取字典序，最多100轮
4. 收敛后生成模块名: `os.path.commonpath` 提取公共路径

**内聚度计算**: `internal_edges / max_possible_edges`，衡量模块内连接紧密度

## 7. Agent 层

### 7.1 agent/graph.py — LangGraph ReAct 状态图

```python
# 核心组件
class AgentState(MessagesState): pass

def create_agent() -> Any     # 构建编译后的 LangGraph
def run_query(question, thread_id="default") -> str        # 同步单次查询
def run_query_stream(question, thread_id, trace_collector)  # 流式查询
```

**LangGraph 架构**:

```
                  ┌─────────────┐
                  │  START      │
                  └──────┬──────┘
                         │
                    ┌────▼────┐
           ┌───────►  agent  ◄────────┐
           │       └────┬────┘        │
           │            │             │
           │   ┌────────▼──────┐      │
           │   │ should_continue│      │
           │   └───┬──────┬────┘      │
           │       │      │           │
           │   tool_call  no_tool     │
           │       │      │           │
           │  ┌────▼───┐  └──► END    │
           │  │  tools  │             │
           │  └────┬────┘             │
           │       │                  │
           └───────┘  (循环)          │
```

**关键设计**:

- ​`MemorySaver`​ checkpointer 实现多轮对话记忆（按 `thread_id` 隔离）
- ​`_agent_node`​ 每次调用前注入 `AGENT_SYSTEM_PROMPT` 作为 SystemMessage
- LLM 配置: `ChatOpenAI`（兼容 Anthropic API），60秒超时
- ​`recursion_limit=50` 防止无限循环
- 流式输出 SSE 事件: `token`​、`tool_start`​、`tool_end`​、`error`
- Tool 输出截断至 500 字符，tool 调用计时

### 7.2 agent/tools.py — 8个工具定义

|工具|签名|功能|实现细节|
| ------| ------| -------------------| ------------------------------|
|​`semantic_search`|​`(query: str, top_k: int=5)`|ChromaDB 向量搜索|失败时建议用 `graph_query`|
|​`graph_query`|​`(cypher: str)`|Neo4j 原生 Cypher|直接执行用户 Cypher|
|​`impact_analysis`|​`(entity_name: str, depth: int=3)`|变更影响传播|模糊匹配 + `ChangeType.BODY`|
|​`get_context`|​`(entity_name: str)`|实体360°上下文|属性+双向关系+相似实体|
|​`list_concepts`|​`()`|列出概念实体|ConceptAligner.list_concepts|
|​`get_module_tree`|​`()`|模块层次结构|UUID→名称映射 enrichment|
|​`detect_changes`|​`(since: str="HEAD~1")`|Git diff|硬编码仓库路径|
|​`export_graph`|​`(limit: int=100)`|导出图数据|返回 nodes+edges JSON|

**单例模式**: 所有工具通过 `_helpers.py` 的懒加载单例获取组件实例。

### 7.3 agent/prompt.py — 系统提示词

**内容结构**:

1. 角色定义: LayerKG 代码知识图谱助手
2. Schema 参考: 节点标签、关系类型、CodeEntity 属性
3. 8 个工具使用指南
4. **10 个 Cypher 模板**: 查找实体、调用链、继承关系、导入关系、包含关系、概念关联、统计、路径查询
5. 强制规则: 必须使用工具验证，禁止无根据回答
6. 工具选择决策树
7. 失败策略: 一次重试，最多连续 2 次失败

### 7.4 agent/trace.py — Trace 可观测性

```python
class TraceStep:     # 单步: step_id, type(thinking/tool_call/tool_result/final), content
class TraceLog:      # 完整轨迹: thread_id, query, steps[], total_duration_ms, status
class TraceCollector: # 线程安全收集器 + SQLite 持久化
    def start_trace(thread_id, query)
    def add_step(thread_id, type, content, ...)
    def end_trace(thread_id, status)
    def get_trace / delete_trace / list_traces
```

**配置**: `max_traces=500`​, `max_age_seconds=3600`​, `persist_path=".traces.db"`

**自动清理**: 90% 容量时触发，删除过期和超量 trace

**持久化**: SQLite WAL 模式，`asyncio.to_thread` 非阻塞写入

## 8. Web 层

### 8.1 web/app.py — FastAPI 应用工厂

```python
def create_app() -> FastAPI
```

**生命周期管理** (`lifespan`):

- 启动: 创建 `Neo4jGraphStore`​ → 存入 `app.state.graph_store`
- 关闭: 关闭 Neo4j 连接

**CORS**: 从 `CORS_ORIGINS`​ 环境变量读取（默认 `http://localhost:5173`），逗号分隔

**路由挂载**: `/api` 前缀下挂载 chat、graph、trace 三个 router

**共享实例**: 单例 `TraceCollector` 注入到 chat_router

### 8.2 web/router/chat.py — 对话 API

|端点|方法|说明|
| ------| ------| ------------------------------|
|​`POST /api/chat`|同步|返回完整回答|
|​`POST /api/chat/stream`|SSE|流式返回 EventSourceResponse|

**请求模型**: `ChatRequest(message: str, thread_id: str|None)`

- message 非空校验，截断至 2000 字符
- thread_id 为空时自动生成 UUID

**响应模型**: `ChatResponse(answer, thread_id, duration_ms)`

**SSE 事件流**: `token`​ → `tool_start`​ → `tool_end`​ → ... → `done`

**超时**: 120 秒整体超时 (`asyncio.timeout`)

### 8.3 web/router/graph.py — 图可视化 API

|端点|说明|
| ------| ---------------------------|
|​`GET /api/graph/stats`|按标签统计节点数 + 总边数|
|​`GET /api/graph?center=&depth=&limit=&type=`|图数据查询|
|​`GET /api/graph/node/{node_id}`|节点详情（属性+双向关系）|
|​`DELETE /api/graph/node/{node_id}`|删除节点|

**图数据查询模式**:

- ​`center=null`: 全图模式（限制 limit）
- ​`center="Neo4jGraphStore"`: 居中扩展模式（从指定节点扩展 depth 层）

**参数范围**: depth 1-3, limit 1-500, type 逗号分隔标签过滤

**两步查询**: 先获取节点 ID 集合，再查这些节点之间的边（避免笛卡尔爆炸）

### 8.4 web/router/trace.py — Trace 查看API

|端点|说明|
| ------| -------------------------|
|​`GET /api/trace/list`|所有 trace 列表（摘要）|
|​`GET /api/trace/thread/{thread_id}`|完整 trace 详情|
|​`GET /api/trace/graph/mermaid`|Agent 图 Mermaid 可视化|
|​`DELETE /api/trace/thread/{thread_id}`|删除 trace|

## 9. Butler 层

### 9.1 总体架构

```
┌─────────────────────────────────────────┐
│              ButlerEngine               │
│  ┌──────────┐  ┌──────────┐            │
│  │EventBus  │  │Scheduler │            │
│  │(pub/sub) │  │(dispatch)│            │
│  └────┬─────┘  └────┬─────┘            │
│       │              │                  │
│  ┌────▼──────┐  ┌────▼──────────┐      │
│  │GitWatcher │  │Handlers       │      │
│  │(polling)  │  │├ KnowledgeUpdate│     │
│  └──────────┘  │├ FullBuild     │      │
│                │└ Reflection    │      │
│  ┌──────────┐  └───────────────┘      │
│  │ConsistGd │  ┌──────────┐            │
│  │(audit)   │  │SkillStore│            │
│  └──────────┘  │(patterns)│            │
│                └──────────┘            │
└─────────────────────────────────────────┘
```

### 9.2 butler/engine.py — 主引擎

```python
class ButlerEngine:
    def __init__(config: LayerKGConfig)
    def register_handler(handler: BaseHandler)
    async def start()     # 创建 Guard + SkillStore + 注册 Scheduler
    async def stop()      # 清理资源
    async def submit_event(event) -> list[HandlerResult]
    async def status() -> dict
```

**事件流**:

1. GitWatcher 轮询到变更 → `EventBus.publish(ButlerEvent)`
2. ​`_dispatch_event`​ 过滤只处理 `source="git_watcher"` 的事件
3. ​`Scheduler.dispatch` 匹配 event_type → 执行对应 Handler
4. Handler 完成 → 发布 `handler.completed`​/`handler.failed` 事件
5. 级联: `ReflectionHandler` 监听完成/失败事件 → 归纳技能模式

**HandlerSpec 默认值**: `retry_count=2`​, `retry_delay=0.1`​, `max_concurrency=10`

### 9.3 butler/event_bus.py — 事件总线

```python
class ButlerEvent:
    event_id: str       # UUID
    event_type: str     # "code.changed", "build.full", "handler.completed" 等
    payload: dict
    source: str         # "git_watcher", "cli", "git_watcher.manual"
    timestamp: str      # ISO 8601

class EventBus:
    def subscribe(event_type, callback) -> str   # 返回 subscription_id
    def unsubscribe(subscription_id)
    async def publish(event)                     # 分发到所有匹配订阅
    def publish_sync(event)                      # 同步包装（自动创建线程）
```

**实现**: 每个订阅一个 `asyncio.Queue`​ + `asyncio.Task`​ 消费循环。支持 `"*"` 通配符订阅。

### 9.4 butler/scheduler.py — Handler 调度器

```python
@dataclass
class HandlerSpec:
    handler_id: str
    event_types: list[str]
    handler_fn: Callable
    retry_count: int = 0        # 重试次数
    retry_delay: float = 0.1    # 重试间隔（秒）
    timeout: float | None = None
    max_concurrency: int = 10

class Scheduler:
    def register(spec: HandlerSpec)             # 每个Handler一个 Semaphore
    async def dispatch(event) -> list[HandlerResult]  # 匹配+执行
```

**重试机制**: `max_attempts = retry_count + 1`，固定间隔重试

### 9.5 butler/handlers/ — Handler 实现

**KnowledgeUpdateHandler** (`handler_id: "knowledge.update"`):

- 监听: `"code.changed"`
- 操作: `IncrementalUpdater.update()`​ 包裹在 `asyncio.to_thread` 中
- 审计: 记录 changeset_id、changes_detected

**FullBuildHandler** (`handler_id: "knowledge.full_build"`):

- 监听: `"build.full"`
- 操作: `LayerKGBuilder.build()`​ 包裹在 `asyncio.to_thread` 中
- 审计: 记录 files_scanned、entities_created

**ReflectionHandler** (`handler_id: "butler.reflection"`):

- 监听: `"handler.completed"`
- 归纳算法:

  1. 生成签名 `"{event_type}:{file_extension}"`
  2. 搜索现有技能
  3. 已存在 → hit_count++, 重算置信度 `0.5 + hit_count × 0.1`​（上限1.0），≥0.8 提升为 `active`
  4. 不存在 → 创建新 `SkillEntity`（confidence=0.5, status="candidate", layer=RULE）

### 9.6 butler/consistency/guard.py — 审计日志

```python
class AuditEntry:
    entry_id, operation, target_type, target_id, before(JSON), after(JSON), operator, timestamp

class ConsistencyGuard:
    def __init__(db_path)      # SQLite + audit_log 表
    async def log_operation(op, target_type, target_id, before, after, operator) -> str
    async def query(target_type, target_id=None) -> list[AuditEntry]
    async def get_last_operation(target_type, target_id) -> AuditEntry | None
```

SQLite WAL 模式，索引 `(target_type, target_id)`​ 和 `timestamp DESC`。

### 9.7 butler/skills/store.py — 技能模式存储

```python
class SkillLayer(Enum):
    RULE = "rule"       # 规则层：固定模式
    META = "meta"       # 元层：策略模式
    HARNESS = "harness" # 利用层：组合模式

class SkillEntity:
    skill_id, name, layer, pattern(dict), action(dict),
    confidence [0.0-1.0], source, status("candidate"/"active"/"deprecated"),
    hit_count, version, parent_id

class SkillStore:
    def create(skill) -> str
    def get(skill_id) -> SkillEntity | None
    def update(skill_id, **kwargs) -> bool
    def delete(skill_id) -> bool       # 软删除 → status="deprecated"
    def list_by_layer(layer, status=None) -> list[SkillEntity]
    def get_candidates(min_confidence=0.5) -> list[SkillEntity]
    def increment_hit_count(skill_id) -> bool
```

**数据库约束**: `CHECK (confidence >= 0.0 AND confidence <= 1.0)`

### 9.8 butler/watchers/git_watcher.py — Git 监控

```python
class GitWatcher:
    def __init__(repo_path, bus, poll_interval=30.0, initial_scan=False)
    async def start()    # 启动轮询 Task
    async def stop()     # 取消 Task
    async def trigger(since=None)  # 手动触发（CLI/测试用）
```

**轮询逻辑**: `asyncio.sleep(poll_interval)`​ → `git rev-parse HEAD`​ → 对比 `_last_ref` → 发布事件

**静默错误**: `_poll_loop` 中异常被吞掉，防止单次错误导致监控崩溃

## 10. 数据流总览

### 10.1 全量构建数据流

```
源码文件(.py/.java/.md/.rst)
    │
    ▼
[Parser] AST/Regex 解析
    │ entities (CodeEntity) + ExtractedRelation
    ▼
[RelationExtractor] 名称→UUID 解析
    │ Relation (UUID-based) + unresolved imports
    ▼
[Neo4jGraphStore] merge_node + merge_relation    ← Stage 2
    │
    ├── [DocParser] → DocEntity
    │   └── _link_docs_to_code → describes 关系  ← Stage 2.5
    │
    ├── [SemanticExtractor] → SemanticRelation     ← Stage 3
    │   └── [ConceptAligner] 对齐去重 → ConceptEntity + 语义关系
    │
    ├── [ModuleClustering] → ModuleEntity          ← Stage 4
    │   └── Label Propagation → contains 关系
    │
    └── [ChromaStore] put_entities_batch           ← Stage 5
        └── Ollama 嵌入 → 向量索引
```

### 10.2 增量更新数据流

```
Git commit / 文件变更
    │
    ▼
[GitChangeDetector] detect_changes
    │ list[ChangedFile] (ADDED/DELETED/SIGNATURE/BODY/DOC_ONLY)
    ▼
[ImpactPropagator] propagate
    │ ImpactReport (双向BFS, 加权衰减)
    ▼
[IncrementalUpdater] selective regeneration
    │ ADDED: parse → merge → vectorize
    │ DELETED: detach_delete → remove vectors
    │ MODIFIED: delete_old → reparse → merge → reindex
    ▼
[ChangeSetEntity] + flag concepts/docs for re-extraction
```

### 10.3 Agent 查询数据流

```
用户问题 (中文/英文)
    │
    ▼
[Agent SystemPrompt] + 10 Cypher 模板
    │
    ▼
[LangGraph ReAct Loop]
    ├── agent (LLM) → 选择工具
    ├── tools (ToolNode) → 执行工具
    │   ├── semantic_search → ChromaDB
    │   ├── graph_query → Neo4j
    │   ├── impact_analysis → ImpactPropagator
    │   ├── get_context → Neo4j + ChromaDB
    │   └── ... (8 tools)
    └── 观察 → 继续推理 or 返回答案
    │
    ▼
[TraceCollector] 记录推理步骤
    │
    ▼
最终答案 + Trace (可选查看)
```

## 11. 扩展点

### 11.1 添加新语言解析器

1. 在 `src/layerkg/parser/`​ 创建 `xxx_parser.py`
2. 继承 `BaseParser`​，实现 `parse_file`​、`parse_source`​、`language`
3. 在 `builder.py`​ 的 `__init__`​ 中注册: `self._parsers[".xxx"] = XxxParser()`
4. 更新 `config.py`​ 的 `build_skip_dirs`（如需）

### 11.2 添加新 Handler

1. 在 `src/layerkg/butler/handlers/`​ 创建 `xxx_handler.py`
2. 继承 `BaseHandler`​，实现 `handler_id`​、`event_types`​、`handle`
3. 在 `cli.py`​ 的 `butler serve`​ 命令中 `engine.register_handler(XxxHandler())`

### 11.3 添加新 MCP 工具

1. 在 `mcp_server.py`​ 中用 `@mcp.tool()` 装饰器添加新函数
2. 函数签名自动生成 JSON Schema 描述

### 11.4 添加新 Agent 工具

1. 在 `agent/tools.py`​ 中用 `@tool` 装饰器添加新函数
2. 添加到 `ALL_TOOLS` 列表
3. 更新 `agent/prompt.py` 中的工具使用指南
