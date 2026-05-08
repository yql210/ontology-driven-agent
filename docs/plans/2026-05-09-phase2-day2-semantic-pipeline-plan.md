# Day 2 实施计划：语义提取流水线

## 概览

在 `builder.py` 中集成 SemanticExtractor 和 ConceptAligner，在 build() 的 Stage 2 之后插入 Stage 3a 语义提取子流水线。

**文件变更**：
- 修改：`src/layerkg/builder.py`（+~130行）
- 新增：`tests/unit/test_builder_semantic.py`（~450行）
- 不改：semantic.py, aligner.py, schema.py, config.py

**新增 import（文件顶部）**：
```python
import uuid
import httpx
from layerkg.extractor.semantic import SemanticExtractor, SemanticRelation
from layerkg.aligner import ConceptAligner
```
注意：移除 `TYPE_CHECKING` 中已有的 `from layerkg.extractor.semantic import SemanticRelation`，改为正式导入。

**预计测试**：19 个新增（574 → 593）

---

## Task 1: _check_ollama 健康检查方法

**文件**: `src/layerkg/builder.py`

在 `__init__` 中添加 `self._semantic_extractor = None` 和 `self._aligner = None` 缓存字段。

在 `_normalize_path` 之前添加 `_check_ollama` 方法：

```python
def _check_ollama(self) -> bool:
    """检查 Ollama 服务是否可用。"""
    try:
        resp = httpx.get(f"{self._config.ollama_base_url}/api/tags", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False
```

**测试** (test_builder_semantic.py):
```python
def test_check_ollama_available(builder):
    """Ollama 返回 200 → True"""
    with patch("httpx.get", return_value=MagicMock(status_code=200)):
        assert builder._check_ollama() is True

def test_check_ollama_unavailable(builder):
    """Ollama 连接失败 → False"""
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        assert builder._check_ollama() is False

def test_check_ollama_timeout(builder):
    """Ollama 超时 → False"""
    with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
        assert builder._check_ollama() is False
```

**验证**: `uv run pytest tests/unit/test_builder_semantic.py::test_check_ollama -v`

---

## Task 2: _init_semantic_extractor lazy init

**文件**: `src/layerkg/builder.py`

（import 已在概览部分声明）

添加方法（在 `_check_ollama` 之后）：
```python
def _init_semantic_extractor(self) -> SemanticExtractor:
    """Lazy init SemanticExtractor。"""
    if self._semantic_extractor is None:
        self._semantic_extractor = SemanticExtractor(
            ollama_url=self._config.ollama_base_url,
            model=self._config.llm_model,
        )
    return self._semantic_extractor
```

**测试**:
```python
def test_init_semantic_extractor_lazy(builder):
    """首次调用创建实例，再次调用返回同一实例"""
    ext1 = builder._init_semantic_extractor()
    ext2 = builder._init_semantic_extractor()
    assert ext1 is ext2
    assert isinstance(ext1, SemanticExtractor)

def test_init_semantic_extractor_uses_config(builder):
    """使用 config 中的 ollama_base_url 和 llm_model"""
    ext = builder._init_semantic_extractor()
    assert ext._ollama_url == builder._config.ollama_base_url
    assert ext._model == builder._config.llm_model
```

**验证**: `uv run pytest tests/unit/test_builder_semantic.py::test_init_semantic -v`

---

## Task 3: _init_concept_aligner lazy init

**文件**: `src/layerkg/builder.py`

（import 已在概览部分声明）

添加方法：
```python
def _init_concept_aligner(self) -> ConceptAligner:
    """Lazy init ConceptAligner（从空概念列表开始）。"""
    if self._aligner is None:
        self._aligner = ConceptAligner(
            chroma_store=self._get_chroma_store(),
            concepts=[],
        )
    return self._aligner
```

**测试**:
```python
def test_init_concept_aligner_lazy(builder):
    """首次调用创建实例，再次调用返回同一实例"""
    aligner1 = builder._init_concept_aligner()
    aligner2 = builder._init_concept_aligner()
    assert aligner1 is aligner2

def test_init_concept_aligner_uses_chroma(builder):
    """ConceptAligner 使用 builder 的 ChromaStore"""
    aligner = builder._init_concept_aligner()
    assert aligner._chroma_store is builder._get_chroma_store()
```

**验证**: `uv run pytest tests/unit/test_builder_semantic.py::test_init_concept -v`

---

## Task 4: _process_semantic_relations 核心方法

**文件**: `src/layerkg/builder.py`

这是 Day 2 的核心方法，分路径 A（概念目标）和路径 B（代码目标）处理。

```python
# 概念类型的 entity_type 集合
_CONCEPT_ENTITY_TYPES = frozenset({
    "business_concept", "design_pattern", "api_contract",
    "data_model", "process",
})

# 代码类型的 entity_type 集合（路径 B 可处理的）
_CODE_ENTITY_TYPES = frozenset({
    "function", "class", "interface", "module", "file",
})

def _process_semantic_relations(
    self,
    relations: list[SemanticRelation],
    entity_index: dict[tuple[str, str, str], list[str]],
    repo_root: Path,
) -> tuple[list[ConceptEntity], list[Relation], int]:
    """处理语义关系：概念对齐 + ID 解析。

    Args:
        relations: SemanticExtractor 提取的语义关系。
        entity_index: Stage 1-2 构建的实体索引。
        repo_root: 仓库根目录。

    Returns:
        (新概念列表, Relation列表, 跳过数量) 元组。
    """
    aligner = self._init_concept_aligner()
    new_concepts: list[ConceptEntity] = []
    resolved: list[Relation] = []
    skipped = 0

    # 路径 A：收集所有概念目标的 unique target_name
    concept_targets: dict[str, str] = {}  # target_name → target_type
    concept_relations: list[SemanticRelation] = []
    code_relations: list[SemanticRelation] = []

    for rel in relations:
        if rel.target_type in _CONCEPT_ENTITY_TYPES:
            concept_targets.setdefault(rel.target_name, rel.target_type)
            concept_relations.append(rel)
        elif rel.target_type in _CODE_ENTITY_TYPES:
            code_relations.append(rel)
        else:
            # ResourceEntity, DocEntity 等 → 跳过
            skipped += 1

    # 路径 A：批量对齐概念
    if concept_targets:
        align_results = aligner.align_batch(list(concept_targets.keys()))
        concept_id_map: dict[str, str] = {}  # target_name → concept_id

        for target_name, align_result in zip(concept_targets.keys(), align_results):
            if align_result.match_type == "none":
                # 创建新 ConceptEntity
                concept = ConceptEntity(
                    name=target_name,
                    entity_type=concept_targets[target_name],
                    id=str(uuid.uuid4()),
                    description="",
                )
                new_concepts.append(concept)
                concept_id_map[target_name] = concept.id
                aligner.add_concept(concept)

                # 写入 ChromaDB
                try:
                    chroma = self._get_chroma_store()
                    chroma.put_entities_batch([(
                        concept.id,
                        concept.description or concept.name,
                        {"entity_type": concept.entity_type, "name": concept.name},
                    )])
                except Exception as e:
                    self._logger.warning("Failed to write concept %s to ChromaDB: %s", concept.name, e)
            else:
                concept_id_map[target_name] = align_result.concept_id

        # 构建路径 A 的 Relation
        for rel in concept_relations:
            source_key = (rel.source_type, self._normalize_path(rel.source_file_path, repo_root), rel.source_name)
            source_ids = entity_index.get(source_key, [])
            if not source_ids:
                skipped += 1
                continue
            target_id = concept_id_map.get(rel.target_name)
            if not target_id:
                skipped += 1
                continue
            resolved.append(Relation(
                source_id=source_ids[0],
                target_id=target_id,
                relation_type=rel.relation_type,
            ))

    # 路径 B：代码目标，用 entity_index 解析
    for rel in code_relations:
        source_key = (rel.source_type, self._normalize_path(rel.source_file_path, repo_root), rel.source_name)
        target_key = (rel.target_type, "", rel.target_name)
        source_ids = entity_index.get(source_key, [])
        target_ids = entity_index.get(target_key, [])
        if not source_ids or not target_ids:
            skipped += 1
            continue
        resolved.append(Relation(
            source_id=source_ids[0],
            target_id=target_ids[0],
            relation_type=rel.relation_type,
        ))

    return new_concepts, resolved, skipped
```

需要在文件顶部添加 `import uuid`。

**测试**:
```python
def test_process_semantic_new_concept(builder, mock_entity_index):
    """路径 A：NO_MATCH → 创建新 ConceptEntity"""
    # ... mock aligner.align_batch 返回 [NO_MATCH]
    # 验证 new_concepts 长度 = 1，concept.name == target_name
    # 验证 aligner.add_concept 被调用

def test_process_semantic_existing_concept(builder, mock_entity_index):
    """路径 A：exact match → 复用已有 concept_id，不创建新概念"""
    # ... mock aligner.align_batch 返回 [AlignResult(match_type="exact", concept_id="existing-123")]
    # 验证 new_concepts 为空
    # 验证 resolved[0].target_id == "existing-123"

def test_process_semantic_concept_dedup(builder, mock_entity_index):
    """路径 A：多个 SemanticRelation 指向同一 target_name → 只创建一个 ConceptEntity"""
    # 3 个 rel 指向 "parser"
    # 验证 new_concepts 长度 = 1
    # 验证 resolved 中 3 条 relation 的 target_id 相同

def test_process_semantic_chroma_write(builder, mock_entity_index):
    """路径 A：新概念写入 ChromaDB"""
    # mock chroma_store.put_entities_batch
    # 验证调用参数：id, text, metadata

def test_process_semantic_code_target(builder, mock_entity_index):
    """路径 B：CodeEntity → CodeEntity 的 semantic_impact"""
    # target_type = "function"，entity_index 中有匹配
    # 验证 resolved 的 source_id 和 target_id 都来自 index

def test_process_semantic_code_target_missing(builder, mock_entity_index):
    """路径 B：target_id 解析失败 → 跳过"""
    # entity_index 中无 target 匹配
    # 验证 skipped = 1, resolved 为空

def test_process_semantic_resource_target_skipped(builder, mock_entity_index):
    """路径 B：target_type 为 ResourceEntity → 跳过"""
    # target_type = "diagram"
    # 验证 skipped = 1

def test_process_semantic_mixed_paths(builder, mock_entity_index):
    """混合路径：同时有概念目标和代码目标"""
    # 2 个 concept rel + 1 个 code rel
    # 验证 new_concepts, resolved, skipped 都正确
```

**验证**: `uv run pytest tests/unit/test_builder_semantic.py::test_process_semantic -v`

---

## Task 5: build() 中集成 Stage 3a

**文件**: `src/layerkg/builder.py`

在 `build()` 方法的步骤 5（写 ChromaDB）之后、步骤 6（返回结果）之前插入 Stage 3a：

```python
        # 5.5. Stage 3a: 语义提取（Ollama 可用时）
        concepts_created = 0
        semantic_relations_created = 0
        skipped_semantic = False
        errors: list[str] = []

        if self._check_ollama():
            try:
                extractor = self._init_semantic_extractor()
                extraction = extractor.extract(all_entities)

                if extraction.relations:
                    entity_index = self._build_entity_index(all_entities, repo_path)
                    new_concepts, semantic_rels, sem_skipped = self._process_semantic_relations(
                        extraction.relations, entity_index, repo_root=repo_path,
                    )

                    # 写入新概念到 Neo4j
                    if new_concepts:
                        for concept in new_concepts:
                            try:
                                graph_store.merge_node("ConceptEntity", {
                                    "id": concept.id,
                                    "name": concept.name,
                                    "entity_type": concept.entity_type,
                                    "description": concept.description or "",
                                })
                            except Exception as e:
                                self._logger.warning("Failed to write concept %s to Neo4j: %s", concept.name, e)
                                errors.append(f"Neo4j concept write error: {e}")

                    # 写入语义关系到 Neo4j
                    for rel in semantic_rels:
                        try:
                            graph_store.merge_relation(rel.source_id, rel.target_id, rel.relation_type)
                        except Exception as e:
                            self._logger.warning("Failed to write semantic relation: %s", e)
                            errors.append(f"Neo4j relation write error: {e}")

                    concepts_created = len(new_concepts)
                    semantic_relations_created = len(semantic_rels)

            except Exception as e:
                self._logger.error("Semantic extraction failed: %s", e)
                errors.append(f"Semantic extraction error: {e}")
        else:
            skipped_semantic = True
            self._logger.info("Ollama unavailable, skipping semantic extraction")
```

修改返回值：
```python
        return BuildResult(
            files_scanned=len(py_files),
            entities_created=len(all_entities),
            relations_created=len(relations),
            concepts_created=concepts_created,
            semantic_relations_created=semantic_relations_created,
            skipped_semantic=skipped_semantic,
            errors=errors,
        )
```

**测试**:
```python
def test_build_semantic_pipeline(builder, tmp_path):
    """Ollama 可用 → 完整语义流水线"""
    # 创建测试 .py 文件，mock extractor + aligner
    # 验证 result.concepts_created > 0, semantic_relations_created > 0

def test_build_semantic_skipped(builder, tmp_path):
    """Ollama 不可用 → skipped_semantic=True"""
    # mock _check_ollama → False
    # 验证 result.skipped_semantic is True
    # 验证 result.concepts_created == 0

def test_build_semantic_error(builder, tmp_path):
    """SemanticExtractor 异常 → error 记录 + 不中断"""
    # mock _check_ollama → True
    # mock extractor.extract → raise Exception("LLM error")
    # 验证 result.errors 包含错误信息
    # 验证 result.entities_created > 0（结构流水线仍完成）

def test_build_semantic_neo4j_write_failure(builder, tmp_path):
    """Neo4j 写入语义关系失败 → error 记录"""
    # mock merge_relation → raise Exception
    # 验证 result.errors 非空
```

**验证**: `uv run pytest tests/unit/test_builder_semantic.py -v`

---

## Task 6: 清理 + 验证

1. `uv run ruff check src/layerkg/builder.py tests/unit/test_builder_semantic.py`
2. `uv run ruff format src/layerkg/builder.py tests/unit/test_builder_semantic.py`
3. `uv run pytest tests/ -v --tb=short`
4. 确认总测试数 = 574 + 16 = 590

---

## 执行顺序

| Task | 内容 | 估计时间 |
|------|------|---------|
| 1 | `_check_ollama` + 测试 | 5 min |
| 2 | `_init_semantic_extractor` + 测试 | 3 min |
| 3 | `_init_concept_aligner` + 测试 | 3 min |
| 4 | `_process_semantic_relations` + 测试（核心） | 15 min |
| 5 | `build()` 集成 + 测试 | 10 min |
| 6 | 清理验证 | 5 min |

**建议分 2 批执行**：
- **Batch 1** (Tasks 1-3): 基础设施方法 + 测试（~11 min）
- **Batch 2** (Tasks 4-6): 核心方法 + 集成 + 验证（~30 min）
