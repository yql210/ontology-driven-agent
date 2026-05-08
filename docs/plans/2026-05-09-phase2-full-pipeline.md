# Phase 2 实施计划：全量构建 Pipeline（9天）

> 目标：升级 `LayerKGBuilder.build()` 为完整流水线，`layerkg build ./repo` 一条命令产出完整知识图谱（6实体+11关系）。

## 前置修正（Claude Code 审核发现的 3 个严重问题）

1. **流水线顺序**：ModuleClustering 依赖 Neo4j 已有数据，必须先写 CodeEntity+关系再聚类
2. **name→id 映射**：SemanticRelation 用 name，写关系需要 id，需维护复合索引
3. **新概念创建**：ConceptAligner 返回 NO_MATCH 时，Builder 需创建新 ConceptEntity

## 正确流水线

```
Stage 1: 扫描 → Parser → CodeEntity 列表
Stage 2: 写入 CodeEntity + 结构关系到 Neo4j（先写入！）
Stage 3: SemanticExtractor → SemanticRelation（依赖 Ollama）
         ConceptAligner 对齐/去重 + 新概念创建
         写入 ConceptEntity + 语义关系
Stage 4: ModuleClustering（从 Neo4j 加载图）→ ModuleEntity
         写入 ModuleEntity + contains 关系
Stage 5: 所有实体写入 ChromaDB 向量
```

---

## Day 1: 实体索引 + BuildResult 扩展

### Task 1.1: 新增 `_build_entity_index()` 方法
- **文件**: `src/layerkg/builder.py`
- 功能：构建 `(file_path, name) → entity.id` 的复合索引
- 处理同名冲突：同一文件中同名函数用 `(file_path, name:line)` 区分
- **module/file 类型实体**：module 可能没有唯一 name，用 file_path 作为 name
- **跨目录同名文件**：file_path 不同自然区分
- 返回 `dict[tuple[str, str], str]`

### Task 1.2: 新增 `_resolve_semantic_names()` 方法
- **文件**: `src/layerkg/builder.py`
- 功能：将 SemanticRelation 的 source_name/target_name 解析为 source_id/target_id
- 使用 `_build_entity_index()` 查找 CodeEntity id
- 找不到的 name 记录 warning，跳过该关系
- 返回 `list[Relation]`（ID-based）

### Task 1.3: 扩展 BuildResult
- **文件**: `src/layerkg/builder.py`
- 新增字段：
  - `concepts_created: int = 0`
  - `semantic_relations_created: int = 0`
  - `modules_created: int = 0`
  - `doc_entities_created: int = 0`
  - `skipped_semantic: bool = False`
  - `elapsed_ms: float = 0.0`
  - `errors: list[str] = []`
- 更新 `to_dict()` 方法

### 测试
- `test_build_entity_index_basic` — 3个实体 → 索引 3 条
- `test_build_entity_index_duplicate_name` — 同名不同文件 → 都保留
- `test_build_entity_index_same_file_duplicate` — 同文件同名 → 加行号区分
- `test_resolve_semantic_names_success` — name 匹配 → 正确 id
- `test_resolve_semantic_names_missing` — name 找不到 → 跳过 + warning
- `test_build_result_new_fields` — 默认值正确

---

## Day 2: 语义提取流水线（Stage 3 前半）

### Task 2.1: Builder 集成 SemanticExtractor
- **文件**: `src/layerkg/builder.py`
- `__init__` 新增 `self._semantic_extractor`（lazy init，使用 config.ollama 配置）
- 新增 `_check_ollama() → bool` 健康检查方法（httpx GET /api/tags, timeout=5s）

### Task 2.2: Builder 集成 ConceptAligner
- **文件**: `src/layerkg/builder.py`
- `__init__` 新增 `self._aligner`（lazy init，需要 ChromaStore）
- 新增 `_process_semantic_relations()` 方法：
  1. 遍历 SemanticExtractor 返回的 SemanticRelation
  2. 对每个 target_name 调用 aligner.align()
  3. match_type=="none" → 创建新 ConceptEntity + add_concept()
  4. 汇总返回 `(list[ConceptEntity], list[Relation])`
- **首次构建说明**：ConceptAligner 从空概念列表开始。SemanticExtractor 提取出第一个概念名时，由于 NO_MATCH 而创建新 ConceptEntity，调用 add_concept() 加入索引。后续相同概念名可精确匹配复用，实现动态去重。

### Task 2.3: build() 中串联 Stage 3 前半
- 在 Stage 2（写入 CodeEntity+结构关系）之后
- 检查 Ollama 可用性 → 可用则执行语义提取
- 不可用则 `skipped_semantic=True`，跳过整个 Stage 3

### 测试
- `test_check_ollama_available` — mock 200 → True
- `test_check_ollama_unavailable` — mock 异常 → False
- `test_process_semantic_new_concept` — NO_MATCH → 创建 ConceptEntity
- `test_process_semantic_existing_concept` — exact match → 复用 id
- `test_semantic_skipped_when_ollama_down` — Ollama 不可用 → skipped_semantic=True
- `test_concept_dedup_by_aligner` — 多个 SemanticRelation 指向同一概念 → 只创建一个

---

## Day 3: 模块聚类流水线（Stage 4）

### Task 3.1: Builder 集成 ModuleClustering
- **文件**: `src/layerkg/builder.py`
- `__init__` 新增 `self._clustering`（lazy init，需要 Neo4jGraphStore）
- 新增 `_detect_modules() → list[ModuleCluster]` 方法
- 新增 `_write_modules() → int` 方法：写入 ModuleEntity + contains 关系

### Task 3.2: build() 中串联 Stage 4
- 在 Stage 3（语义提取+写入）之后
- 调用 ModuleClustering.cluster() → save_modules()
- 将 modules_created 写入 BuildResult

### Task 3.3: 向量写入统一（Stage 5）
- **文件**: `src/layerkg/builder.py`
- 新增 `_write_all_vectors()` 方法，统一写入所有实体到 ChromaDB：
  - CodeEntity（source 或 name+file_path 构造文本）
  - ConceptEntity（name + description）
  - ModuleEntity（name + description）
- 替换现有的分散写入逻辑

### 测试
- `test_detect_modules_returns_clusters` — mock clustering → 3 clusters
- `test_write_modules_creates_entities_and_relations` — 验证 merge_node + merge_relation 调用
- `test_write_all_vectors_code_entities` — CodeEntity 向量写入
- `test_write_all_vectors_concept_entities` — ConceptEntity 向量写入
- `test_module_clustering_skipped_when_no_entities` — 空 Neo4j → 0 modules

---

## Day 4: 重构 build() 主方法

### Task 4.1: 重写 build() 为分阶段流水线
- **文件**: `src/layerkg/builder.py`
- 完整重写 `build()` 方法，按正确顺序编排 5 个 Stage
- 每个阶段有独立的错误处理和日志
- 总耗时记录到 BuildResult.elapsed_ms

### Task 4.2: 错误处理策略
- Stage 1（解析）失败：跳过该文件，记录 warning，继续
- Stage 2（写入 Neo4j）失败：记录 error，**中止构建**（数据不一致）
- Stage 3（语义提取）失败：降级跳过，skipped_semantic=True
- Stage 4（模块聚类）失败：降级跳过，modules_created=0
- Stage 5（向量写入）失败：记录 error，继续（图谱完整，向量缺失可重建）

### Task 4.3: close() 方法更新
- 新增关闭 SemanticExtractor（httpx client）

### 测试
- `test_build_full_pipeline_e2e` — mock 所有组件 → 完整流水线通过
- `test_build_stage2_failure_aborts` — Neo4j 写入异常 → 中止
- `test_build_stage3_failure_degrades` — Ollama 异常 → 跳过语义
- `test_build_stage4_failure_degrades` — clustering 异常 → 跳过
- `test_build_elapsed_ms_recorded` — elapsed_ms > 0

---

## Day 5: 文档摄入（Stage 1 扩展）

### Task 5.1: 新增 DocParser
- **文件**: `src/layerkg/parser/doc_parser.py`（新文件）
- 功能：解析 `.md` / `.rst` 文件
- 输出：`list[DocEntity]`
- **Markdown 解析**：使用正则表达式（无需额外依赖）
  - 提取标题（`# ...`）作为 name
  - 正文作为 content
  - 代码块（` ``` ` 围栏）中的内容单独提取
- **RST 解析**：使用正则表达式
  - 提取 section 标题（`===`/`---` 下划线语法）
  - directive（`.. code-block::` 等）
- 识别文档类型：README / module_doc / api_doc / comment / wiki / architecture_doc
  - 文件名 `README*` → readme
  - `docs/` 目录下 + 文件名含 `api` → api_doc
  - `docs/` 目录下 + 其他 → module_doc
  - `docs/architecture/` 或文件名含 `arch`/`design` → architecture_doc
  - 其他 → comment

### Task 5.2: Builder 扩展文档扫描
- **文件**: `src/layerkg/builder.py`
- `_scan_python_files()` 重命名为 `_scan_files()`，同时扫描 `.py` + `.md` + `.rst`
- Stage 1 分两路：
  - `.py` → PythonParser → CodeEntity
  - `.md/.rst` → DocParser → DocEntity
- DocEntity 也写入 Neo4j 和 ChromaDB

### Task 5.3: 文档→代码关联（describes 关系）
- **文件**: `src/layerkg/builder.py`
- 新增 `_link_docs_to_code()` 方法：
  - **路径匹配**：文档中引用的代码路径（如 `src/layerkg/builder.py`）→ 匹配该路径下所有 CodeEntity
  - **函数名匹配**：代码块中的标识符 → 匹配 CodeEntity name（仅在路径匹配的实体范围内匹配，降低误匹配）
  - 生成 `describes` 关系
- **防误匹配策略**：路径匹配优先级高于函数名匹配；函数名匹配仅限长度 > 3 的标识符

### 测试
- `test_doc_parser_markdown_basic` — 解析 README → DocEntity
- `test_doc_parser_rst_basic` — 解析 .rst → DocEntity
- `test_doc_parser_type_detection_readme` — README* → readme 类型
- `test_doc_parser_type_detection_docs_dir` — docs/ 下 → module_doc
- `test_scan_files_includes_md` — 扫描结果包含 .md 文件
- `test_link_docs_to_code_path_match` — 文档引用路径 → describes 关系
- `test_doc_entities_in_build_result` — BuildResult.doc_entities_created > 0

---

## Day 6: CLI 增强 + 构建报告

### Task 6.1: build CLI 增强
- **文件**: `src/layerkg/cli.py`
- `build` 命令新增选项：
  - `--skip-semantic` — 跳过语义提取
  - `--skip-clustering` — 跳过模块聚类
  - `--verbose-build` — 逐阶段输出详情
- 输出改进：显示 BuildResult 的所有字段（含新增字段）

### Task 6.2: BuildResult 序列化
- **文件**: `src/layerkg/builder.py`
- 完善 `to_dict()` 和 `__str__()` 方法
- 人类可读的构建报告格式

### Task 6.3: config 新增 build 相关配置
- **文件**: `src/layerkg/config.py`
- 新增配置项：
  - `build_include_docs: bool = True` — 是否扫描文档
  - `build_doc_extensions: list[str] = [".md", ".rst"]` — 文档扩展名
  - `build_skip_dirs: set[str]` — 跳过的目录列表

### 测试
- `test_cli_build_skip_semantic` — --skip-semantic → skipped_semantic=True
- `test_cli_build_skip_clustering` — --skip-clustering → modules_created=0
- `test_cli_build_verbose_output` — verbose 模式输出
- `test_build_result_str_format` — __str__() 可读
- `test_config_build_defaults` — 默认配置正确

---

## Day 7: 真实全量构建验证

### Task 7.1: 对 LayerKG 自身代码执行真实 build
- 运行 `uv run layerkg build . --verbose-build`
- 调通 Neo4j + ChromaDB + Ollama 全链路
- 验证产出：CodeEntity + ConceptEntity + ModuleEntity + DocEntity + 所有 11 种关系

### Task 7.2: 修复真实环境问题
- 预期可能遇到的问题：
  - Ollama 超时（大文件批处理）→ 调整 batch_size
  - Neo4j 约束冲突 → 调整 ensure_constraints
  - ChromaDB 嵌入失败 → 调整文本长度
- 记录每个问题的修复

### Task 7.3: 真实数据质量检查
- 用 MCP Server 的查询工具检查图谱：
  - `semantic_search("知识图谱")` → 应能搜到相关实体
  - `graph_query("MATCH (n) RETURN labels(n), count(*)")` → 各类型实体数
  - `get_module_tree()` → 应有聚类结果
- **补充验证**：
  - 文档摄入：检查 `docs/` 目录下的 .md 文件是否被正确解析为 DocEntity
  - 语义关系：检查是否生成了 `semantic_impact` 关系
  - 聚类结果：检查 ModuleEntity 是否包含合理的 code 聚类

---

## Day 8: 增量更新联调

### Task 8.1: Builder 持有 GitChangeDetector + 缓存初始化
- **文件**: `src/layerkg/builder.py`
- `__init__` 新增 `self._change_detector`（lazy init，用 config.repo_path）
- build() 结束后调用 `self._change_detector.update_cache()` 初始化缓存
- 确保后续 update 不会重复处理已有文件

### Task 8.2: IncrementalUpdater 补齐语义提取和模块重聚类
- **文件**: `src/layerkg/incremental_updater.py`
- `_apply_added()` 方法扩展为完整流水线：
  1. Parser → CodeEntity（现有）
  2. 写入 CodeEntity + 结构关系到 Neo4j（现有）
  3. **新增**：SemanticExtractor → ConceptAligner → 语义关系写入（复用 Builder 的 `_process_semantic_relations()`）
  4. **新增**：ModuleClustering 重聚类（因新增实体可能改变聚类边界）
- `_apply_modified()` 和 `_apply_deleted()` 不变（变更传播已由 IncrementalUpdater 的 Stage 3 处理）
- 新增 `_recluster_modules()` 私有方法，封装聚类重算逻辑

### Task 8.3: build → update 联调测试
- 真实流程：build → 修改一个文件 → update → 验证增量更新结果
- 验证不会产生重复实体
- 验证 ChangeSetEntity 记录正确
- 验证新增文件的语义提取和聚类重算

### 测试（集成测试级别）
- `test_build_then_update_no_duplicates` — build→update→无重复
- `test_build_cache_initialized` — build 后缓存有数据
- `test_update_on_full_graph` — 完整图谱上增量更新
- `test_update_added_file_triggers_semantic` — 新增文件触发语义提取
- `test_update_added_file_triggers_recluster` — 新增文件触发聚类重算

---

## Day 9: 质量收尾

### Task 9.1: 全量测试运行
- `pytest tests/ -v` — 确认所有测试通过
- `ruff check src/ tests/` — 零警告
- 预期测试数：558 + ~50 = ~608

### Task 9.2: 代码审查
- 交 Claude Code 审查 Phase 2 所有变更
- 修复发现的问题

### Task 9.3: 文档更新
- 更新思源笔记进展文档
- 更新 CLAUDE.md 项目结构
- 更新 CLI help 文本
- Git commit + push

---

## 预期成果

| 指标 | Phase 1 结束 | Phase 2 结束 |
|------|-------------|-------------|
| build 覆盖实体 | CodeEntity only | **6实体全覆盖** |
| build 覆盖关系 | 5种结构关系 | **11种关系全覆盖** |
| 测试总数 | 558 | ~608 |
| CLI 能力 | build/update/serve | **build(完整)+update+serve** |
| 真实验证 | 无 | **LayerKG 自身代码** |

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Ollama 超时 | Day 7 预留修复时间，可调 batch_size |
| Neo4j 性能 | 小项目（LayerKG 自身）不会成为瓶颈 |
| 同名实体冲突 | Day 1 已有复合索引方案 |
| 文档解析质量 | 简单启发式，Phase 3 可用 LLM 增强 |
