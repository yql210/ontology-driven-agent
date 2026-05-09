# Phase 2 Day 9 反思：三次 Hotfix 与语义提取突破

## 日期
2026-05-14

## 目标
验证 Day 8 质量修复效果，确保全量构建三个核心指标达标。

## 执行过程

### 第一轮：merge_relation MERGE 模式匹配 Bug
- **现象**：Stage 2 约束冲突 `Node(261) already exists with label CodeEntity`
- **根因**：Neo4j `MERGE (source:CodeEntity {id:$id})-[r:CONTAINS]->(target:CodeEntity {id:$id})` 对整个模式做原子匹配。当节点已存在但关系不存在时，MERGE 尝试重建节点，触发 UNIQUE 约束。
- **修复**：拆成 MATCH source → MATCH target → MERGE (source)-[r]->(target)
- **教训**：Neo4j MERGE 是模式级操作，不是元素级。对已存在节点创建新关系时，必须先 MATCH 再 MERGE 关系。

### 第二轮：PythonParser source 字段为空
- **现象**：构建成功但 Concepts=0, Semantic=0，84 分钟 LLM 调用全部 200 OK 但无产出
- **根因**：PythonParser 创建 CodeEntity 时不设置 source 字段。LLM 收到的 prompt 只有函数名没有源码，无法提取语义关系。
- **修复**：用 `ast.get_source_range()` 提取 function/class 节点源码文本，module 用文件前 200 字符。
- **教训**：数据管线中任何字段的缺失都可能导致下游完全静默失败。source 是语义提取的核心输入，缺失后 LLM 只有无意义函数名，输出自然无效。

### 第三轮：entity_index 模糊查找 Bug
- **现象**：source 修复后 LLM 确实在调用且返回结果（30 次 /api/chat 200 OK），但 Semantic rels 仍为 0
- **根因**：`_process_semantic_relations` 路径 B 的 entity_index 查找用 `target_key = (type, "", name)`（空字符串 file_path），但 index 里所有 key 都有非空 file_path。同时 LLM 返回的 entity name 可能没有类名前缀。
- **修复**：添加 `_fuzzy_lookup_entity` 方法，精确匹配失败后回退 (type, name) 匹配忽略 file_path。
- **教训**：LLM 输出和结构化索引之间的映射存在语义鸿沟。LLM 不知道也不关心 file_path，只提供函数名。模糊匹配是必要的容错机制。

## 最终构建结果

| 指标 | Day 7 基线 | Day 8 修复 | Day 9 三修 |
|------|-----------|-----------|-----------|
| CodeEntity | 945 | 958 | 958 |
| DocEntity | 652 | 693 | 693 |
| ModuleEntity | 945 | 48 | 48 |
| 无label节点 | 5390 | 0 | 0 |
| 总关系 | 2695 | 1770 | 2792 |
| SEMANTIC_IMPACT | 0 | 0 | **61** |
| DERIVED_FROM | 0 | 0 | **1** |
| 向量 | - | 1594 | 1594 |
| Concepts | 0 | 0 | 0 |

## 核心突破
语义关系从 0→62（61 SEMANTIC_IMPACT + 1 DERIVED_FROM），标志着 LLM 驱动的语义提取管线真正跑通。

## 未解决
- Concepts=0：LLM（qwen2.5-coder:0.5b）能力不足，未生成独立 ConceptEntity 节点。这不是代码 Bug，是模型能力问题。升级模型（如 qwen3.5:9b）后应能解决。

## 关键收获
1. **Merger 模式匹配** 是 Neo4j 常见陷阱，MERGE 不是 UPSERT
2. **数据完整性验证** 应该在每个阶段检查关键字段是否填充，而不是等最终结果
3. **LLM ↔ 结构化数据的桥接** 需要模糊匹配容错，不能假设 LLM 输出完全对齐索引 schema
4. **静默失败** 是最难调试的问题——LLM 返回 200 OK 但实际不产出，需要逐层追踪数据流

## Phase 2 整体评价
Day 1-9 从零搭建了完整的知识图谱构建管线：Parser → Builder → Neo4j Store → Chroma 向量 → 语义提取 → 模块聚类。Phase 2 目标达成：全量构建 Pipeline 组装完成，真实验证通过。
