# Day 3 方案 v2：模块聚类流水线 + 向量写入统一

## 问题陈述

当前 `build()` 完成了 Stage 1-3a（扫描→解析→结构关系→语义提取），但：
1. ModuleClustering（Phase 1 已实现）未集成到 Builder 中
2. 向量写入逻辑分散：CodeEntity 在步骤 5 单独写入，ConceptEntity 在 `_process_semantic_relations` 中写入，不统一

## 目标

1. 在 build() 的 Stage 3a 之后插入 Stage 4（模块聚类）
2. 统一所有实体的向量写入逻辑为 `_write_all_vectors()` 方法，替换分散写入
3. 当 Neo4j 中无数据时优雅降级（0 modules）

## 方案设计

### 新增方法（均在 `src/layerkg/builder.py`）

#### 1. `_init_clustering() → ModuleClustering`
- lazy init，需要 Neo4jGraphStore 实例
- 缓存到 `self._clustering`
- 参数：`neo4j_store=self._get_graph_store()`

#### 2. `_detect_and_write_modules(graph_store: Neo4jGraphStore) → tuple[int, list[ModuleCluster]]`
集成方法，封装完整的 Stage 4 流程：
1. 调用 `_init_clustering()` 创建实例
2. 调用 `clustering.detect_modules()` 获取 `list[ModuleCluster]`
3. 空图时 `detect_modules()` 返回空列表（不抛异常）
4. 调用 `clustering.save_modules(clusters)` 写入 Neo4j
5. **返回 `(len(clusters), clusters)` 元组** — clusters 供 Stage 5 使用
6. 异常处理：捕获任何异常 → 记录 errors → 返回 `(0, [])`

**设计决策**：返回 clusters 列表是因为 Stage 5 的 `_write_all_vectors` 需要 ModuleCluster.module 来构造向量文本。

#### 3. `_write_all_vectors(all_entities: list[CodeEntity], new_concepts: list[ConceptEntity], clusters: list[ModuleCluster]) → None`
统一向量写入（参数命名与 build() 调用处一致）：
- **CodeEntity**：text = `entity.source`（源码）或 `f"{entity.name} {entity.file_path}"`，metadata = `{entity_type, name}`
- **ConceptEntity**：text = `concept.description or concept.name`，metadata = `{entity_type, name}`
- **ModuleCluster**：从 `cluster.module` 提取 ModuleEntity，text = `module.description or module.name`（description 通常为 None，降级到 name），metadata = `{entity_type, name}`
- 收集所有 items 后，**仅当 items 非空时**调用 `chroma_store.put_entities_batch(items)`（显式跳过空列表，避免 put_entities_batch([]) 副作用）
- 空 entities/concepts/clusters 列表安全处理（跳过空列表）

**设计决策**：参数命名为 `clusters`（非 modules）明确类型是 `ModuleCluster`，避免与 `ModuleEntity` 混淆。

### build() 中的集成

修改 build() 方法：

```
Stage 1-2: 扫描+解析+写入 Neo4j              [已有]
Stage 3a: 语义提取（Day 2）                   [已有，返回 new_concepts]
Stage 4: 模块聚类 ← Day 3 新增
  ├─ clusters_count, clusters = _detect_and_write_modules(graph_store)
  └─ clusters 传递给 Stage 5
Stage 5: 向量写入统一 ← Day 3 新增（替换原步骤5）
  ├─ _write_all_vectors(all_entities, new_concepts, clusters)
  └─ 删除原步骤5的分散 CodeEntity 写入
```

**关键变更**：
- 原 build() 步骤 5（写 ChromaDB CodeEntity）→ **删除**，由 Stage 5 统一处理
- `_process_semantic_relations` 中的 ChromaDB 写入 → **删除**，由 Stage 5 统一处理
- `new_concepts` 在 Stage 3a 中创建，传递给 Stage 5 → **可达性无问题**

### 降级策略

| 异常场景 | 处理方式 |
|---------|---------|
| Neo4j 空图（无 CodeEntity） | `detect_modules()` 返回空列表 → clusters_count=0，不报错 |
| detect_modules 异常 | 捕获，追加到 errors，返回 (0, []) |
| save_modules 部分失败 | save_modules 内部处理，返回实际写入数 |
| ChromaDB 写入失败 | 捕获异常，追加到 errors |
| 空 entities/concepts/clusters | `_write_all_vectors` 跳过空列表，不调用 ChromaDB |

### 不修改的文件

- `module_clustering.py` — 不改，`detect_modules()` + `save_modules()` 已满足
- `schema.py` — 不改
- `aligner.py` — 不改

### 需修改的文件

- `src/layerkg/builder.py` — 新增3个方法 + 修改 build() + 移除分散 ChromaDB 写入
- 新增 `tests/unit/test_builder_modules.py`

## 测试策略

新增 `tests/unit/test_builder_modules.py`，预计 10-12 个测试：

1. `_init_clustering` — lazy init + 缓存验证
2. `_detect_and_write_modules` — mock clustering → 3 clusters → 返回 (3, clusters)
3. `_detect_and_write_modules` — Neo4j 空图 → detect_modules 返回 [] → (0, [])
4. `_detect_and_write_modules` — detect_modules 异常 → error + (0, [])
5. `_write_all_vectors` — 只有 CodeEntity
6. `_write_all_vectors` — 只有 ConceptEntity
7. `_write_all_vectors` — 只有 ModuleCluster（从 cluster.module 提取）
8. `_write_all_vectors` — 混合三种实体类型
9. `_write_all_vectors` — 空 entities/concepts/clusters → 不调用 ChromaDB
10. build() 集成 — 完整流水线包含 Stage 4+5
11. build() 集成 — ChromaDB 写入失败 → error 记录
12. build() 集成 — 确认原步骤5的分散写入已移除
