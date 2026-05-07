# Day 10: SemanticExtractor 实现计划

> **For Hermes:** 使用 claude-code skill 的 print mode (`-p`) 执行任务。每个任务 2-5 分钟粒度。强制 TDD。详细规范见 `.claude/rules/` 目录。

**Goal:** 实现语义关系提取器 — 调用 Ollama LLM（qwen3.5:9b）从代码实体中提取 4 种语义关系（semantic_impact, describes, illustrates, derived_from）。

---

## 一、背景与依赖

### 已有组件

| 组件 | 文件 | 职责 |
|------|------|------|
| Schema | `schema.py` | 6 实体 + 11 关系（含 semantic_impact, describes, illustrates, derived_from） |
| RelationExtractor | `extractor/relation.py` | 结构关系提取（calls, extends, implements, imports, contains） |
| LayerKGBuilder | `builder.py` | 全量构建流水线：解析→提取→写图+向量 |
| IncrementalUpdater | `incremental_updater.py` | 增量更新编排器 |
| LayerKGConfig | `config.py` | 配置管理（含 ollama_base_url, embedding_model） |
| OllamaEmbeddingFunction | `chroma_store.py` | 已有 httpx + Ollama REST API 调用模式 |
| GraphStore ABC | `graph_store.py` | merge_node, merge_relation |
| Neo4jGraphStore | `neo4j_store.py` | Neo4j 实现 |
| PythonParser | `parser/python_parser.py` | AST 解析 → CodeEntity 列表 |
| ParseResult/ExtractedRelation | `parser/base.py` | 中间表示 |
| ChromaStore | `chroma_store.py` | 向量存储 + 语义搜索 |
| exceptions | `exceptions.py` | LayerKGError, SchemaValidationError, StoreError, EmbeddingError |

### 语义关系（Schema 中已定义）

| 关系类型 | Neo4j 标签 | 源→目标 | 含义 |
|----------|-----------|---------|------|
| `semantic_impact` | SEMANTIC_IMPACT | Code→Code | A 的变更可能影响 B 的行为 |
| `describes` | DESCRIBES | Doc→Code/Concept | 文档描述某个代码/概念 |
| `illustrates` | ILLUSTRATES | Resource→Code/Concept | 资源（图表）展示某个代码/概念 |
| `derived_from` | DERIVED_FROM | Code→Concept | 代码实现源自某个概念 |

### Ollama API 调研结论

- **Endpoint**: `POST http://REDACTED_IP:11434/api/chat`
- **模型**: `qwen3.5:9b`（已验证可用）
- **关键参数**: `stream: false`, `think: false`（关闭思考模式，~3s 响应）
- **已有模式**: `chroma_store.py` 中的 `OllamaEmbeddingFunction` 用 httpx 调 Ollama REST API
- **依赖**: 项目已有 `httpx>=0.28.1`，无需新增依赖

### 本日新增/修改文件

- **新增** `src/layerkg/extractor/semantic.py` — SemanticExtractor + SemanticRelation + ExtractionResult
- **新增** `tests/unit/test_semantic_extractor.py` — ~45 tests
- **修改** `src/layerkg/config.py` — 添加 `llm_model` 字段
- **修改** `src/layerkg/exceptions.py` — 添加 `ExtractionError` 异常
- **修改** `src/layerkg/extractor/__init__.py` — 导出新符号
- **修改** `src/layerkg/builder.py` — build() 中集成语义提取（可选步骤）

---

## 二、核心设计

### 架构定位

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
│ PythonParser │───▶│RelationExtractor│  │ SemanticExtractor │
│ (AST)        │    │ (结构关系)     │  │ (LLM 语义关系)    │
└──────────────┘    └──────────────┘    └──────────────────┘
     ↓                     ↓                     ↓
  CodeEntity[]      Relation[]           SemanticRelation[]
  ExtractedRelation[]                    (semantic_impact,
                                         describes, illustrates,
                                         derived_from)
```

SemanticExtractor **不依赖** PythonParser 的输出，它直接接收 CodeEntity 列表和可选的 DocEntity/ConceptEntity，调用 LLM 提取语义关系。

### 工作流程

```
CodeEntity[] + DocEntity[] ──▶ SemanticExtractor.extract() ──▶ ExtractionResult
                                      │
                              ┌───────┼───────┐
                              ▼       ▼       ▼
                         按实体     LLM      解析
                         分组      调用     JSON响应
                         (batch)  (Ollama)  → SemanticRelation[]
```

### 批处理策略

1. **实体分批**: 将 CodeEntity 按 `file_path` 分组，每批 ≤5 个实体
2. **LLM 调用**: 每批发送一次 chat 请求，让 LLM 提取实体间语义关系
3. **结果解析**: 从 LLM JSON 响应中解析出 SemanticRelation 列表
4. **重试**: 单次调用失败自动重试，最多 3 次（指数退避）

---

## 三、公共接口规范

### SemanticRelation dataclass

```python
@dataclass
class SemanticRelation:
    """LLM 提取的语义关系（中间表示，待存入图谱）。"""

    source_name: str          # 源实体名称
    source_type: str          # 源实体类型（function/class/module 等）
    target_name: str          # 目标实体名称
    target_type: str          # 目标实体类型
    relation_type: str        # 必须是 semantic_impact/describes/illustrates/derived_from 之一
    confidence: float         # 置信度 [0, 1]
    reasoning: str            # LLM 给出的推理说明
    file_path: str            # 所属文件路径
```

### ExtractionResult dataclass

```python
@dataclass
class ExtractionResult:
    """语义提取结果。"""

    relations: list[SemanticRelation]    # 提取到的语义关系
    entities_processed: int              # 处理的实体数
    llm_calls: int                       # LLM 调用次数
    total_tokens: int                    # 总 token 消耗
    elapsed_ms: float                    # 总耗时（毫秒）
    errors: list[str]                    # 错误信息列表

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "relations_found": len(self.relations),
            "entities_processed": self.entities_processed,
            "llm_calls": self.llm_calls,
            "total_tokens": self.total_tokens,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "errors": self.errors,
        }
```

### SemanticExtractor 类

```python
class SemanticExtractor:
    """语义关系提取器。

    使用 Ollama LLM 从代码实体中提取语义关系：
    - semantic_impact: A 的变更可能影响 B
    - describes: 文档描述代码/概念
    - illustrates: 资源展示代码/概念
    - derived_from: 代码实现源自概念
    """

    VALID_SEMANTIC_RELATIONS = frozenset({
        "semantic_impact",
        "describes",
        "illustrates",
        "derived_from",
    })

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        model: str = "qwen3.5:9b",
        *,
        batch_size: int = 5,
        max_retries: int = 3,
        timeout: float = 60.0,
        temperature: float = 0.1,
    ) -> None:
        """初始化。

        Args:
            ollama_url: Ollama 服务地址。
            model: LLM 模型名称。
            batch_size: 每批处理的实体数量。
            max_retries: LLM 调用失败最大重试次数。
            timeout: HTTP 请求超时（秒）。
            temperature: LLM 生成温度。
        """

    def extract(
        self,
        entities: list[CodeEntity],
        *,
        doc_entities: list[DocEntity] | None = None,
        concept_entities: list[ConceptEntity] | None = None,
    ) -> ExtractionResult:
        """从实体列表中提取语义关系。

        Args:
            entities: 代码实体列表。
            doc_entities: 文档实体列表（可选，用于 describes 关系）。
            concept_entities: 概念实体列表（可选，用于 derived_from 关系）。

        Returns:
            ExtractionResult 提取结果。
        """

    def extract_batch(
        self,
        entities: list[CodeEntity],
    ) -> list[SemanticRelation]:
        """处理单个批次，调用 LLM 提取语义关系。

        Args:
            entities: 一批代码实体（≤ batch_size）。

        Returns:
            提取到的语义关系列表。
        """

    @staticmethod
    def _build_prompt(entities: list[CodeEntity]) -> str:
        """构建 LLM prompt。

        Args:
            entities: 待分析的代码实体。

        Returns:
            完整的 prompt 字符串。
        """

    @staticmethod
    def _parse_response(response_text: str) -> list[SemanticRelation]:
        """解析 LLM 响应为 SemanticRelation 列表。

        Args:
            response_text: LLM 返回的文本。

        Returns:
            解析出的语义关系列表。

        Raises:
            ExtractionError: 当响应无法解析时。
        """

    @staticmethod
    def _validate_relation(rel: SemanticRelation) -> bool:
        """校验单个语义关系的有效性。

        Args:
            rel: 待校验的语义关系。

        Returns:
            True 如果关系有效。
        """
```

### ExtractionError 异常

```python
# exceptions.py 新增
class ExtractionError(LayerKGError):
    """语义关系提取失败。"""
```

### Config 扩展

```python
# config.py 新增字段
@dataclass
class LayerKGConfig:
    # ... 现有字段 ...
    llm_model: str = "qwen3.5:9b"     # 语义提取使用的 LLM 模型

    # from_env 中新增：
    llm_model=os.getenv("LAYERKG_LLM_MODEL", cls.llm_model),
```

---

## 四、内部实现逻辑

### extract() 主流程

```python
def extract(self, entities, *, doc_entities=None, concept_entities=None) -> ExtractionResult:
    start = time.time()
    all_relations: list[SemanticRelation] = []
    errors: list[str] = []
    total_tokens = 0
    llm_calls = 0

    # 1. 将实体按批次分组
    batches = self._create_batches(entities)

    # 2. 逐批调用 LLM
    for batch in batches:
        try:
            batch_relations = self.extract_batch(batch)
            all_relations.extend(batch_relations)
        except ExtractionError as e:
            errors.append(str(e))
        finally:
            llm_calls += 1

    # 3. 可选：跨类型关系（Code-Doc, Code-Concept）
    if doc_entities:
        doc_relations = self._extract_cross_type_relations(entities, doc_entities, "describes")
        all_relations.extend(doc_relations)
    if concept_entities:
        concept_relations = self._extract_cross_type_relations(entities, concept_entities, "derived_from")
        all_relations.extend(concept_relations)

    return ExtractionResult(
        relations=all_relations,
        entities_processed=len(entities),
        llm_calls=llm_calls,
        total_tokens=total_tokens,
        elapsed_ms=(time.time() - start) * 1000,
        errors=errors,
    )
```

### _build_prompt() — 核心提示词

```python
@staticmethod
def _build_prompt(entities: list[CodeEntity]) -> str:
    entity_descriptions = []
    for e in entities:
        desc = f"- {e.entity_type} `{e.name}`"
        if e.source:
            # 截断过长的源码
            source_preview = e.source[:200] + "..." if len(e.source) > 200 else e.source
            desc += f": {source_preview}"
        if e.file_path:
            desc += f" (file: {e.file_path})"
        entity_descriptions.append(desc)

    entities_text = "\n".join(entity_descriptions)

    return f"""Analyze the following code entities and extract semantic relationships.

Entities:
{entities_text}

Extract ONLY these relationship types:
1. "semantic_impact": Entity A's changes likely affect Entity B's behavior
2. "describes": A document describes a code entity
3. "illustrates": A resource illustrates a code entity
4. "derived_from": Code implements a concept/pattern

Return a JSON object with this exact format:
{{
  "relations": [
    {{
      "source": "EntityName",
      "source_type": "function",
      "target": "EntityName",
      "target_type": "class",
      "relation_type": "semantic_impact",
      "confidence": 0.8,
      "reasoning": "Brief explanation"
    }}
  ]
}}

Rules:
- Only include relations you are confident about (confidence >= 0.5)
- relation_type must be one of: semantic_impact, describes, illustrates, derived_from
- Return ONLY the JSON, no additional text"""
```

### _parse_response() — 响应解析

```python
@staticmethod
def _parse_response(response_text: str) -> list[SemanticRelation]:
    # 1. 提取 JSON（处理 markdown code block 包裹）
    text = response_text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # 2. 解析 JSON
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"LLM response is not valid JSON: {e}") from e

    # 3. 校验结构
    if not isinstance(data, dict) or "relations" not in data:
        raise ExtractionError("LLM response missing 'relations' key")

    # 4. 转换为 SemanticRelation
    relations = []
    for item in data["relations"]:
        try:
            rel = SemanticRelation(
                source_name=item["source"],
                source_type=item["source_type"],
                target_name=item["target"],
                target_type=item["target_type"],
                relation_type=item["relation_type"],
                confidence=float(item.get("confidence", 0.5)),
                reasoning=item.get("reasoning", ""),
                file_path="",  # 由调用者填充
            )
            if SemanticExtractor._validate_relation(rel):
                relations.append(rel)
        except (KeyError, ValueError):
            continue  # 跳过格式错误的单条关系

    return relations
```

### extract_batch() — 单批处理

```python
def extract_batch(self, entities: list[CodeEntity]) -> list[SemanticRelation]:
    if not entities:
        return []

    prompt = self._build_prompt(entities)

    # 带重试的 LLM 调用
    for attempt in range(self._max_retries):
        try:
            response = self._call_llm(prompt)
            relations = self._parse_response(response)
            # 填充 file_path
            file_paths = {e.name: (e.file_path or "") for e in entities}
            for rel in relations:
                if not rel.file_path:
                    rel.file_path = file_paths.get(rel.source_name, "")
            return relations
        except ExtractionError:
            if attempt == self._max_retries - 1:
                raise
            time.sleep(2 ** attempt)  # 指数退避

    return []  # unreachable
```

### _call_llm() — HTTP 调用

```python
def _call_llm(self, prompt: str) -> str:
    """调用 Ollama chat API。"""
    try:
        response = self._client.post(
            f"{self._ollama_url}/api/chat",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {
                    "temperature": self._temperature,
                    "num_predict": 1024,
                },
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"]
    except httpx.HTTPError as e:
        raise ExtractionError(f"Ollama API call failed: {e}") from e
```

---

## 五、Mock 策略

SemanticExtractor 的 LLM 调用是外部依赖，**必须 mock**：

| 依赖 | Mock 方式 | 说明 |
|------|-----------|------|
| Ollama API | `patch.object(extractor, "_call_llm")` | 返回预设的 JSON 字符串 |
| httpx.Client | 不直接 mock（通过 _call_llm 间接 mock） | 测试不依赖网络 |

**Mock 示例**:

```python
@pytest.fixture
def extractor() -> SemanticExtractor:
    return SemanticExtractor(
        ollama_url="http://fake:11434",
        model="test-model",
        batch_size=3,
        max_retries=1,
        timeout=5.0,
    )

MOCK_LLM_RESPONSE = json.dumps({
    "relations": [
        {
            "source": "UserService",
            "source_type": "class",
            "target": "AuthModule",
            "target_type": "class",
            "relation_type": "semantic_impact",
            "confidence": 0.9,
            "reasoning": "UserService depends on AuthModule for authentication",
        }
    ]
})

def test_extract_single_batch(extractor):
    with patch.object(extractor, "_call_llm", return_value=MOCK_LLM_RESPONSE):
        entities = [
            CodeEntity(name="UserService", entity_type="class", source="class UserService: ..."),
            CodeEntity(name="AuthModule", entity_type="class", source="class AuthModule: ..."),
        ]
        result = extractor.extract(entities)
        assert len(result.relations) == 1
        assert result.relations[0].relation_type == "semantic_impact"
```

---

## 六、关键设计决策

1. **独立于 RelationExtractor**: SemanticExtractor 是独立的提取器，不继承/包装 RelationExtractor，保持关注点分离
2. **批量处理**: 每批 ≤5 个实体，避免 prompt 过长导致 LLM 响应质量下降
3. **httpx 复用**: 使用与 OllamaEmbeddingFunction 相同的 httpx 库（项目已有依赖），不引入 ollama Python SDK
4. **think: False**: qwen3.5:9b 默认开启思考模式，必须传 `think: False` 关闭以加速响应
5. **置信度过滤**: 只保留 confidence ≥ 0.5 的关系，低置信度的丢弃
6. **容错**: 单批失败不影响其他批次，错误记录到 ExtractionResult.errors
7. **Builder 集成**: 在 `builder.py` 的 `build()` 中新增可选步骤调用 SemanticExtractor（Day 10 先写独立的测试，Day 12 集成到 Builder/CLI）
8. **SemanticRelation vs Relation**: SemanticRelation 是中间表示（用名称），最终需要 resolve 为 Relation（用 ID），这个 resolve 步骤在 Builder 层完成

---

## 七、TDD 任务列表（~45 tests）

> 每个任务 = 2-5 分钟。严格 RED-GREEN-REFACTOR。

### Task 1: ExtractionError + Config 扩展 (3 tests)

**Files:** Modify `src/layerkg/exceptions.py`, Modify `src/layerkg/config.py`, Modify `tests/unit/test_schema.py` or Create helper

- ExtractionError 继承 LayerKGError
- LayerKGConfig 新增 `llm_model` 字段，默认 `"qwen3.5:9b"`
- `LayerKGConfig.from_env()` 读取 `LAYERKG_LLM_MODEL` 环境变量

### Task 2: SemanticRelation dataclass (5 tests)

**Files:** Create `src/layerkg/extractor/semantic.py`, Create `tests/unit/test_semantic_extractor.py`

- 创建完整 SemanticRelation（所有字段）
- 默认值正确（confidence=0.5, reasoning="", file_path=""）
- relation_type 校验（必须是 4 种语义关系之一，否则 raise ValueError）
- confidence 范围校验（必须在 [0, 1]，否则 raise ValueError）
- 相等性比较（source_name + target_name + relation_type 相同则视为相同关系）

### Task 3: ExtractionResult dataclass (4 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- 创建完整 ExtractionResult（所有字段）
- 默认值正确（空列表和零值）
- `to_dict()` 输出正确（含 elapsed_ms 四舍五入）
- to_dict 中 relations 字段为 count 而非完整列表

### Task 4: SemanticExtractor 构造函数 (4 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- 创建实例，所有参数属性正确
- 默认参数值正确（batch_size=5, max_retries=3, timeout=60.0, temperature=0.1）
- VALID_SEMANTIC_RELATIONS frozenset 包含 4 种关系
- httpx.Client 被创建（可用于后续调用）

### Task 5: _build_prompt (5 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- 空 entities → prompt 包含 "Entities:" 后为空
- 1 个 entity（有 source）→ prompt 包含实体名和源码预览
- 1 个 entity（无 source）→ prompt 只包含实体名
- 多个 entities → prompt 包含所有实体
- 长 source（>200 chars）→ prompt 中被截断为 200 + "..."

### Task 6: _parse_response 正常 (5 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- 标准 JSON → 解析出 1 个 SemanticRelation
- 包含 ```json``` code block → 正确提取 JSON
- 包含 ``` ``` code block → 正确提取 JSON
- 多个 relations → 全部解析
- confidence 缺失 → 默认 0.5

### Task 7: _parse_response 异常 (5 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- 非 JSON 文本 → raise ExtractionError
- JSON 缺少 "relations" key → raise ExtractionError
- 单条 relation 缺少 "source" key → 跳过该条，返回其余
- relation_type 无效 → 跳过该条
- confidence > 1.0 → 跳过该条

### Task 8: _validate_relation (4 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- 有效关系 → True
- 无效 relation_type → False
- confidence < 0.5 → False
- source_name 为空 → False

### Task 9: _call_llm (4 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- mock httpx 响应 → 返回 content 字符串
- HTTP 错误 → raise ExtractionError（含 "Ollama API call failed"）
- 超时 → raise ExtractionError
- 请求参数正确（model, messages, stream, think, options）

### Task 10: extract_batch 单批 (4 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- mock _call_llm → 返回 2 个 relations
- 空 entities → 返回空列表（不调用 _call_llm）
- file_path 自动填充（从 entities 中查找）
- _call_llm 失败重试 3 次后 raise ExtractionError

### Task 11: extract 多批次 (4 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- 7 个 entities（batch_size=3）→ 3 次 LLM 调用（3+3+1）
- ExtractionResult.entities_processed = 7
- ExtractionResult.llm_calls = 3
- 部分批次失败 → errors 非空但其他批次结果保留

### Task 12: extract 完整流程 (5 tests)

**Files:** `src/layerkg/extractor/semantic.py`, `tests/unit/test_semantic_extractor.py`

- 完整流程：extract(5 entities) → ExtractionResult 所有字段正确
- elapsed_ms > 0
- 包含 doc_entities → 额外提取 describes 关系
- 包含 concept_entities → 额外提取 derived_from 关系
- 空实体列表 → ExtractionResult(relations=[], entities_processed=0, llm_calls=0)

### Task 13: 边界测试 + 集成 (3 tests)

**Files:** `tests/unit/test_semantic_extractor.py`

- 大量实体（20个）→ 正确分批（4批）
- LLM 返回空 relations → ExtractionResult.relations 为空列表，无异常
- 置信度过滤：LLM 返回 confidence=0.3 的关系 → 被过滤掉

**合计：~51 tests**

---

## 八、审核修复记录

> Claude Code 审核返回 NEEDS_CHANGES，8 个问题。以下是逐项修复：

| # | 问题 | 严重性 | 修复 |
|---|------|--------|------|
| 1 | 缺少 `_create_batches` / `_extract_cross_type_relations` / `close()` 定义 | 🔴 | 在第三节补充完整定义 |
| 2 | SemanticRelation.file_path 语义不清 | 🔴 | 改为 `source_file_path`，target 文件路径由 resolve 阶段查找 |
| 3 | 缺少 import 声明 | 🔴 | 补充完整的 import 列表 |
| 4 | source_type/target_type 校验缺失 | 🔴 | 添加合法类型白名单校验 |
| 5 | 跨类型关系逻辑缺失 | 🟡 | Day 10 只实现 Code→Code 的 semantic_impact，describes/derived_from/illustrates 留 Day 11 跨类型扩展 |
| 6 | token 统计缺失 | 🟡 | _call_llm 返回 (content, tokens)，从 Ollama 响应中提取 |
| 7 | 名称→ID resolve 未定义 | 🟡 | 复用 RelationExtractor._build_name_map 模式，在 Builder 层 resolve |
| 8 | 置信度过滤策略不统一 | 🟢 | 统一在 _validate_relation 中过滤，_parse_response 调用它 |

---

## 九、修复后的完整接口规范

### Import 列表

```python
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

import httpx

from layerkg.exceptions import ExtractionError
from layerkg.schema import CodeEntity, VALID_RELATION_TYPES
```

### SemanticRelation dataclass（修复 #2 #4）

```python
# 合法的 source_type / target_type 值
VALID_SOURCE_TYPES = frozenset({
    "function", "class", "interface", "module", "file",  # CodeEntity
    "readme", "module_doc", "api_doc", "comment", "wiki", "architecture_doc",  # DocEntity
    "business_concept", "design_pattern", "api_contract", "data_model", "process",  # ConceptEntity
})

@dataclass
class SemanticRelation:
    """LLM 提取的语义关系（中间表示，待存入图谱）。"""

    source_name: str          # 源实体名称
    source_type: str          # 源实体类型（必须是 VALID_SOURCE_TYPES 之一）
    target_name: str          # 目标实体名称
    target_type: str          # 目标实体类型（必须是 VALID_SOURCE_TYPES 之一）
    relation_type: str        # 必须是 semantic_impact/describes/illustrates/derived_from 之一
    confidence: float = 0.5   # 置信度 [0, 1]
    reasoning: str = ""       # LLM 给出的推理说明
    source_file_path: str = ""  # 源实体所在文件路径

    def __post_init__(self) -> None:
        """校验字段。"""
        if not self.source_name:
            raise ValueError("source_name cannot be empty")
        if not self.target_name:
            raise ValueError("target_name cannot be empty")
        if self.source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid source_type: {self.source_type}")
        if self.target_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid target_type: {self.target_type}")
        semantic_types = {"semantic_impact", "describes", "illustrates", "derived_from"}
        if self.relation_type not in semantic_types:
            raise ValueError(f"Invalid relation_type: {self.relation_type}")
        if not (0 <= self.confidence <= 1):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
```

### SemanticExtractor 补充方法（修复 #1 #5 #6）

```python
class SemanticExtractor:
    # ... 已有 __init__, extract, extract_batch, _build_prompt, _parse_response, _validate_relation, _call_llm ...

    def _create_batches(self, entities: list[CodeEntity]) -> list[list[CodeEntity]]:
        """将实体列表按 batch_size 分批。

        Args:
            entities: 实体列表。

        Returns:
            分批后的二维列表。
        """
        if not entities:
            return []
        return [entities[i:i + self._batch_size] for i in range(0, len(entities), self._batch_size)]

    def _extract_cross_type_relations(
        self,
        code_entities: list[CodeEntity],
        other_entities: list[CodeEntity],
        relation_type: str,
    ) -> list[SemanticRelation]:
        """提取跨类型语义关系（Day 10 简化实现：基于名称匹配规则）。

        Day 11 将替换为 LLM 调用。当前实现：
        - 遍历 other_entities，若名称出现在 code_entities 的 source 中，则建立关系。

        Args:
            code_entities: 代码实体列表。
            other_entities: 其他类型实体列表（Doc 或 Concept）。
            relation_type: 关系类型（describes/derived_from）。

        Returns:
            提取到的语义关系列表。
        """
        relations = []
        for other in other_entities:
            for code in code_entities:
                if code.source and other.name in code.source:
                    relations.append(SemanticRelation(
                        source_name=other.name,
                        source_type=other.entity_type,
                        target_name=code.name,
                        target_type=code.entity_type,
                        relation_type=relation_type,
                        confidence=0.6,
                        reasoning=f"Name '{other.name}' found in source of '{code.name}'",
                        source_file_path=other.file_path or "",
                    ))
        return relations

    def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if hasattr(self, "_client") and self._client:
            self._client.close()

    def __enter__(self) -> SemanticExtractor:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
```

### _call_llm 修复（#6 token 统计）

```python
def _call_llm(self, prompt: str) -> tuple[str, int]:
    """调用 Ollama chat API。

    Args:
        prompt: 用户 prompt。

    Returns:
        (响应文本, token消耗) 元组。

    Raises:
        ExtractionError: 当 API 调用失败时。
    """
    try:
        response = self._client.post(
            f"{self._ollama_url}/api/chat",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {
                    "temperature": self._temperature,
                    "num_predict": 1024,
                },
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = data["message"]["content"]
        # Ollama 返回 prompt_eval_count + eval_count
        tokens = (data.get("prompt_eval_count", 0) or 0) + (data.get("eval_count", 0) or 0)
        return content, tokens
    except httpx.HTTPError as e:
        raise ExtractionError(f"Ollama API call failed: {e}") from e
```

### extract() 修复（#5 #6 #8）

```python
def extract(
    self,
    entities: list[CodeEntity],
    *,
    doc_entities: list[DocEntity] | None = None,
    concept_entities: list[ConceptEntity] | None = None,
) -> ExtractionResult:
    start = time.time()
    all_relations: list[SemanticRelation] = []
    errors: list[str] = []
    total_tokens = 0
    llm_calls = 0

    # 1. Code→Code 语义关系（LLM）
    batches = self._create_batches(entities)
    for batch in batches:
        try:
            batch_relations = self.extract_batch(batch)
            all_relations.extend(batch_relations)
        except ExtractionError as e:
            errors.append(str(e))
        finally:
            llm_calls += 1

    # 2. 跨类型关系（Day 10 简化规则匹配）
    if doc_entities:
        doc_relations = self._extract_cross_type_relations(entities, doc_entities, "describes")
        all_relations.extend(doc_relations)
    if concept_entities:
        concept_relations = self._extract_cross_type_relations(entities, concept_entities, "derived_from")
        all_relations.extend(concept_relations)

    return ExtractionResult(
        relations=all_relations,
        entities_processed=len(entities),
        llm_calls=llm_calls,
        total_tokens=total_tokens,
        elapsed_ms=(time.time() - start) * 1000,
        errors=errors,
    )
```

### extract_batch 修复（#6 token）

```python
def extract_batch(self, entities: list[CodeEntity]) -> list[SemanticRelation]:
    if not entities:
        return []

    prompt = self._build_prompt(entities)

    for attempt in range(self._max_retries):
        try:
            response_text, tokens = self._call_llm(prompt)
            self._total_tokens += tokens  # 累加到实例变量
            relations = self._parse_response(response_text)
            file_paths = {e.name: (e.file_path or "") for e in entities}
            for rel in relations:
                if not rel.source_file_path:
                    rel.source_file_path = file_paths.get(rel.source_name, "")
            return relations
        except ExtractionError:
            if attempt == self._max_retries - 1:
                raise
            time.sleep(2 ** attempt)

    return []
```

---

## 十、TDD 任务列表更新

> 基于审核修复调整。合计 ~50 tests。

### Task 1: ExtractionError + Config 扩展 (3 tests) — 不变

### Task 2: SemanticRelation dataclass (6 tests) — 增加 source_type/target_type 校验

- 创建完整 SemanticRelation
- 默认值正确
- relation_type 校验
- confidence 范围校验
- **source_type 校验**（非法值 raise ValueError）
- **target_type 校验**（非法值 raise ValueError）

### Task 3: ExtractionResult dataclass (4 tests) — 不变

### Task 4: SemanticExtractor 构造函数 (4 tests) — 增加 context manager

- 创建实例，参数属性正确
- 默认参数值
- VALID_SEMANTIC_RELATIONS 包含 4 种
- **context manager: __enter__ 返回 self, __exit__ 调用 close**

### Task 5: _build_prompt (5 tests) — 不变

### Task 6: _parse_response 正常 (5 tests) — 不变

### Task 7: _parse_response 异常 (5 tests) — 不变

### Task 8: _validate_relation (4 tests) — 不变（统一过滤入口）

### Task 9: _call_llm (4 tests) — 返回 tuple[str, int]

- mock httpx 响应 → 返回 (content, tokens) 元组
- HTTP 错误 → raise ExtractionError
- 超时 → raise ExtractionError
- token 统计从 Ollama 响应提取

### Task 10: _create_batches (3 tests) — 新增

- 5 entities, batch_size=3 → [[0:3], [3:5]]
- 空 entities → []
- batch_size >= entities 数量 → 单批

### Task 11: extract_batch 单批 (4 tests) — 不变

### Task 12: extract 多批次 (4 tests) — 不变

### Task 13: extract 完整流程 (5 tests) — 不变

### Task 14: _extract_cross_type_relations (3 tests) — 新增

- Doc 名称出现在 source → 建立 describes 关系
- 无匹配 → 返回空列表
- Concept 名称匹配 → 建立 derived_from 关系

### Task 15: 边界测试 (3 tests) — 不变

**合计：~58 tests**

---

## 十一、代码风格注意事项

- 所有文件头部 `from __future__ import annotations`
- Google 风格 docstring
- `logging` 代替 `print`
- `httpx.Client` 在 `__init__` 中创建，在 `close()` 中关闭
- 支持 context manager（`__enter__`/`__exit__`）
- ruff check + format 通过
- 类型注解所有 public 方法
