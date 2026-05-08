# Day 3 实施计划：模块聚类流水线 + 向量写入统一

基于方案 v3（审核 9.5/10）

## Task 1: 新增 `_init_clustering()` 方法
**文件**: `src/layerkg/builder.py`
**位置**: `_init_concept_aligner()` 之后

```python
def _init_clustering(self) -> ModuleClustering:
    """Lazy init ModuleClustering."""
    if self._clustering is None:
        from layerkg.module_clustering import ModuleClustering
        self._clustering = ModuleClustering(
            neo4j_store=self._get_graph_store(),
        )
    return self._clustering
```

**新增字段**: `__init__` 中 `self._clustering: ModuleClustering | None = None`

**测试**: `test_init_clustering_lazy_init`
- mock Neo4jGraphStore，调用两次，验证只创建一次
- 验证传入了正确的 neo4j_store

## Task 2: 新增 `_detect_and_write_modules()` 方法
**文件**: `src/layerkg/builder.py`

```python
def _detect_and_write_modules(
    self, graph_store: Neo4jGraphStore
) -> tuple[int, list[ModuleCluster]]:
    """Stage 4: 检测模块聚类并写入 Neo4j.

    Returns:
        (clusters_count, clusters) 元组。
    """
    try:
        clustering = self._init_clustering()
        clusters = clustering.detect_modules()
        if clusters:
            clustering.save_modules(clusters)
        return len(clusters), clusters
    except Exception as e:
        self._logger.warning("Module clustering failed: %s", e)
        return 0, []
```

**测试**:
- `test_detect_and_write_modules_success` — mock 3 clusters → (3, clusters)
- `test_detect_and_write_modules_empty_graph` — detect_modules 返回 [] → (0, [])
- `test_detect_and_write_modules_exception` — detect_modules 抛异常 → (0, [])

## Task 3: 新增 `_write_all_vectors()` 方法
**文件**: `src/layerkg/builder.py`

```python
def _write_all_vectors(
    self,
    all_entities: list[CodeEntity],
    new_concepts: list[ConceptEntity],
    clusters: list[ModuleCluster],
) -> None:
    """Stage 5: 统一向量写入 ChromaDB."""
    chroma_store = self._get_chroma_store()
    items: list[tuple[str, str, dict]] = []

    # CodeEntity
    for entity in all_entities:
        text = self._entity_to_text(entity)
        if text:
            items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))

    # ConceptEntity
    for concept in new_concepts:
        text = concept.description or concept.name
        items.append((concept.id, text, {"entity_type": concept.entity_type, "name": concept.name}))

    # ModuleCluster → cluster.module
    for cluster in clusters:
        module = cluster.module
        text = module.description or module.name
        # ModuleEntity 无 entity_type 字段，硬编码 "module"
        items.append((module.id, text, {"entity_type": "module", "name": module.name}))

    if items:
        chroma_store.put_entities_batch(items)
```

**测试**:
- `test_write_all_vectors_code_entities_only` — 验证 items 格式
- `test_write_all_vectors_concept_entities_only`
- `test_write_all_vectors_module_clusters`
- `test_write_all_vectors_mixed_types` — 三种混合
- `test_write_all_vectors_empty_lists` — 空 lists → 不调用 put_entities_batch

## Task 4: 修改 build() — 插入 Stage 4 + Stage 5
**文件**: `src/layerkg/builder.py`

**变更1**: 删除原步骤 5（第 166-175 行的 ChromaDB CodeEntity 写入）

**变更2**: 在 Stage 3a 之前（第 177 行 `concepts_created = 0` 之前）初始化：
```python
        new_concepts: list[ConceptEntity] = []  # 供 Stage 5 使用
```

**变更3**: 在 Stage 3a（第 228 行之后）插入：
```python
        # Stage 4: 模块聚类
        clusters_count, clusters = self._detect_and_write_modules(graph_store)

        # Stage 5: 向量写入统一
        try:
            self._write_all_vectors(all_entities, new_concepts, clusters)
        except Exception as e:
            self._logger.warning("Vector write failed: %s", e)
            errors.append(f"Vector write error: {e}")
```

**变更4**: BuildResult 新增 `modules_created` 字段（已存在于 schema.py 第 65 行）
- 返回语句添加 `modules_created=clusters_count`

**变更4**: 如果 `_process_semantic_relations` 中有 ChromaDB 写入，移除之

**测试**:
- `test_build_full_pipeline_with_modules` — 完整流水线，验证 BuildResult 包含 modules_created
- `test_build_chroma_failure_records_error` — ChromaDB 失败 → errors 非空

## Task 5: 移除分散的 ChromaDB 写入
**文件**: `src/layerkg/builder.py`

检查 `_process_semantic_relations` 方法，移除内部的 ChromaDB 写入代码。
新概念向量由 Stage 5 `_write_all_vectors` 统一处理。

## 执行顺序

```
Task 1 → Task 2 → Task 3 → Task 4 → Task 5
(每个 Task 先写测试，再实现，跑通后进入下一个)
```

## 预计测试数

新增 10-12 个测试，总数从 595 → ~607
