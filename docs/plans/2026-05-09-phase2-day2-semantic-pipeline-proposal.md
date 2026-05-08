# Day 2 方案 v2：语义提取流水线（SemanticExtractor + ConceptAligner 集成）

## 问题陈述

当前 `build()` 方法只完成了结构流水线（Stage 1-2：扫描→解析→结构关系→写入 Neo4j/Chroma）。
语义关系（semantic_impact, describes, illustrates, derived_from）尚未集成到构建流程中。
Phase 1 已独立实现了 SemanticExtractor 和 ConceptAligner，但它们没有被串联到 Builder 中。

## 目标

将 SemanticExtractor 和 ConceptAligner 集成到 `LayerKGBuilder.build()` 中，形成完整的 Stage 3（语义提取）子流水线。
当 Ollama 不可用时优雅降级（skip），不影响已有的结构流水线。

## 方案设计

### 新增方法（均在 `src/layerkg/builder.py`）

#### 1. `_check_ollama() → bool`
- 用 httpx GET `ollama_url/api/tags`，timeout=5s
- 成功返回 True，任何异常返回 False

#### 2. `_init_semantic_extractor() → SemanticExtractor`
- lazy init，使用 `config.ollama_base_url` 和 `config.llm_model`
- 缓存到 `self._semantic_extractor`

#### 3. `_init_concept_aligner() → ConceptAligner`
- lazy init，需要 ChromaStore 实例
- 初始概念列表从空开始（首次构建场景）
- 缓存到 `self._aligner`

#### 4. `_process_semantic_relations(relations: list[SemanticRelation], entity_index: dict, repo_root: Path) → tuple[list[ConceptEntity], list[Relation]]`

**核心处理逻辑（分两条路径）：**

**路径 A：target_type 指向 ConceptEntity（target_type ∈ {"business_concept", "design_pattern", "api_contract", "data_model", "process"}）**
1. 收集所有 unique `target_name`（按 `(target_type, target_name)` 去重）
2. 调用 `aligner.align_batch(target_names)` 批量对齐
3. 对 match_type=="none" 的术语：
   - 创建新 ConceptEntity（entity_type=rel.target_type, id=uuid4）
   - 调用 `aligner.add_concept()` 加入内存索引
   - **写入 ChromaDB**：调用 `self._get_chroma_store().put_entities_batch([concept.to_dict()])` 或等价方法，确保后续 vector_match 可用
   - 将新概念加入 new_concepts 列表
4. source_id 从 entity_index 解析：`(rel.source_type, normalize(rel.source_file_path), rel.source_name)`
5. target_id = align_result.concept_id（对齐结果或新创建的）
6. 构建 Relation 对象

**路径 B：target_type 指向 CodeEntity / DocEntity（target_type ∈ {"function", "class", "interface", "module", "file", "readme", ...}）**
1. source_id 和 target_id **都从 entity_index 解析**
2. source_key = `(rel.source_type, normalize(rel.source_file_path), rel.source_name)`
3. target_key = `(rel.target_type, "", rel.target_name)`（语义关系 target 通常无路径）
4. 如果 target_ids 为空，跳过该关系（记录 skipped）
5. 构建 Relation 对象

**边界说明**：当前 entity_index 只包含 CodeEntity（Stage 2 写入的）。
- ResourceEntity（image/diagram/pdf/config/schema_file/log）**不在 index 中**，遇到时直接跳过
- DocEntity 当前 build() 不扫描文档文件，遇到 target 为 DocEntity 的语义关系也跳过
- 跳过的关系计入 skipped 计数，不产生 Relation
- 这些边界将在 Phase 2 Day 5（文档摄入）和后续 Phase 中逐步补全

**返回**：`(new_concepts, relations)`

### 实现注意事项

**entity_index 构建细节**：
- `_build_entity_index(entities, repo_root)` 已在 Day 1 实现，key 格式 = `(entity_type, normalized_file_path, name)`
- ConceptEntity 无 file_path，normalize 返回 `""`，key = `(entity_type, "", name)`
- 调用 `_process_semantic_relations` 时，需传入 `build()` 中 Stage 1-2 收集的全部 CodeEntity 列表构建的 index
- `_resolve_semantic_names()` 会同时解析 source 和 target，但对于路径 A（概念目标），target 不在 index 中（是新创建的概念），所以路径 A 不能依赖 `_resolve_semantic_names`，需在 `_process_semantic_relations` 内部直接构建 Relation

**ChromaDB 写入接口**：
- `ChromaStore.put_entities_batch(items: list[tuple[str, str, dict[str, Any]]])`
- 参数格式：`[(entity_id, text, metadata), ...]`
- ConceptEntity 写入时：id=concept.id, text=concept.description or concept.name, metadata={"entity_type": concept.entity_type, "name": concept.name}

### build() 中的集成位置

在现有 Stage 2（写入 CodeEntity + 结构关系到 Neo4j）之后插入：

```
Stage 1: 扫描+解析 → CodeEntity 列表        [已有]
Stage 2: 结构关系提取+写入 Neo4j/Chroma     [已有]
Stage 3a: 语义提取 ← Day 2 新增
  ├─ 1. 检查 Ollama 可用性 (_check_ollama)
  ├─ 2. 可用 → SemanticExtractor.extract(all_code_entities)
  ├─ 3. 构建 entity_index（包含所有已扫描实体）
  ├─ 4. ConceptAligner 对齐 → 分路径 A/B 处理
  ├─ 5. _resolve_semantic_names() → 最终 Relation 列表
  ├─ 6. 写入新 ConceptEntity 到 Neo4j + ChromaDB
  ├─ 7. 写入语义 Relation 到 Neo4j
  └─ 8. 更新 BuildResult 计数
```

### 降级策略（完善版）

| 异常场景 | 处理方式 |
|---------|---------|
| Ollama 不可用 | `skipped_semantic=True`，跳过整个 Stage 3a |
| SemanticExtractor.extract() 异常 | 捕获，追加到 `result.errors`，不中断 |
| 单个 SemanticRelation 处理失败 | 跳过该关系，记录 skipped 计数 |
| ConceptAligner.align() 异常 | 跳过该概念，记录到 errors |
| Ollama 中途断连（SemanticExtractor 已部分返回） | 已提取的语义关系正常处理，异常捕获后不再重试 |
| Neo4j 写入语义关系失败 | 捕获异常，追加到 errors，BuildResult 反映实际写入数 |
| ChromaDB 写入新概念失败 | 捕获异常，追加到 errors，概念仍在内存索引中（本次构建可用，下次丢失） |

**原则**：语义是增强功能，任何失败不阻塞核心构建流程。

### BuildResult 字段更新

无需修改 dataclass，已预留：
- `concepts_created` ← 新创建的 ConceptEntity 数量
- `semantic_relations_created` ← 成功写入的语义关系数量
- `skipped_semantic` ← Ollama 不可用时为 True

### 不修改的文件（scope 边界）

- `semantic.py` — 不改，接口已满足需求
- `aligner.py` — 不改，`add_concept()` + `align_batch()` 已够用
- `schema.py` — 不改，ConceptEntity 构造函数已满足
- 测试文件 — 新增 `tests/unit/test_builder_semantic.py`，不修改已有测试

## 依赖关系

- **依赖 Day 1**：`_build_entity_index()`, `_resolve_semantic_names()`, `_normalize_path()`
- **依赖 Phase 1**：`SemanticExtractor`, `ConceptAligner`, `SemanticRelation`, `ExtractionResult`
- **被 Day 3 依赖**：Stage 3a 完成后 Day 3 才能做模块聚类

## 测试策略

新增 `tests/unit/test_builder_semantic.py`，预计 12-16 个测试：

**_check_ollama 测试（2个）**
1. mock httpx 200 → True
2. mock httpx 异常 → False

**_init 方法测试（2个）**
3. `_init_semantic_extractor` — lazy init + 缓存验证
4. `_init_concept_aligner` — lazy init + ChromaStore 依赖

**_process_semantic_relations 测试（5-6个）**
5. 路径 A：新概念创建 + aligner.add_concept 调用验证
6. 路径 A：对齐到已有概念（aligner 返回 match_type="exact"），不创建新 ConceptEntity，复用已有 concept_id
7. 路径 A：概念去重（多个 SemanticRelation 指向同一 target_name → 只创建一个 ConceptEntity）
8. 路径 A：新概念写入 ChromaDB 验证（mock chroma_store 验证调用参数）
9. 路径 B：CodeEntity → CodeEntity 的 semantic_impact（两个 ID 都从 index 解析）
10. 路径 B：target_id 解析失败（entity_index 中无匹配）→ 跳过该关系，skipped+1
11. 路径 B：target_type 为 ResourceEntity（如 "diagram"）→ 跳过该关系
12. 混合路径：同时有概念目标和代码目标的语义关系

**build() 集成测试（3-4个）**
13. Ollama 可用 → 完整语义流水线（concepts_created > 0, semantic_relations_created > 0）
14. Ollama 不可用 → skipped_semantic=True, concepts_created=0
15. SemanticExtractor 异常 → error 记录 + 不中断结构流水线
16. Neo4j 写入失败 → error 记录 + 计数正确
