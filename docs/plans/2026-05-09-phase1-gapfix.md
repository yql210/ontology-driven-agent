# Phase 1 补救计划：IncrementalUpdater 缺失功能补齐

> 背景：Claude Code 审查发现 Day 9 IncrementalUpdater 存在 3 项严重缺失 + 2 项简化处理

## 修复范围

### Task 1: Stage 3 新增 ConceptEntity 处理分支
- **文件**: `src/layerkg/incremental_updater.py`
- **位置**: `update()` 方法 Stage 3 循环**结束后**（在 Stage 4 之前）
- **改动**: 从 `impact_report.impacted_nodes` 中过滤 `node_label == "ConceptEntity"` 的节点，标记需要 LLM 重提取
- **实现**:
  1. 在 `update()` 方法 Stage 3 for 循环结束后，从 `impact_report.impacted_nodes` 按 `node_label` 过滤
  2. 新增 `_flag_concept_reextraction(impacted_concept_ids: list[str]) -> int` 方法：通过 Cypher `MATCH (n:ConceptEntity) WHERE n.id IN $ids SET n.needs_reextraction = true` 标记
  3. 返回标记数量，写入 UpdateReport 新字段 `concepts_flagged`
- **测试**: `tests/unit/test_incremental_updater.py` 新增 class `TestConceptEntityHandling`
  - `test_concept_reextraction_flagged_on_signature_change`: SIGNATURE 变更影响 ConceptEntity → 调用 `_flag_concept_reextraction`
  - `test_concept_reextraction_not_called_for_code_only`: 只影响 CodeEntity → 不调用
  - `test_concept_reextraction_count_in_report`: 验证 `UpdateReport.concepts_flagged` 计数正确

### Task 2: Stage 3 新增 DocEntity 处理分支
- **文件**: `src/layerkg/incremental_updater.py`
- **位置**: `update()` 方法 Stage 3 循环**结束后**（在 Stage 4 之前，紧随 Task 1 之后）
- **改动**: 从 `impact_report.impacted_nodes` 中过滤 `node_label == "DocEntity"` 的节点，标记需要重生成
- **实现**:
  1. 新增 `_flag_doc_regeneration(impacted_doc_ids: list[str]) -> int` 方法：通过 Cypher `MATCH (n:DocEntity) WHERE n.id IN $ids SET n.needs_regeneration = true` 标记
  2. 在 Stage 3 结束后，从 impact_report 中收集 DocEntity 类型的 impacted_nodes 并标记
  3. UpdateReport 新增 `docs_flagged` 字段
- **测试**: 新增 class `TestDocEntityHandling`
  - `test_doc_regeneration_flagged_on_change`: 变更影响 DocEntity → 标记重生成
  - `test_doc_regeneration_not_called_for_code_only`: 只影响 CodeEntity → 不调用
  - `test_docs_flagged_count_in_report`: 验证 `UpdateReport.docs_flagged` 计数

### Task 3: Stage 4 新增完整性检查 `_validate_graph_integrity()`
- **文件**: `src/layerkg/incremental_updater.py`
- **位置**: `update()` 方法 Stage 4 部分（在 `_record_changeset` 之后）
- **改动**: 新增图谱完整性校验方法
- **实现**:
  1. 新增 `_validate_graph_integrity() -> dict` 方法：
     - 检查 CodeEntity 必须关系：每个 CodeEntity 至少有一条 outgoing 关系（calls/extends/implements/imports/contains）
     - 注意：此检查为**软检查**（只记录警告，不阻断更新），返回 `{"warnings": int, "orphan_code_entities": list[str]}`
     - 使用 Cypher: `MATCH (n:CodeEntity) WHERE NOT (n)--() RETURN n.id AS id, n.name AS name`
  2. UpdateReport 新增 `integrity_warnings` 字段（int, default=0）
  3. 在 `update()` 的 Stage 4 调用 `_validate_graph_integrity()`，结果写入 UpdateReport
- **测试**: 新增 class `TestGraphIntegrityValidation`
  - `test_validate_returns_warnings_for_orphan_nodes`: mock query 返回孤立节点 → warnings > 0
  - `test_validate_returns_zero_for_healthy_graph`: mock query 返回空列表 → warnings == 0
  - `test_integrity_warnings_in_update_report`: update() 结束后 report.integrity_warnings 正确

### Task 4: UpdateReport 字段扩展 + 序列化
- **文件**: `src/layerkg/incremental_updater.py`
- **位置**: `UpdateReport` dataclass
- **改动**:
  1. 新增字段:
     - `concepts_flagged: int = 0` — 被标记重提取的概念数
     - `docs_flagged: int = 0` — 被标记重生成的文档数
     - `integrity_warnings: int = 0` — 完整性检查警告数
  2. 更新 `to_dict()` 方法包含新字段
- **测试**: 新增/更新 class `TestUpdateReport`
  - `test_new_fields_default_to_zero`: 新字段默认值为 0
  - `test_to_dict_includes_new_fields`: to_dict() 包含新字段

### Task 5: UpdateReport 构造调用点适配
- **文件**: `src/layerkg/incremental_updater.py`
- **位置**: `update()` 方法中的 `UpdateReport(...)` 构造（两处：dry_run 和正常返回）
- **改动**: 传入新字段值

## 实施顺序

**Batch 1** (Task 4 + Task 5): 先扩展 UpdateReport，再适配调用点 → 基础设施
**Batch 2** (Task 1): ConceptEntity 处理
**Batch 3** (Task 2): DocEntity 处理
**Batch 4** (Task 3): 完整性检查

## 预期新增测试

- 约 12 个新测试（每个 Task 3 个）
- 总测试数 544 → ~556

## 质量标准

- `ruff check src/ tests/` 零警告
- `pytest tests/` 全部通过
- 所有新方法有文档字符串
