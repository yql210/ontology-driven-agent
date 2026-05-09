# Phase 2 Day 6 方案：CLI 增强 + 构建报告

## 问题背景

当前 `build` 命令功能单一：
1. 没有跳过特定阶段的选项（调试时必须跑全流水线）
2. 输出只显示 3 个字段（files_scanned/entities_created/relations_created），Phase 2 新增的 8 个字段未展示
3. `BuildResult.to_dict()` 用 `dataclasses.asdict()` 序列化，缺少人类可读格式
4. `LayerKGConfig` 没有 build 相关配置项（文档扫描开关等）

## 设计方案

### Task 1：build CLI 增强

**文件**: `src/layerkg/cli.py`

给 `build` 命令新增 3 个选项：

```python
@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--skip-semantic", is_flag=True, help="跳过语义提取 (Stage 3)")
@click.option("--skip-clustering", is_flag=True, help="跳过模块聚类 (Stage 4)")
@click.option("--verbose-build", is_flag=True, help="逐阶段输出详情")
def build(repo_path: str, skip_semantic: bool, skip_clustering: bool, verbose_build: bool) -> None:
```

**skip-semantic 行为**：
- 将 `skip_semantic=True` 传给 `builder.build()`
- Builder 收到此参数时跳过 Stage 3（语义提取），设置 `skipped_semantic=True`

**skip-clustering 行为**：
- 将 `skip_clustering=True` 传给 `builder.build()`
- Builder 收到此参数时跳过 Stage 4（模块聚类），设置 `modules_created=0`

**verbose-build 行为**：
- 构建后输出完整的 `BuildResult` 报告（调用 `BuildResult.__str__()`）
- 非verbose模式输出单行摘要（兼容现有行为）

### Task 2：build() 方法接受 skip 参数

**文件**: `src/layerkg/builder.py`

修改 `build()` 签名：

```python
def build(
    self,
    repo_path: Path,
    *,
    skip_semantic: bool = False,
    skip_clustering: bool = False,
) -> BuildResult:
```

- `skip_semantic=True` 时：跳过 `_stage_semantic()` 调用，直接设 `skipped_semantic=True, concepts_created=0, semantic_relations_created=0`
- `skip_clustering=True` 时：跳过 `_detect_and_write_modules()` 调用，设 `clusters_count=0`

### Task 3：BuildResult 可读报告

**文件**: `src/layerkg/builder.py`

新增 `__str__()` 方法：

```python
def __str__(self) -> str:
    lines = ["Build Report:"]
    lines.append(f"  Files scanned:    {self.files_scanned}")
    lines.append(f"  Entities created: {self.entities_created}")
    lines.append(f"  Relations created: {self.relations_created}")
    lines.append(f"  Doc entities:     {self.doc_entities_created}")
    lines.append(f"  Concepts:         {self.concepts_created}")
    lines.append(f"  Semantic rels:    {self.semantic_relations_created}")
    lines.append(f"  Modules:          {self.modules_created}")
    lines.append(f"  Semantic stage: {'[!] skipped' if self.skipped_semantic else '[+] completed'}")
    lines.append(f"  Build status: {'[X] aborted' if self.aborted else '[+] success'}")
    if self.errors:
        lines.append(f"  Errors ({len(self.errors)}):")
        for err in self.errors:
            lines.append(f"    - {err}")
    lines.append(f"  Elapsed: {self.elapsed_ms:.0f}ms")
    return "\n".join(lines)
```

CLI 中：
- **非 verbose**: 单行 `Build complete: N files, M entities, K relations`（兼容现有）
- **verbose**: 输出完整 `__str__()` 报告

### Task 4：Config 新增 build 配置

**文件**: `src/layerkg/config.py`

新增字段：

```python
from dataclasses import dataclass, field

@dataclass
class LayerKGConfig:
    # ... 现有字段 ...
    
    # Build 配置
    build_include_docs: bool = True
    build_doc_extensions: list[str] = field(default_factory=lambda: [".md", ".rst"])
    build_skip_dirs: set[str] = field(default_factory=lambda: {
        "__pycache__", ".git", ".mypy_cache", ".ruff_cache",
        "node_modules", ".venv", "site", ".tox", "dist", "build",
        "*.egg-info",
    })
```

`from_env()` 补充：

```python
build_include_docs=os.getenv("LAYERKG_BUILD_INCLUDE_DOCS", "true").lower() == "true",
```

`build_doc_extensions` 和 `build_skip_dirs` 不通过环境变量配置（复杂列表不便序列化），直接用默认值。

## 依赖关系

执行顺序：
1. **Task 2** (build 签名变更) — 必须先完成，CLI 才能传参
2. **Task 3** (BuildResult.__str__) — 独立，可与 Task 2 并行
3. **Task 1** (CLI 选项) — 依赖 Task 2 + Task 3
4. **Task 4** (Config 扩展) — 独立
5. **Task 5** (测试) — 覆盖所有 Task

### skip 组合行为

| skip_semantic | skip_clustering | 行为 |
|:-:|:-:|------|
| False | False | 全流水线（默认） |
| True | False | 跳过 Stage 3，跑 Stage 4/5 |
| False | True | 跳过 Stage 4，跑 Stage 3/5 |
| True | True | 只跑 Stage 1/2/5（解析+写入+向量） |

### Task 5：测试覆盖

**文件**: `tests/unit/test_cli.py`，`tests/unit/test_builder.py`

新增测试：
- `test_build_command_skip_semantic` — `--skip-semantic` → `skipped_semantic=True`
- `test_build_command_skip_clustering` — `--skip-clustering` → `modules_created=0`
- `test_build_command_verbose_output` — `--verbose-build` → 完整报告输出
- `test_build_result_str_representation` — `__str__()` 包含所有非零字段
- `test_build_result_str_errors` — `__str__()` 包含错误列表
- `test_build_skip_semantic_returns_zero_concepts` — builder.build(skip_semantic=True) → concepts_created=0
- `test_build_skip_clustering_returns_zero_modules` — builder.build(skip_clustering=True) → modules_created=0
- `test_build_skip_both` — 同时 skip_semantic + skip_clustering → 两者都跳过

**文件**: `tests/unit/test_config.py`

新增测试：
- `test_config_build_include_docs_default` — 默认 True
- `test_config_build_include_docs_from_env` — 环境变量覆盖
- `test_config_build_doc_extensions_default` — 默认 [".md", ".rst"]
- `test_config_build_skip_dirs_default` — 默认包含 "__pycache__" 等

## 不改什么

- 不改 `build()` 内部流水线逻辑（只加 skip 开关）
- 不改 `update` / `query` / `info` / `serve` 命令
- 不改 `_stage_parse` / `_stage_semantic` 等内部方法签名
- `build_skip_dirs` 暂时不接入 Builder（Builder 已有硬编码 skip_dirs，Day 7 联调时再接入）

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| skip_semantic 导致概念缺失 | CLI 明确提示 "⚠ Semantic stage skipped" |
| __str__() emoji 在某些终端不显示 | 使用纯 ASCII 标记 [!] [X] [+] |
| build() 签名变更破坏现有调用 | 使用 keyword-only 参数（`*`），不破坏位置参数 |
| Config 新增字段影响 from_env() | 新字段有默认值，from_env() 只读环境变量（可选）|
