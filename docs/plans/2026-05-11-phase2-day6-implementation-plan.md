# Phase 2 Day 6 实施计划：CLI 增强 + 构建报告

> 方案审核 8.5→修订→预估 9.5+

## Batch 1：Builder + BuildResult（Task 2 + Task 3）

### Task 1：build() 新增 skip 参数

**文件**: `src/layerkg/builder.py`

1. 修改 `build()` 签名为：
```python
def build(
    self,
    repo_path: Path,
    *,
    skip_semantic: bool = False,
    skip_clustering: bool = False,
) -> BuildResult:
```

2. Stage 3 语义提取前加判断：
```python
# Stage 3: 语义提取（可降级）
if skip_semantic:
    concepts_created = 0
    semantic_rels_created = 0
    skipped_semantic = True
    sem_errors = []
    new_concepts = []
else:
    concepts_created, semantic_rels_created, skipped_semantic, sem_errors, new_concepts = self._stage_semantic(...)
```

3. Stage 4 模块聚类前加判断：
```python
# Stage 4: 模块聚类（可降级）
if skip_clustering:
    clusters_count = 0
    clusters = []
else:
    try:
        clusters_count, clusters = self._detect_and_write_modules(graph_store)
    except Exception as e:
        ...
```

**测试**:
- `test_build_skip_semantic_returns_zero_concepts` — mock build, skip_semantic=True → skipped_semantic=True, concepts_created=0
- `test_build_skip_clustering_returns_zero_modules` — skip_clustering=True → modules_created=0
- `test_build_skip_both` — 两个都 skip → 两者都跳过

### Task 2：BuildResult.__str__()

**文件**: `src/layerkg/builder.py`

在 BuildResult dataclass 中新增：
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

**测试**:
- `test_build_result_str_contains_all_fields` — __str__() 包含 "Files scanned", "Entities created", "Relations created", "Doc entities", "Concepts", "Semantic rels", "Modules", "Semantic stage", "Build status", "Elapsed"
- `test_build_result_str_errors` — 有 errors 时包含 "Errors" 和错误消息
- `test_build_result_str_aborted` — aborted=True 时包含 "[X] aborted"

---

## Batch 2：CLI 选项 + Config（Task 1 + Task 4）

### Task 3：build CLI 新增选项

**文件**: `src/layerkg/cli.py`

1. 修改 `build` 命令签名：
```python
@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--skip-semantic", is_flag=True, help="跳过语义提取 (Stage 3)")
@click.option("--skip-clustering", is_flag=True, help="跳过模块聚类 (Stage 4)")
@click.option("--verbose-build", is_flag=True, help="逐阶段输出详情")
def build(repo_path: str, skip_semantic: bool, skip_clustering: bool, verbose_build: bool) -> None:
```

2. 修改 build 调用：
```python
result = builder.build(
    Path(repo_path),
    skip_semantic=skip_semantic,
    skip_clustering=skip_clustering,
)
```

3. 修改输出逻辑：
```python
if verbose_build:
    click.echo(str(result))
else:
    click.echo(
        f"Build complete: {result.files_scanned} files scanned, "
        f"{result.entities_created} entities created, "
        f"{result.relations_created} relations created"
    )
```

**测试**:
- `test_build_command_skip_semantic` — `--skip-semantic` → builder.build called with skip_semantic=True
- `test_build_command_skip_clustering` — `--skip-clustering` → builder.build called with skip_clustering=True
- `test_build_command_verbose_output` — `--verbose-build` → output contains "Build Report:" and "Semantic stage"
- `test_build_command_default_output_unchanged` — 无 flag → output matches existing format

### Task 4：Config 新增 build 配置

**文件**: `src/layerkg/config.py`

1. 补充导入：`from dataclasses import dataclass, field`

2. 新增字段（在 `llm_model` 之后）：
```python
    # Build 配置
    build_include_docs: bool = True
    build_doc_extensions: list[str] = field(default_factory=lambda: [".md", ".rst"])
    build_skip_dirs: set[str] = field(default_factory=lambda: {
        "__pycache__", ".git", ".mypy_cache", ".ruff_cache",
        "node_modules", ".venv", "site", ".tox", "dist", "build",
        "*.egg-info",
    })
```

3. `from_env()` 补充：
```python
build_include_docs=os.getenv("LAYERKG_BUILD_INCLUDE_DOCS", "true").lower() == "true",
```

**测试**:
- `test_config_build_include_docs_default` — 默认 True
- `test_config_build_include_docs_from_env` — 环境变量 "false" → False
- `test_config_build_doc_extensions_default` — 默认 [".md", ".rst"]
- `test_config_build_skip_dirs_default` — 默认包含 "__pycache__"

---

## 验证

```bash
uv run pytest tests/ -q --tb=short
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

预期：639 → ~651 tests
