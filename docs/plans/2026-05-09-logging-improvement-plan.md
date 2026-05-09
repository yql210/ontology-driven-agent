# 日志改进实施计划

> 方案文档: `docs/plans/2026-05-09-logging-improvement-design.md`
> 总改动量: ~46 行纯日志代码，不影响任何业务逻辑

## Task 1: cli.py — 外部库日志降噪

**文件**: `src/layerkg/cli.py`
**位置**: `main()` 函数，第 18 行 `logging.basicConfig(...)` 之后

```python
# 第 18 行现有:
logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

# 新增 3 行:
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("neo4j").setLevel(logging.WARNING)
```

**验证**: grep 确认 3 个 setLevel 调用存在

---

## Task 2: semantic.py — 添加 logging + batch 进度日志（核心）

**文件**: `src/layerkg/extractor/semantic.py`

### 2a. 添加 import（第 5 行后）

```python
import time  # 已存在

import logging  # ← 新增
```

### 2b. SemanticExtractor.__init__ 中初始化 logger

在 `__init__` 方法中（大约第 110-125 行），末尾加一行:
```python
self._logger = logging.getLogger(__name__)
```

### 2c. extract() 方法 — 改循环为 enumerate + 加进度日志

**当前代码**（第 155-167 行）:
```python
batches = self._create_batches(entities)

# 2. 逐批调用 LLM
for batch in batches:
    try:
        batch_relations, batch_tokens = self.extract_batch(batch)
        all_relations.extend(batch_relations)
        total_tokens += batch_tokens
    except ExtractionError as e:
        errors.append(str(e))
    finally:
        llm_calls += 1
```

**改为**:
```python
batches = self._create_batches(entities)
total_batches = len(batches)
self._logger.info("[Semantic] Starting: %d entities in %d batches", len(entities), total_batches)

# 2. 逐批调用 LLM
for i, batch in enumerate(batches, 1):
    t_batch = time.time()
    try:
        batch_relations, batch_tokens = self.extract_batch(batch)
        all_relations.extend(batch_relations)
        total_tokens += batch_tokens
        elapsed_batch = time.time() - t_batch
        self._logger.info(
            "[Semantic] Batch %d/%d: %d relations, %d tokens (%.1fs)",
            i, total_batches, len(batch_relations), batch_tokens, elapsed_batch,
        )
    except ExtractionError as e:
        errors.append(str(e))
        self._logger.warning("[Semantic] Batch %d/%d FAILED: %s", i, total_batches, e)
    finally:
        llm_calls += 1
```

### 2d. extract() 方法末尾 — 加汇总日志

**当前代码**（第 177-184 行，return 之前）:
```python
return ExtractionResult(...)
```

**在 return 前加**:
```python
self._logger.info(
    "[Semantic] Complete: %d relations from %d batches, %d tokens total",
    len(all_relations), total_batches, total_tokens,
)
```

### 2e. extract_batch() — 重试路径加 warning

**当前代码**（第 260-265 行）:
```python
except ExtractionError:
    if attempt == self._max_retries - 1:
        raise
    import time
    time.sleep(2**attempt)
```

**改为**:
```python
except ExtractionError:
    if attempt == self._max_retries - 1:
        raise
    self._logger.warning("[Semantic] Batch retry %d/%d", attempt + 2, self._max_retries)
    import time
    time.sleep(2**attempt)
```

**验证**: grep `self._logger` semantic.py 确认至少 5 处调用

---

## Task 3: builder.py — 阶段分界线 + Stage 汇总 + Neo4j/向量进度

**文件**: `src/layerkg/builder.py`

### 3a. build() 方法 — 每个阶段调用前加分界线

在 `build()` 方法中（第 378-434 行），每个阶段调用前加一行:

```python
# 第 378 行前加:
self._logger.info("═══ Stage 1/5: Parse ═══")
all_entities, doc_entities, relations, files_scanned = self._stage_parse(repo_path)

# 第 381 行前加:
self._logger.info("═══ Stage 2/5: Structural Write ═══")

# 第 398 行前加:
self._logger.info("═══ Stage 2.5/5: Doc-Code Link ═══")
entity_index = self._build_entity_index(all_entities, repo_path)
describes_rels = self._link_docs_to_code(doc_entities, entity_index)
# ... 现有循环 ...
# 循环后加:
self._logger.info("═══ Stage 2.5/5 complete: %d DESCRIBES relations ═══", len(describes_rels))

# 第 406 行前加:
self._logger.info("═══ Stage 3/5: Semantic Extraction (may take a while...) ═══")

# 第 414-416 行后（semantic 完成后）加:
self._logger.info("═══ Stage 3/5 complete: %d concepts, %d semantic relations ═══", concepts_created, semantic_rels_created)

# 第 419 行前加:
self._logger.info("═══ Stage 4/5: Module Clustering ═══")

# 第 430 行后（clustering 完成后，try/except 之后）加:
self._logger.info("═══ Stage 4/5 complete: %d modules ═══", clusters_count)

# 第 432 行前加:
self._logger.info("═══ Stage 5/5: Vector Index ═══")
```

### 3b. _stage_semantic() 方法末尾 — 加汇总日志

**当前代码**（第 346 行 return 之前）:
```python
return concepts_created, semantic_relations_created, skipped_semantic, errors, new_concepts
```

**在 return 前加**:
```python
self._logger.info(
    "[Semantic] Stage complete: %d concepts, %d relations, %d errors",
    concepts_created, semantic_relations_created, len(errors),
)
```

### 3c. _stage_write_structural() — Neo4j 写入进度

**当前代码**（第 253-264 行）:
```python
for entity in all_entities:
    graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))
for doc in doc_entities:
    graph_store.merge_node("DocEntity", self._doc_entity_to_dict(doc))
for rel in relations:
    graph_store.merge_relation(...)
```

**改为**:
```python
for i, entity in enumerate(all_entities, 1):
    graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))
    if i % 200 == 0:
        self._logger.info("[Neo4j] Merged %d/%d CodeEntities", i, len(all_entities))
self._logger.info("[Neo4j] Merged %d CodeEntities complete", len(all_entities))

for i, doc in enumerate(doc_entities, 1):
    graph_store.merge_node("DocEntity", self._doc_entity_to_dict(doc))
    if i % 200 == 0:
        self._logger.info("[Neo4j] Merged %d/%d DocEntities", i, len(doc_entities))
self._logger.info("[Neo4j] Merged %d DocEntities complete", len(doc_entities))

for rel in relations:
    graph_store.merge_relation(
        rel.source_id, rel.target_id, rel.relation_type,
        source_label="CodeEntity", target_label="CodeEntity",
    )
self._logger.info("[Neo4j] Wrote %d structural relations", len(relations))
```

### 3d. _write_all_vectors() — 向量写入进度

**当前代码**（第 779-780 行）:
```python
if items:
    chroma_store.put_entities_batch(items)
```

**改为**:
```python
if items:
    self._logger.info("[Vector] Writing %d items to ChromaDB...", len(items))
    chroma_store.put_entities_batch(items)
    self._logger.info("[Vector] Wrote %d vectors complete", len(items))
```

**验证**: grep `self._logger.info` builder.py 确认新增的日志调用

---

## Task 4: module_clustering.py — 聚类进度

**文件**: `src/layerkg/module_clustering.py`

### 4a. detect_modules() — 加完成日志

`detect_modules()` 已有开头的诊断日志（第 282 行）。只需在 return 前加:

```python
# 第 312 行 return clusters 前加:
self._logger.info("[Clustering] Detected %d modules", len(clusters))
return clusters
```

### 4b. save_modules() — 加完成日志

**当前代码**（第 314 行附近），在 return saved 之前加:
```python
self._logger.info("[Clustering] Saved %d modules to Neo4j", saved)
return saved
```

**验证**: grep `_logger.info` module_clustering.py 确认新增调用

---

## Task 5: ruff + 测试

```bash
cd /opt/data/workspace/ontology-driven-agent
ruff check src/layerkg/cli.py src/layerkg/extractor/semantic.py src/layerkg/builder.py src/layerkg/module_clustering.py
uv run pytest tests/ -q --tb=short
```

预期：ruff clean，679 tests 全通过（纯日志改动不影响业务逻辑）。

---

## 执行顺序

Task 1 → Task 2 → Task 3 → Task 4 → Task 5

Task 1 独立，Task 2 核心最大，Task 3 次之，Task 4 最小，Task 5 验证。
