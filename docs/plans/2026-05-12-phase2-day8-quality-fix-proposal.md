# Phase 2 Day 8: 质量收尾 — 无 label 节点 + 聚类失效 + 语义零产出

## 问题背景

Day 7 全量构建验证跑通，但暴露三个质量问题：

| 问题 | 现象 | 根因 |
|------|------|------|
| 无 label 节点 | Neo4j 中 5390 个无 label 节点 | `neo4j_store.py:merge_relation()` 的 Cypher 未给 source/target 加 label |
| 聚类失效 | ModuleEntity=945=CodeEntity | Label Propagation 只查 CALLS/IMPORTS/EXTENDS/IMPLEMENTS 4种边，图太稀疏 |
| 语义零产出 | Concepts=0, SemanticRels=0 | 需进一步诊断 LLM 返回内容 |

## 问题 1：无 label 节点（P0 - 最高优先级）

### 根因

`neo4j_store.py:168` 的 `merge_relation()` 使用：
```cypher
MERGE (source {id: $source_id})-[r:REL_TYPE]->(target {id: $target_id})
```

**没有给 source 和 target 加 label**（如 `:CodeEntity`）。当 MERGE 找不到匹配节点时，会创建无 label 的中间节点。每次关系写入如果 source/target 节点不存在（如跨批次、或引用了不存在的实体），就会创建无 label 节点。

### 修复方案

1. **修改 `merge_relation()` 签名**：添加 `source_label`/`target_label` keyword-only 参数（不修改 Relation dataclass）
   ```python
   def merge_relation(self, source_id: str, target_id: str, rel_type: str,
                      properties: dict | None = None, *,
                      source_label: str = "", target_label: str = "") -> dict:
   ```
   - 有 label 时：`MERGE (source:CodeEntity {id: $source_id})`
   - 无 label 时（空字符串）：`MERGE (source {id: $source_id})`（向后兼容）

2. **所有 `merge_relation()` 调用点添加 label**：

   | 文件 | 行号 | 场景 | source_label | target_label |
   |------|------|------|-------------|-------------|
   | `builder.py:238` | 结构关系写入（AST） | `CodeEntity` | `CodeEntity` |
   | `builder.py:296` | 语义关系写入 | `CodeEntity` | `CodeEntity`（语义）或 `ConceptEntity` |
   | `builder.py:365` | DESCRIBES 关系写入 | `DocEntity` | `CodeEntity` |
   | `module_clustering.py:308` | CONTAINS 关系写入 | `ModuleEntity` | `CodeEntity` |

   注意：**不需要修改 Relation dataclass**。每个调用点的实体类型是已知的：
   - 结构关系（238行）：全是 CodeEntity→CodeEntity，硬编码即可
   - 语义关系（296行）：使用 `SemanticRelation.source_type`/`target_type` 字段映射到 label
   - DESCRIBES（365行）：DocEntity→CodeEntity，硬编码
   - CONTAINS（module_clustering.py:308）：ModuleEntity→CodeEntity，硬编码

   语义关系的 type→label 映射逻辑：
   ```python
   ENTITY_TYPE_TO_LABEL = {
       "function": "CodeEntity", "class": "CodeEntity", "module": "CodeEntity",
       "readme": "DocEntity", "module_doc": "DocEntity",
       "business_concept": "ConceptEntity", "design_pattern": "ConceptEntity",
   }
   ```

3. **清理已有无 label 节点**：添加 `neo4j_store.py` 中的 `cleanup_orphan_nodes()` 方法
   ```cypher
   MATCH (n) WHERE size(labels(n)) = 0 DETACH DELETE n
   ```

### 不改什么

- `merge_node()` 方法已有正确的 label（`f"MERGE (n:{label} {{id: $id}})"`），不需要改
- 关系属性的 SET 子句逻辑正确，不需要改
- Relation dataclass 不需要改（label 信息在调用时传入，不需要持久化到 Relation 对象）

## 问题 2：聚类失效（P1）

### 根因

`module_clustering.py:88-91` 的 `_load_graph()` 只查 4 种关系：
```python
WHERE type(r) IN ['CALLS', 'IMPORTS', 'EXTENDS', 'IMPLEMENTS']
```

而实际 Neo4j 中的关系分布：
- CONTAINS: 1842（最多）
- DESCRIBES: 846
- EXTENDS: 7
- CALLS/IMPORTS/IMPLEMENTS: 可能非常少

945 个 CodeEntity 中，如果只有 7 条 EXTENDS 和少量 CALLS/IMPORTS，图极度稀疏。Label Propagation 中孤立节点保持自身标签，导致 945 个独立模块。

### 前置诊断（必须先做）

在修复聚类前，先诊断为什么 AST 解析器没提取到 CALLS/IMPORTS：
1. 查询 Neo4j 中 CALLS/IMPORTS/IMPLEMENTS 关系的实际数量
2. 抽样检查几个 Python 文件的 AST 解析结果，确认是否漏提取
3. 根据诊断结果决定：是修复解析器还是调整聚类算法（或两者都要）

### 修复方案

1. **同文件预分组**（主要方案）：不依赖 CONTAINS 关系（当前 CONTAINS 是 ModuleEntity→CodeEntity，不是 CodeEntity→CodeEntity），而是直接用 `file_path` 属性建立虚拟边
   - 遍历所有 CodeEntity，按 `file_path` 分组
   - 同文件的函数/类互连（全连接虚拟边）
   - 这是最强的分组信号，优先级高于 CALLS/IMPORTS

2. **保留原有结构边**：继续加载 CALLS/IMPORTS/EXTENDS/IMPLEMENTS 作为补充边
   - 如果 AST 解析器确实没提取到这些关系（诊断确认），后续单独修解析器

3. **诊断日志**：在 `detect_modules()` 中打印图统计信息
   ```
   Graph: 945 nodes, 850 isolated (89.9%), 15 connected components
   ```
   方便后续调试

### 不改什么

- 不换聚类算法（Label Propagation 本身没问题，是输入图太稀疏）
- 不加语义边（语义提取尚未稳定，不依赖它）
- 不改 AST 解析器（那是另一个问题，不在 Day 8 范围内）

## 问题 3：语义零产出（P2 - 需诊断）

### 可能原因

1. **LLM 返回非 JSON**：qwen3.5:9b 会输出思考过程（`<think...</think`>）包裹 JSON，`_parse_response()` 无法解析
2. **confidence 过滤**：`_validate_relation()` 要求 confidence >= 0.5，如果模型输出 < 0.5 则全部被过滤
3. **批次过小**：`batch_size=5` 导致 945/5=189 次 LLM 调用，每次只看 5 个实体，关系跨批次无法被发现
4. **extract() 未传 doc_entities**：builder.py:271 `extractor.extract(all_entities)` 未传 `doc_entities` 参数
5. **_stage_semantic() 签名不支持**：方法签名（builder.py:244-249）不接受 `doc_entities` 参数，需要修改

### 修复方案

1. **处理 `<think`> 标签**：在 `_parse_response()` 中，先 strip 掉 `<think...</think`> 再解析 JSON
   ```python
   # 在 json.loads 之前
   import re
   text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL).strip()
   ```

2. **增大 batch_size**：从 5 提升到 20
   - qwen3.5:9b 的 context window 为 131072 tokens，每次 20 个实体的 prompt 约几千 tokens，不会超限
   - LLM 调用次数从 189 降到 ~48 次

3. **传 doc_entities**：
   - 修改 `_stage_semantic()` 方法签名：添加 `doc_entities: list[DocEntity]` 参数
   - 修改调用点（builder.py:375）：`self._stage_semantic(all_entities, graph_store, repo_path, doc_entities=doc_entities)`
   - 修改 `extractor.extract(all_entities)` 为 `extractor.extract(all_entities, doc_entities=doc_entities)`

4. **添加诊断日志**：
   - 在 `_parse_response()` 中记录 LLM 原始返回的前 200 字符
   - 在 `extract()` 中记录每批次的解析成功/失败次数
   - 在 Config 中添加 `semantic_debug: bool = False`，开启时记录完整请求/响应

### 不改什么

- 不换模型（qwen3.5:9b 是当前可用的最大模型）
- 不改 prompt 结构（prompt 设计合理，问题在解析和参数）
- 不改 `_validate_relation()` 的 confidence 门槛（0.5 是合理的最低标准）

## 依赖关系

```
问题1 (无label节点) → 独立，先修
问题2 (聚类失效)   → 独立，可与问题1并行
问题3 (语义零产出)  → 依赖诊断结果，优先级最低
```

三个问题互不依赖，但修复后需要重新构建验证。

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| merge_relation 加 label 后破坏现有关系 | 保留无 label fallback，向后兼容 |
| 聚类改后过度合并（945→1） | 限制最大模块大小，加诊断日志 |
| 语义修复后 LLM 调用时间变长 | batch_size=20 是合理折中，189→48次调用 |
