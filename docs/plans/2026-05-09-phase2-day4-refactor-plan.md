# Phase 2 Day 4 实施计划：重构 build() 为5阶段流水线 + 错误降级

> 基于 Day 4 方案 v2（审核 9.0/10 通过）

## 目标

将 `build()` 方法从单体 120 行内联逻辑重构为 5 个独立 `_stage_*` 方法编排，增加错误降级和计时。

## 变更清单

### Step 1: BuildResult 新增 `aborted` 字段

**文件**: `src/layerkg/builder.py` (L44-73)

```python
# 在 BuildResult dataclass 中新增:
aborted: bool = False
```

**位置**: 在 `skipped_semantic` 后面（L68 之后）

**兼容性**: 无破坏性变更，默认 `False`，现有 `BuildResult(0, 0, 0)` 不受影响。注意 `test_cli.py:402` 使用位置参数 `BuildResult(0, 0, 0)` — 因为 `aborted` 在 `skipped_semantic` 之后且有默认值，不需要修改。

### Step 2: 新增 `_stage_parse()` 方法

**文件**: `src/layerkg/builder.py`，新增方法（在 `_scan_python_files` 之前）

**逻辑**（从 build() L133-156 提取）:
```python
def _stage_parse(self, repo_path: Path) -> tuple[list[CodeEntity], list[Relation], int]:
    """Stage 1: 扫描 + 解析文件 + 提取结构关系。
    
    Returns:
        (all_entities, relations, files_scanned) 三元组。
    """
    self._repo_root = repo_path
    py_files = self._scan_python_files(repo_path)
    self._logger.info("Scanned %d Python files", len(py_files))

    all_entities: list[CodeEntity] = []
    skipped_files = 0
    for file_path in py_files:
        result = self._parser.parse_file(file_path)
        if result.error:
            self._logger.warning("Skip %s: %s", file_path, result.error)
            skipped_files += 1
            continue
        all_entities.extend(result.entities)
        self._extractor.add_parse_result(result.entities, result.relations)

    self._logger.info("Parsed %d entities, skipped %d files", len(all_entities), skipped_files)

    relations = self._extractor.resolve(all_entities)
    self._logger.info("Resolved %d relations", len(relations))

    return all_entities, relations, len(py_files)
```

### Step 3: 新增 `_stage_write_structural()` 方法

**文件**: `src/layerkg/builder.py`，新增方法

**逻辑**（从 build() L158-166 提取）:
```python
def _stage_write_structural(
    self, 
    all_entities: list[CodeEntity], 
    relations: list[Relation],
) -> Neo4jGraphStore:
    """Stage 2: 写入结构实体和关系到 Neo4j。
    
    关键路径：失败则抛出 RuntimeError 中止构建。
    
    Args:
        all_entities: 代码实体列表。
        relations: 关系列表。
        
    Returns:
        Neo4jGraphStore 实例。
        
    Raises:
        RuntimeError: 写入失败时。
    """
    graph_store = self._get_graph_store()
    graph_store.ensure_constraints()

    try:
        for entity in all_entities:
            graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))
        for rel in relations:
            graph_store.merge_relation(rel.source_id, rel.target_id, rel.relation_type)
    except Exception as e:
        raise RuntimeError(f"Stage 2 structural write failed: {e}") from e

    return graph_store
```

### Step 4: 新增 `_stage_semantic()` 方法

**文件**: `src/layerkg/builder.py`，新增方法

**逻辑**（从 build() L168-219 提取）:
```python
def _stage_semantic(
    self,
    all_entities: list[CodeEntity],
    graph_store: Neo4jGraphStore,
    repo_path: Path,
) -> tuple[int, int, bool, list[str], list[ConceptEntity]]:
    """Stage 3: 语义提取 + 概念对齐 + 写入 Neo4j。
    
    可降级：失败不中止构建，跳过并记录错误。
    
    Args:
        all_entities: 代码实体列表。
        graph_store: Neo4j 存储实例。
        repo_path: 仓库根目录。
        
    Returns:
        (concepts_created, semantic_relations_created, skipped_semantic, errors, new_concepts) 元组。
    """
    concepts_created = 0
    semantic_relations_created = 0
    skipped_semantic = False
    errors: list[str] = []
    new_concepts: list[ConceptEntity] = []

    if self._check_ollama():
        try:
            extractor = self._init_semantic_extractor()
            extraction = extractor.extract(all_entities)
            if extraction.relations:
                entity_index = self._build_entity_index(all_entities, repo_path)
                new_concepts, semantic_rels, _sem_skipped = self._process_semantic_relations(
                    extraction.relations, entity_index, repo_root=repo_path
                )
                # 写入新概念到 Neo4j
                if new_concepts:
                    for concept in new_concepts:
                        try:
                            graph_store.merge_node(
                                "ConceptEntity",
                                {"id": concept.id, "name": concept.name,
                                 "entity_type": concept.entity_type,
                                 "description": concept.description or ""},
                            )
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

    return concepts_created, semantic_relations_created, skipped_semantic, errors, new_concepts
```

### Step 5: 重写 `build()` 为5阶段编排

**文件**: `src/layerkg/builder.py`，替换 L124-241

```python
def build(self, repo_path: Path) -> BuildResult:
    """全量构建：5阶段流水线编排。

    阶段:
        1. 解析: 扫描 + AST 解析 + 结构关系提取
        2. 结构写入: 实体 + 关系 → Neo4j (关键路径)
        3. 语义提取: LLM 概念 + 语义关系 (可降级)
        4. 模块聚类: 社区检测 → ModuleEntity (可降级)
        5. 向量写入: 统一 ChromaDB (可降级)

    Args:
        repo_path: 仓库根目录路径。

    Returns:
        构建结果统计。
    """
    import time
    t0 = time.monotonic()
    all_errors: list[str] = []
    aborted = False

    # Stage 1: 解析
    all_entities, relations, files_scanned = self._stage_parse(repo_path)

    # Stage 2: 结构写入（关键路径）
    try:
        graph_store = self._stage_write_structural(all_entities, relations)
    except RuntimeError as e:
        all_errors.append(str(e))
        aborted = True
        elapsed = (time.monotonic() - t0) * 1000
        return BuildResult(
            files_scanned=files_scanned,
            entities_created=0,
            relations_created=0,
            aborted=aborted,
            elapsed_ms=elapsed,
            errors=all_errors,
        )

    # Stage 3: 语义提取（可降级）
    concepts_created, semantic_rels_created, skipped_semantic, sem_errors, new_concepts = \
        self._stage_semantic(all_entities, graph_store, repo_path)
    all_errors.extend(sem_errors)

    # Stage 4: 模块聚类（可降级）
    try:
        clusters_count, clusters = self._detect_and_write_modules(graph_store)
    except Exception as e:
        self._logger.warning("Module clustering failed: %s", e)
        all_errors.append(f"Module clustering error: {e}")
        clusters_count = 0
        clusters = []

    # Stage 5: 向量写入（可降级）
    try:
        self._write_all_vectors(all_entities, new_concepts, clusters)
    except Exception as e:
        self._logger.warning("Vector write failed: %s", e)
        all_errors.append(f"Vector write error: {e}")

    elapsed = (time.monotonic() - t0) * 1000
    return BuildResult(
        files_scanned=files_scanned,
        entities_created=len(all_entities),
        relations_created=len(relations),
        concepts_created=concepts_created,
        semantic_relations_created=semantic_rels_created,
        modules_created=clusters_count,
        skipped_semantic=skipped_semantic,
        aborted=aborted,
        elapsed_ms=elapsed,
        errors=all_errors,
    )
```

**⚠️ 注意**: `files_scanned` 原来是 `len(py_files)`，重构后 `py_files` 只在 `_stage_parse` 内部。需要从 `_stage_parse` 返回 `files_scanned` 或在 `build()` 里单独扫描。**方案：修改 `_stage_parse` 返回值包含 `files_scanned`**。

修正：`_stage_parse` 返回 `tuple[list[CodeEntity], list[Relation], int]`，第三个值是 `files_scanned`。

### Step 6: 新增测试

**文件**: `tests/unit/test_builder_stages.py`（新建）

6 个测试用例：

| # | 测试名 | 目的 |
|---|--------|------|
| 1 | `test_stage_parse_returns_entities_and_relations` | 验证 `_stage_parse` 正常工作，返回三元组 |
| 2 | `test_stage_write_structural_raises_on_neo4j_failure` | 验证 Stage 2 Neo4j 失败抛 RuntimeError |
| 3 | `test_stage_semantic_degraded_when_ollama_down` | 验证 Stage 3 Ollama 不可用时优雅降级 |
| 4 | `test_build_aborted_on_stage2_failure` | 验证 Stage 2 失败 → `aborted=True` |
| 5 | `test_build_elapsed_ms_positive` | 验证 `elapsed_ms > 0` |
| 6 | `test_build_full_pipeline_succeeds` | 端到端正常路径 |
| 7 | `test_build_continues_after_stage4_failure` | Stage 4 失败后继续 Stage 5，结果包含错误但不中止 |
| 8 | `test_build_accumulates_errors_from_stages_3_4_5` | 多阶段降级错误累积到 errors 列表 |

**Mock 策略**:
- `Neo4jGraphStore`: mock `merge_node`, `merge_relation`, `ensure_constraints`
- `ChromaStore`: mock `put_entities_batch`, `count`
- `Ollama`: mock `_check_ollama` 返回 `False`（跳过语义）
- `Parser`: mock `parse_file` 返回预设实体
- `httpx.get`: mock 用于 `_check_ollama`

## 执行顺序

1. **改 BuildResult**: 加 `aborted: bool = False`（无破坏性变更）
2. **写测试** (TDD RED): 创建 `test_builder_stages.py`，8 个测试先全红
3. **加 `_stage_parse()`**: 从 build() 提取 L133-156，返回三元组
4. **加 `_stage_write_structural()`**: 从 build() 提取 L158-166
5. **加 `_stage_semantic()`**: 从 build() 提取 L168-219
6. **重写 `build()`**: 5阶段编排 + 计时
7. **跑全量测试**: 606 + 8 = 614 tests all pass
8. **ruff check**: 零警告
9. **Git commit + push**
10. **思源笔记更新**

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| `files_scanned` 值变化 | `_stage_parse` 返回三元组包含 `files_scanned` |
| `test_cli.py:402` 兼容性 | `aborted` 在已有字段之后且有默认值，不影响位置参数 |
| 重构引入回归 | 先写测试，后改代码 |
| `_detect_and_write_modules` 已有 try/except | build() 中保留外层 try/except 作为防御性编程，确保未来内部方法变更时仍安全 |
