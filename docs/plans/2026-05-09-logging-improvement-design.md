# 日志改进方案

> 日期: 2026-05-09
> 目标: 解决构建管线日志"最慢的阶段最沉默"的问题，让长时间运行的构建过程可观测

## 1. 问题背景

第四次全量构建耗时 **146 分钟**，有效日志只有 Build Report 最后几行。中间全是 httpx 的 `HTTP Request: POST` 重复噪声。

核心矛盾：**Stage 4 语义提取（LLM 调用，占总耗时 80%+）没有任何中间进度输出**。958 个实体分 48 批调 LLM，每批约 2-3 分钟，用户完全无法判断：
- 构建是卡死了还是在正常跑？
- 当前进度到哪里了？
- 哪些 batch 成功/失败了？
- 还要等多久？

### 日志现状盘点

| 模块 | 日志情况 | 问题 |
|------|---------|------|
| `builder.py` | 有 `logging`，Stage 1 有 4 条 INFO | Stage 2-5 缺少阶段分界日志 |
| `semantic.py` | **零日志** | 48 batch LLM 调用全程静默 |
| `module_clustering.py` | 1 处 INFO | 聚类过程无进度 |
| `chroma_store.py` | 仅 DEBUG 级别 | INFO 级别看不到任何向量写入 |
| `neo4j_store.py` | 仅 DEBUG 级别 | INFO 级别看不到写入进度 |
| `cli.py` | `--verbose-build` 只输出最终报告 | 无实时进度 |
| httpx | 每次请求一条 INFO | 噪声日志，淹没有用信息 |

## 2. 设计方案

### 2.1 httpx 日志降噪（全局）

**问题**：httpx 默认 INFO 级别输出每次 HTTP 请求，构建期间产生数百条 `HTTP Request: POST http://...` 噪声。Neo4j driver 也输出大量 constraint 通知。

**方案**：在 `cli.py` 的 `logging.basicConfig` 后，将 httpx/httpcore/neo4j 的日志级别设为 WARNING。

**修改位置 1**：`src/layerkg/cli.py` — `main()` 函数中 `logging.basicConfig` 之后

```python
# 现有
logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

# 新增：降噪外部库日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("neo4j").setLevel(logging.WARNING)
```

> 注意：`neo4j_store.py` 自身的 `logger = logging.getLogger(__name__)` 输出的是 `layerkg.neo4j_store` 命名空间，
> 不受 `neo4j` 命名空间降噪影响，所以只需在 CLI 层统一处理。

### 2.2 Builder 阶段生命周期日志

**问题**：`build()` 编排 5 个 Stage，但只有 Stage 1 有 "Scanned" 日志，后续阶段全部静默。各 `_stage_*` 方法分散在不同位置。

**方案**：在 `build()` 主方法中加阶段分界线，在各 `_stage_*` 方法内部加汇总日志（因为统计信息在方法内部可见）。

**修改位置**：`src/layerkg/builder.py`

#### `build()` 方法（行 348-452）— 加阶段分界线

```python
def build(self, repo_path, ...):
    ...
    # Stage 1: 解析
    self._logger.info("═══ Stage 1/5: Parse ═══")
    all_entities, doc_entities, relations, files_scanned = self._stage_parse(repo_path)
    
    # Stage 2: 结构写入
    self._logger.info("═══ Stage 2/5: Structural Write ═══")
    ...
    
    # Stage 2.5: 文档→代码关联
    self._logger.info("═══ Stage 2.5/5: Doc-Code Link ═══")
    ...
    self._logger.info("═══ Stage 2.5/5 complete: %d DESCRIBES relations ═══", len(describes_rels))
    
    # Stage 3: 语义提取
    self._logger.info("═══ Stage 3/5: Semantic Extraction (may take a while...) ═══")
    ...
    self._logger.info("═══ Stage 3/5 complete: %d concepts, %d semantic relations ═══", concepts_created, semantic_rels_created)
    
    # Stage 4: 模块聚类
    self._logger.info("═══ Stage 4/5: Module Clustering ═══")
    ...
    self._logger.info("═══ Stage 4/5 complete: %d modules ═══", clusters_count)
    
    # Stage 5: 向量写入
    self._logger.info("═══ Stage 5/5: Vector Index ═══")
    ...
```

#### `_stage_semantic()` 方法末尾（行 340 附近）— 加汇总

```python
# 在 return 之前加：
self._logger.info(
    "[Semantic] Stage complete: %d concepts, %d relations, %d errors",
    concepts_created, semantic_relations_created, len(errors),
)
return concepts_created, semantic_relations_created, skipped_semantic, errors, new_concepts
```

### 2.3 SemanticExtractor batch 进度日志（核心改进）

**问题**：`extract()` 循环 48 个 batch，每批调一次 LLM，全程零日志。

**方案**：在 `SemanticExtractor` 中引入 `logging`，每批输出进度。耗时在调用方 `extract()` 中计算（包含重试等待时间，这是合理的——用户关心的是总等待时间）。

**修改位置**：`src/layerkg/extractor/semantic.py`

**前置修改**：添加 `import logging`（文件头部）和 `self._logger = logging.getLogger(__name__)`（`__init__` 中）

**在 `extract()` 方法中**：
```python
def extract(self, entities, ...):
    ...
    batches = self._create_batches(entities)
    total_batches = len(batches)
    self._logger.info("[Semantic] Starting: %d entities in %d batches", len(entities), total_batches)
    
    for i, batch in enumerate(batches, 1):
        t_batch = time.time()  # ← 耗时在调用方计算（含重试等待）
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
    
    self._logger.info(
        "[Semantic] Complete: %d relations from %d batches, %d tokens total",
        len(all_relations), total_batches, total_tokens,
    )
```

**在 `extract_batch()` 的重试路径中**（行 260-265）：
```python
except ExtractionError:
    if attempt < self._max_retries - 1:
        self._logger.warning("[Semantic] Batch retry %d/%d", attempt + 2, self._max_retries)
        time.sleep(2 ** attempt)
    else:
        raise
```

### 2.4 Neo4j 批量写入进度

**问题**：`_stage_write_structural()` 循环写入 958 个 CodeEntity + 714 个 DocEntity + 数百个 Relation，但无进度输出。

**方案**：每写入 200 个实体输出一条进度日志。

**修改位置**：`src/layerkg/builder.py` 的 `_stage_write_structural()`

**当前代码**（行 253）：`for entity in all_entities:`

```python
# 改为 enumerate
for i, entity in enumerate(all_entities, 1):
    graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))
    if i % 200 == 0:
        self._logger.info("[Neo4j] Merged %d/%d CodeEntities", i, len(all_entities))
self._logger.info("[Neo4j] Merged %d CodeEntities complete", len(all_entities))
```

对 `doc_entities` 循环同理（行 255），每 200 条输出一次。

### 2.5 ChromaStore 向量写入进度

**问题**：`put_entities_batch()` 写入上千条向量，仅 DEBUG 级别有日志。

**方案**：在 `_write_all_vectors()` 中加进度日志。

**修改位置**：`src/layerkg/builder.py` 的 `_write_all_vectors()`

```python
def _write_all_vectors(self, ...):
    ...
    if items:
        self._logger.info("[Vector] Writing %d items to ChromaDB...", len(items))
        chroma_store.put_entities_batch(items)
        self._logger.info("[Vector] Wrote %d vectors complete", len(items))
```

### 2.6 ModuleClustering 进度

**问题**：`detect_modules()` 内部过程无日志。

**方案**：在 `detect_modules()` 和 `save_modules()` 加关键步骤日志。

**修改位置**：`src/layerkg/module_clustering.py`

```python
def detect_modules(self):
    self._logger.info("[Clustering] Starting module detection...")
    ...
    self._logger.info("[Clustering] Detected %d modules", len(clusters))
    return clusters

def save_modules(self, clusters):
    ...
    self._logger.info("[Clustering] Saved %d modules to Neo4j", count)
```

## 3. 不改什么

- **不引入新依赖**（不用 tqdm/rich）— 保持依赖精简，日志全部用标准 `logging`
- **不改日志格式** — 继续用 `%(levelname)s: %(message)s`
- **不改 CLI 接口** — `--verbose-build` 行为不变，只是日志内容更丰富
- **不改测试** — 纯日志改动不影响业务逻辑，不需要新测试
- **不改 `incremental_updater.py`** — 增量更新的日志不在本次范围内

## 4. 预期效果

改进后的一次全量构建日志输出示例：

```
INFO: ═══ Stage 1/5: Parse ═══
INFO: Scanned 48 Python files, 36 doc files
INFO: Parsed 958 entities, skipped 0 files
INFO: Parsed 714 doc entities
INFO: Resolved 917 relations
INFO: ═══ Stage 2/5: Structural Write ═══
INFO: [Neo4j] Merged 200/958 CodeEntities
INFO: [Neo4j] Merged 400/958 CodeEntities
...
INFO: [Neo4j] Merged 958 CodeEntities complete
INFO: [Neo4j] Merged 714 DocEntities complete
INFO: ═══ Stage 2.5/5: Doc-Code Link ═══
INFO: ═══ Stage 2.5 complete: 853 DESCRIBES relations (2.1s) ═══
INFO: ═══ Stage 3/5: Semantic Extraction (may take a while...) ═══
INFO: [Semantic] Starting: 958 entities in 48 batches
INFO: [Semantic] Batch 1/48: 3 relations, 1200 tokens (145.2s)
INFO: [Semantic] Batch 2/48: 2 relations, 980 tokens (138.7s)
...
INFO: [Semantic] Complete: 62 relations from 48 batches, 52340 tokens total
INFO: ═══ Stage 3/5 complete: 27 concepts, 33 semantic relations (0 errors) ═══
INFO: ═══ Stage 4/5: Module Clustering ═══
INFO: [Clustering] Detected 48 modules
INFO: [Clustering] Saved 48 modules to Neo4j
INFO: ═══ Stage 5/5: Vector Index ═══
INFO: [Vector] Writing 1720 items to ChromaDB...
INFO: [Vector] Wrote 1720 vectors complete
```

对比改进前：146 分钟只有 12 行日志（含噪声）→ 改进后每阶段有清晰分界线，最关键的 LLM 批处理每 2-3 分钟输出一条进度。

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| httpx 日志降级后可能漏掉有用的连接错误 | 只降到 WARNING，连接错误/超时仍会输出 |
| Neo4j 每 200 条一次日志可能在大项目中太多 | 200 是合理间隔（958 实体只出 5 条），可调 |
| SemanticExtractor 引入 logger 需要传参或用全局 logger | 用模块级 `logging.getLogger(__name__)` 即可，不需要改构造函数签名 |

## 6. 涉及文件

| 文件 | 改动量 | 改动类型 |
|------|--------|---------|
| `src/layerkg/cli.py` | +3 行 | httpx/httpcore/neo4j 降噪（统一在 CLI 层处理） |
| `src/layerkg/extractor/semantic.py` | +15 行 | `import logging` + logger 初始化 + batch 进度日志（核心） |
| `src/layerkg/builder.py` | +25 行 | 阶段分界线 + `_stage_semantic` 汇总 + Neo4j/向量进度 |
| `src/layerkg/module_clustering.py` | +3 行 | 聚类进度 |

**总改动量**：~46 行纯日志代码，不影响任何业务逻辑。
