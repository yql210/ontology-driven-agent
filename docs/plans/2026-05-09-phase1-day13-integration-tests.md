# Day 13: 集成测试 + CLI 完善 实现计划（v2 — 审核修订版）

> **For Hermes:** 使用 claude-code skill 的 print mode (`-p`) 执行任务。每个任务 2-5 分钟粒度。强制 TDD。
>
> **修订说明：** 根据首轮审核意见修订，修复 3 处重复测试、调整文件分类、补充缺失场景。

**Goal:** Phase 1 收尾 — 端到端集成测试 + CLI 测试补全 + 质量收尾。

---

## 一、现状分析

### 已有测试分布（517 tests）
| 文件 | tests | 类型 |
|------|-------|------|
| test_impact_propagator.py | 76 | unit |
| test_semantic_extractor.py | 60 | unit |
| test_incremental_updater.py | 46 | unit |
| test_change_detector.py | 45 | unit |
| test_aligner.py | 38 | unit |
| test_schema.py | 37 | unit |
| test_chroma_store.py | 34 | unit |
| test_module_clustering.py | 27 | unit |
| test_schema_extra.py | 24 | unit |
| test_python_parser.py | 23 | unit |
| test_mcp_server.py | 23 | unit |
| test_builder.py | 22 | unit |
| test_neo4j_store.py | 20 | unit |
| test_relation_extractor.py | 11 | unit |
| test_cli.py | 11 | unit |
| test_exceptions.py | 6 | unit |
| test_parser_base.py | 5 | unit |
| test_graph_store.py | 3 | unit |
| test_config.py | 3 | unit |
| test_neo4j_connection.py | 3 | integration |

### 缺口
1. **CLI 测试不完整** — serve 调用、update 调用、help 完整性无测试
2. **集成测试太少** — 只有 3 个 neo4j 连接测试，缺端到端流程测试
3. **MCP Server 端到端** — 工具注册验证
4. **缺少算法流程集成测试** — ImpactPropagator 完整传播链路、ConceptAligner 多步匹配流程

### 审核修订要点
- ❌ 原计划 Task 1 与 test_mcp_server.py 重复 → 改为 mock mcp.run() 调用验证
- ❌ 原计划 Task 3 test_main_help 已存在 → 改为补充 update/serve 命令
- ❌ 原计划 Task 4 test_build_missing_path 已存在 → 改为不传路径参数场景
- ❌ 原计划 Task 7/8 mock 测试放 integration/ → 移到 unit/ 扩展
- ❌ 原计划 Task 8 ConceptAligner 描述与实现不符 → 重写为匹配流程测试
- ✅ 新增 Task 11: IncrementalUpdater 四阶段流水线集成测试

---

## 二、TDD 任务分解（11 tasks, ~28 tests）

### Part A: CLI 测试补全（4 tasks, ~8 tests）

#### Task 1: CLI serve 命令调用验证（2 tests）
> mock `mcp.run()` 验证实际调用，不重复 test_mcp_server.py 中的注册测试
- `test_serve_stdio_calls_mcp_run`：mock `layerkg.mcp_server.mcp.run`，调用 `layerkg serve`，验证 `mcp.run()` 被调用且无参数
- `test_serve_http_calls_mcp_run_with_params`：mock 同上，调用 `layerkg serve --transport http --port 9000`，验证 `mcp.run(transport="http", port=9000)`

#### Task 2: CLI update 命令测试（2 tests）
- `test_update_command_success`：mock `IncrementalUpdater`，调用 `layerkg update <path>`，验证输出含更新信息
- `test_update_command_dry_run`：调用 `layerkg update <path> --dry-run`，验证 `dry_run=True` 传递正确

#### Task 3: CLI help 完整性补充（1 test）
> 注意：test_cli.py 中 test_main_help 只验证了 build/query/info，需补充
- `test_main_help_includes_update_and_serve`：验证 `--help` 输出包含 update 和 serve 命令描述

#### Task 4: CLI 错误处理补充（2 tests）
> 注意：test_build_command_nonexistent_path_fails 已存在，不重复
- `test_build_command_missing_path_argument`：不传路径参数调用 `layerkg build`，验证退出码非零且提示缺少参数
- `test_query_command_without_limit_shows_results`：验证 query 不传 --limit 时正常返回结果（不验证具体默认值，已有 test_query_with_type_option 隐式覆盖）

### Part B: 算法流程集成测试（3 tasks, ~8 tests）
> 放在 `tests/unit/` 下，mock 外部依赖，验证完整算法流程

#### Task 5: MCP Server 工具注册验证（3 tests）
- `test_all_tools_registered`：验证 8 个工具都注册在 mcp 实例中（semantic_search, graph_query, impact_analysis, get_context, list_concepts, get_module_tree, detect_changes, export_graph）
- `test_tool_has_docstring`：验证每个工具函数都有 docstring
- `test_tool_decorator_applied`：验证工具函数有 mcp tool 标记

#### Task 6: Builder 端到端（Python 源码 → 解析 → 关系提取）（3 tests）
> 使用真实 PythonParser + RelationExtractor（无 Neo4j/ChromaDB），扩展 `tests/unit/test_builder.py`
- `test_parse_and_extract_sample_file`：解析一个简单 Python 文件，验证实体和关系
- `test_parse_class_with_methods`：解析含类+方法的文件，验证 contains 关系
- `test_parse_imports`：解析含 import 的文件，验证 imports 关系

#### Task 7: ImpactPropagator 算法流程验证（3 tests）
> mock GraphStore（返回预设图结构），验证完整传播流程
- `test_linear_chain_propagation`：A→B→C→D 链式传播，验证影响范围和衰减
- `test_diamond_propagation`：菱形依赖 A→B, A→C, B→D, C→D，验证不重复
- `test_max_depth_cutoff`：超过 max_depth 停止传播

### Part C: ConceptAligner 流程 + 集成 + 质量收尾（4 tasks, ~12 tests）

#### Task 8: ConceptAligner 匹配流程验证（3 tests）
> mock ChromaDB + Neo4j，验证多步匹配策略（精确→别名→向量→图结构）
- `test_align_pipeline_vector_fallback`：精确/别名匹配失败后向量匹配生效
- `test_align_with_graph_structure`：图结构匹配（Jaccard 重叠度）
- `test_align_batch_terms_extended`：批量对齐多术语，验证各策略降级

#### Task 9: IncrementalUpdater 四阶段流水线（3 tests）
> mock GraphStore + VectorStore，验证 detect→propagate→update→validate 流程，扩展 `tests/unit/test_incremental_updater.py`
- `test_incremental_update_add_file`：新增文件场景，验证完整流程
- `test_incremental_update_modify_file`：修改文件场景，验证变更传播
- `test_incremental_update_dry_run`：dry-run 模式不修改任何存储

#### Task 10: 边界测试（3 tests）
- `test_export_graph_dot_format`：验证 DOT 输出格式正确
- `test_export_graph_cytoscape_format`：验证 Cytoscape 输出结构
- `test_change_detector_mixed_status`：git diff 含混合状态（A+M+D）的正确分类

#### Task 11: 最终验证（2 tests + 全量检查）
- `test_all_source_modules_importable`：验证所有 src/layerkg/*.py 可导入
- `test_all_test_modules_importable`：验证所有 test_*.py 可导入
- 全量 pytest + ruff + 覆盖率报告

---

## 三、新增/修改文件

| 文件 | 类型 | 预估行数 | 说明 |
|------|------|---------|------|
| `tests/unit/test_cli.py` | 修改 | +80 行 | Task 1-4 |
| `tests/unit/test_mcp_server.py` | 修改 | +40 行 | Task 5 |
| `tests/unit/test_impact_propagator.py` | 修改 | +60 行 | Task 7 |
| `tests/unit/test_aligner.py` | 修改 | +50 行 | Task 8 |
| `tests/unit/test_builder.py` | 修改 | +120 行 | Task 6（真实 Parser+Extractor，无外部服务，放 unit） |
| `tests/unit/test_incremental_updater.py` | 修改 | +80 行 | Task 9（mock 存储，放 unit） |
| `tests/unit/test_export_boundary.py` | 新增 | ~40 行 | Task 10 |

---

## 四、关键注意事项

- 集成测试用 `@pytest.mark.integration` 标记（需要真实外部服务）
- Task 6 端到端只用真实 Parser + Extractor，不连 Neo4j/ChromaDB
- Task 7-8 使用 mock 外部依赖，放在 unit/ 下（审核意见）
- Task 9 mock GraphStore + VectorStore 验证流程逻辑
- **不要重复已有测试**：写之前先 grep 确认测试名不存在
- 最终目标：**545+ tests**，覆盖率 ≥95%
- ruff clean，无 warning

---

## 五、执行顺序

**Batch 1（Task 1-5）：** CLI 补全 + MCP 工具注册验证 (~11 tests)
**Batch 2（Task 6-11）：** 端到端集成测试 + 算法流程 + 质量收尾 (~17 tests)
