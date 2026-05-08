# Day 4 方案：重构 build() 主方法

## 1. 问题背景

当前 `build()` 方法（builder.py L124-L241）虽然在 Day 1-3 中逐步集成了 Stage 1-5，但存在以下问题：

1. **无计时统计**：`BuildResult.elapsed_ms` 字段已定义但 build() 未赋值
2. **错误处理不统一**：Stage 2 (Neo4j 写入) 失败应中止构建但当前只是继续执行
3. **阶段日志不够清晰**：缺少明确的阶段分隔标记，不利于调试和 verbose 模式
4. **build() 结构可读性差**：Stage 3 的语义提取逻辑内联在 build() 中，120+ 行混合了编排和细节

## 2. 设计方案

### 2.1 重写 build() 为分阶段编排

将 build() 重构为清晰的 5 阶段编排结构，每阶段提取为独立私有方法：

```python
def build(self, repo_path: Path) -> BuildResult:
    t0 = time.monotonic()
    self._repo_root = repo_path
    errors: list[str] = []

    # Stage 1: 扫描 + 解析 + 关系提取
    all_entities, relations, py_files = self._stage_parse(repo_path, errors)

    # Stage 2: 写入 CodeEntity + 结构关系 → Neo4j（失败抛异常）
    try:
        graph_store = self._stage_write_structural(all_entities, relations, errors)
    except RuntimeError as e:
        self._logger.error("Stage 2 failed, aborting: %s", e)
        errors.append(str(e))
        return self._make_result(py_files, all_entities, [], [], 0, t0, errors, aborted=True)

    # Stage 3: 语义提取 + 概念对齐（失败降级）
    new_concepts, semantic_rels = self._stage_semantic(all_entities, graph_store, repo_path, errors)

    # Stage 4: 模块聚类（失败降级，已内置在 _detect_and_write_modules）
    clusters_count, clusters = self._detect_and_write_modules(graph_store)

    # Stage 5: 统一向量写入（失败记录 error，继续）
    self._stage_write_vectors(all_entities, new_concepts, clusters, errors)

    return self._make_result(py_files, all_entities, relations, new_concepts, clusters_count, t0, errors)
```

**关键设计**：Stage 2 失败通过 `RuntimeError` 异常传递，由 build() 捕获后直接返回中止结果。这样避免修改 `_get_graph_store()` 的行为（它失败时自然抛异常）。

### 2.2 提取的私有方法

| 方法 | 职责 | 返回 |
|------|------|------|
| `_stage_parse(repo_path, errors)` | Stage 1: 扫描 + 解析 + 关系提取，逐文件错误降级 | `(entities, relations, py_files)` |
| `_stage_write_structural(entities, relations, errors)` | Stage 2: 写入 Neo4j，**失败抛 RuntimeError** | `Neo4jGraphStore` |
| `_stage_semantic(entities, graph_store, repo_root, errors)` | Stage 3: 语义提取 + 概念对齐 + 写入，内部异常降级 | `(new_concepts, semantic_rels)` |
| `_stage_write_vectors(entities, concepts, clusters, errors)` | Stage 5: 统一向量写入，失败不中止 | `None` |
| `_make_result(...)` | 构建 BuildResult + 计算 elapsed_ms | `BuildResult` |

### 2.3 错误处理策略（与 Phase 2 计划一致）

| Stage | 失败行为 | 理由 |
|-------|---------|------|
| Stage 1 (解析) | 跳过该文件，继续 | 单文件失败不影响其他 |
| Stage 2 (Neo4j 写入) | **中止构建**（抛 RuntimeError） | 数据不一致，后续阶段依赖 Neo4j |
| Stage 3 (语义提取) | 降级跳过 | 图谱完整但无语义信息 |
| Stage 4 (模块聚类) | 降级跳过 | 已有降级处理 |
| Stage 5 (向量写入) | 记录 error，继续 | 图谱完整，向量可重建 |

### 2.4 计时统计

- 使用 `time.monotonic()` 在 build() 入口记录 `t0`
- `_make_result()` 中计算 `elapsed_ms = (monotonic() - t0) * 1000`

### 2.5 Stage 3 细节保留

Stage 3 的核心逻辑（概念对齐 + 路径 A/B 分流）已在 `_process_semantic_relations()` 中，`_stage_semantic()` 仅负责：
1. 检查 Ollama 可用性
2. 调用 SemanticExtractor
3. 调用 `_process_semantic_relations()` 
4. 将结果写入 Neo4j
5. 异常时降级

### 2.6 BuildResult 扩展

添加 `aborted: bool = False` 字段到 BuildResult，用于标记 Stage 2 中止。当 `aborted=True` 时，`errors` 非空。

### 2.7 Stage 3 内部降级说明

Stage 3 内部包含多个子步骤，每个子步骤的失败行为：

| 子步骤 | 失败行为 |
|--------|---------|
| `_check_ollama()` 返回 False | 整体跳过，`skipped_semantic=True` |
| `extractor.extract()` 抛异常 | catch 后降级，记录 error |
| `_process_semantic_relations()` 抛异常 | catch 后降级，记录 error |
| 单个 concept Neo4j 写入失败 | 记录 warning，继续处理其他 concept |
| 单个 semantic relation Neo4j 写入失败 | 记录 warning，继续处理其他 relation |

### 2.8 不改什么

- **不修改**任何 Stage 子方法的内部逻辑（`_process_semantic_relations`, `_detect_and_write_modules`, `_write_all_vectors` 等）
- **不修改** BuildResult 已有字段（只新增 `aborted`）
- **不修改** close() 方法
- **不修改** query() / info() 方法
- **不修改** __init__ 签名
- 测试文件中已有的测试 **不删除**，只新增 Day 4 测试

## 3. 依赖关系

- 前置：Day 1-3 所有基础设施方法已就位 ✅
- 无外部新增依赖（`time` 是标准库）

## 4. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 重构 build() 可能破坏 Day 1-3 的 20+ 个已有测试 | 已有测试全部 mock 了子方法，重构编排不应影响 |
| Stage 2 中止路径需返回部分结果 | `_make_result` 支持 aborted 标志 |
| `_stage_semantic` 异常降级需确保 new_concepts 初始化 | 方法返回空列表作为默认值 |

## 5. 预期测试（约 6 个）

1. `test_build_full_pipeline_e2e` — mock 所有组件，完整 5 阶段通过
2. `test_build_stage2_failure_aborts` — Neo4j 写入异常 → RuntimeError → 中止返回
3. `test_build_stage2_abort_has_aborted_flag` — 中止时 BuildResult.aborted=True
4. `test_build_stage3_failure_degrades` — Ollama 异常 → 跳过语义，aborted=False
5. `test_build_stage4_failure_degrades` — clustering 异常 → 跳过
6. `test_build_elapsed_ms_recorded` — elapsed_ms > 0
