# Phase 2 Day 8: 实施计划

## 前提
方案已通过审核（docs/plans/2026-05-12-phase2-day8-quality-fix-proposal.md V3）。

## Task 列表

### Task 1: merge_relation() 添加 label 参数 + 抽象接口更新
**文件**: `src/layerkg/graph_store.py`, `src/layerkg/neo4j_store.py`
**测试**: `tests/unit/test_neo4j_store.py` (已有)

1. 修改 `graph_store.py:GraphStore.merge_relation()` 抽象方法签名：
   ```python
   def merge_relation(self, source_id: str, target_id: str, rel_type: str,
                      properties: dict | None = None, *,
                      source_label: str = "", target_label: str = "") -> dict:
   ```

2. 修改 `neo4j_store.py:merge_relation()` 实现：
   - 添加 `source_label: str = ""` 和 `target_label: str = ""` keyword-only 参数
   - 动态构建 Cypher：
     ```python
     source_part = f"source:{source_label}" if source_label else "source"
     target_part = f"target:{target_label}" if target_label else "target"
     cypher = f"MERGE ({source_part} {{id: $source_id}})-[r:{neo4j_rel_type}]->({target_part} {{id: $target_id}})"
     ```

3. 添加测试用例：
   - `test_merge_relation_with_labels`: 验证带 label 的 MERGE 正确创建节点
   - `test_merge_relation_without_labels`: 验证无 label 时向后兼容

### Task 2: builder.py + incremental_updater.py 所有 merge_relation 调用点传 label
**文件**: `src/layerkg/builder.py`, `src/layerkg/incremental_updater.py`

1. `builder.py:238` — 结构关系，添加 `source_label="CodeEntity", target_label="CodeEntity"`
2. `builder.py:296` — 语义关系，使用 `ENTITY_TYPE_TO_LABEL.get()` 映射 source_type/target_type：
   ```python
   s_label = ENTITY_TYPE_TO_LABEL.get(rel.source_type, "")
   t_label = ENTITY_TYPE_TO_LABEL.get(rel.target_type, "")
   graph_store.merge_relation(rel.source_id, rel.target_id, rel.relation_type,
                              source_label=s_label, target_label=t_label)
   ```
3. `builder.py:365` — DESCRIBES，添加 `source_label="DocEntity", target_label="CodeEntity"`
4. `incremental_updater.py:295` — 结构关系，添加 `source_label="CodeEntity", target_label="CodeEntity"`
5. `incremental_updater.py:404` — 结构关系，添加 `source_label="CodeEntity", target_label="CodeEntity"`

ENTITY_TYPE_TO_LABEL 常量定义：
```python
ENTITY_TYPE_TO_LABEL: dict[str, str] = {
    "function": "CodeEntity", "class": "CodeEntity", "interface": "CodeEntity",
    "module": "CodeEntity", "file": "CodeEntity",
    "readme": "DocEntity", "module_doc": "DocEntity", "api_doc": "DocEntity",
    "comment": "DocEntity", "wiki": "DocEntity", "architecture_doc": "DocEntity",
    "business_concept": "ConceptEntity", "design_pattern": "ConceptEntity",
    "api_contract": "ConceptEntity", "data_model": "ConceptEntity", "process": "ConceptEntity",
}
```

### Task 3: module_clustering.py merge_relation 调用点传 label
**文件**: `src/layerkg/module_clustering.py`

1. `module_clustering.py:304` — CONTAINS 关系，添加 `source_label="ModuleEntity", target_label="CodeEntity"`

### Task 4: 添加 cleanup_orphan_nodes() 方法
**文件**: `src/layerkg/neo4j_store.py`, `src/layerkg/graph_store.py`
**测试**: `tests/unit/test_neo4j_store.py`

1. 在 `graph_store.py` 抽象接口添加 `cleanup_orphan_nodes() -> int`
2. 在 `neo4j_store.py` 实现：
   ```python
   def cleanup_orphan_nodes(self) -> int:
       with self._driver.session() as session:
           result = session.run("MATCH (n) WHERE labels(n) = [] DETACH DELETE n RETURN count(*) as deleted")
           return result.single()["deleted"]
   ```

### Task 5: 语义提取 — _parse_response() 处理 <think/> 标签
**文件**: `src/layerkg/extractor/semantic.py`
**测试**: `tests/unit/test_semantic.py` (新建或已有)

1. 在 `_parse_response()` 方法中，在 `text = response_text.strip()` 之后、`if "```json" in text:` 之前（即 line 324 之前）插入：
   ```python
   # 移除 qwen3.5 等模型的 <think...</think 标签
   text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL).strip()
   ```

2. 在文件顶部确认 `import re` 已存在（若无则添加）

3. 添加测试用例：
   - `test_parse_response_with_think_tags`: 验证 `<think...JSON</think` 后能正确解析
   - `test_parse_response_without_think_tags`: 验证无 think 标签时正常工作

### Task 6: 语义提取 — batch_size 5→20 + _stage_semantic 传 doc_entities
**文件**: `src/layerkg/extractor/semantic.py`, `src/layerkg/builder.py`

1. `semantic.py:112` — 修改默认 `batch_size: int = 20`

2. `builder.py:244` — 修改 `_stage_semantic()` 签名：
   ```python
   def _stage_semantic(self, all_entities: list[CodeEntity], graph_store: Neo4jGraphStore,
                       repo_path: Path, *, doc_entities: list[DocEntity] | None = None) -> tuple[...]:
   ```

3. `builder.py:271` — 修改 `extractor.extract()` 调用：
   ```python
   extraction = extractor.extract(all_entities, doc_entities=doc_entities)
   ```

4. `builder.py:375` — 修改调用点：
   ```python
   concepts_created, semantic_rels_created, skipped_semantic, sem_errors, new_concepts = self._stage_semantic(
       all_entities, graph_store, repo_path, doc_entities=doc_entities)
   ```

### Task 7: 聚类 — 同文件预分组 + 诊断日志
**文件**: `src/layerkg/module_clustering.py`
**测试**: `tests/unit/test_module_clustering.py`

1. 在 `_load_graph()` 方法中添加同文件虚拟边逻辑：
   - 查询所有 CodeEntity 的 `id` 和 `file_path` 属性
   - 按 `file_path` 分组，同文件内的实体两两建立虚拟边（互连）
   - 虚拟边加入邻接表 `adj`

2. 在 `detect_modules()` 中添加诊断日志：
   ```python
   isolated = sum(1 for neighbors in adj.values() if not neighbors)
   self._logger.info("Module clustering graph: %d nodes, %d isolated (%.1f%%), %d edges",
                     len(adj), isolated, 100*isolated/len(adj) if adj else 0,
                     sum(len(n) for n in adj.values()) // 2)
   ```

### Task 8: 验证 + ruff
**命令**:
```bash
uv run pytest tests/ -q --tb=short
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

## 执行顺序

```
Batch 1 (Tasks 1-4): 基础设施 — merge_relation label + cleanup
Batch 2 (Tasks 5-6): 语义提取修复
Batch 3 (Task 7):    聚类修复
Batch 4 (Task 8):    验证
```
