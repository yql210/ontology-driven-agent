# Phase 0 Day 5: CLI 入口 + Builder 流水线 + 集成测试

## 目标
实现 CLI 入口和 Builder 流水线，将 Day 1-4 的所有组件（Parser → Extractor → Neo4jStore + ChromaStore）串联为端到端可用的命令行工具。完成 Phase 0 全部工作。

## 设计决策

### 1. Builder 流水线（独立于 CLI）
CLI 不直接组装组件，而是调用一个 `LayerKGBuilder` 类。这样 CLI 测试只需验证命令参数传递，核心逻辑在 Builder 中测试。

### 2. CLI 命令设计
```bash
layerkg build <repo_path>          # 全量构建：扫描 .py → 解析 → 写 Neo4j + ChromaDB
layerkg query <text> [--filter k=v] [--limit N]  # 语义搜索
layerkg info                        # 显示配置信息（连接状态、集合统计）
```

`update` 命令暂不实现（需要 Git 集成，Phase 1 范围）。

### 3. 文件扫描策略
- 默认扫描 `repo_path` 下所有 `*.py` 文件
- 自动跳过隐藏目录（`.git`, `__pycache__`, `.venv`, `node_modules`）
- 使用 `pathlib.Path.rglob("*.py")`

## 文件清单

### 新增文件
| 文件 | 预估行数 | 说明 |
|------|---------|------|
| `src/layerkg/builder.py` | ~150 | LayerKGBuilder 流水线 |
| `src/layerkg/cli.py` | ~120 | Click CLI 入口（3 命令） |
| `tests/unit/test_builder.py` | ~350 | Builder 单元测试（mock stores） |
| `tests/unit/test_cli.py` | ~200 | CLI 单元测试（Click test runner） |

### 修改文件
| 文件 | 变更 |
|------|------|
| `pyproject.toml` | 无需修改（click 已在依赖中） |

## 实现计划

---

### Task 1: LayerKGBuilder (builder.py)

**核心类**，封装全量构建和查询流水线：

```python
class LayerKGBuilder:
    """LayerKG 构建器，组装解析 → 提取 → 存储流水线。"""

    def __init__(self, config: LayerKGConfig) -> None:
        self._config = config
        self._parser = PythonParser()
        self._extractor = RelationExtractor()
        self._graph_store: Neo4jGraphStore | None = None
        self._chroma_store: ChromaStore | None = None
        self._logger = logging.getLogger(__name__)

    def _get_graph_store(self) -> Neo4jGraphStore:
        if self._graph_store is None:
            self._graph_store = Neo4jGraphStore(
                uri=self._config.neo4j_uri,
                user=self._config.neo4j_user,
                password=self._config.neo4j_password,
            )
        return self._graph_store

    def _get_chroma_store(self) -> ChromaStore:
        if self._chroma_store is None:
            self._chroma_store = ChromaStore(
                persist_dir=self._config.chroma_persist_dir,
                ollama_url=self._config.ollama_base_url,
                embedding_model=self._config.embedding_model,
            )
        return self._chroma_store

    # --- 核心操作 ---

    def build(self, repo_path: Path) -> BuildResult:
        """全量构建：扫描 → 解析 → 写图 + 向量。"""
        # 1. 扫描 .py 文件
        py_files = self._scan_python_files(repo_path)
        # 2. 逐文件解析
        all_entities: list[CodeEntity] = []
        for f in py_files:
            result = self._parser.parse_file(f)
            if result.error:
                self._logger.warning("Skip %s: %s", f, result.error)
                continue
            all_entities.extend(result.entities)
            self._extractor.add_parse_result(result.entities, result.relations)
        # 3. 解析关系
        relations = self._extractor.resolve(all_entities)
        # 4. 写 Neo4j
        graph_store = self._get_graph_store()
        graph_store.ensure_constraints()
        for entity in all_entities:
            graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))
        for rel in relations:
            graph_store.merge_relation(rel.source_id, rel.target_id, rel.relation_type)
        # 5. 写 ChromaDB（有 source 的实体）
        chroma_store = self._get_chroma_store()
        items = []
        for entity in all_entities:
            text = self._entity_to_text(entity)
            if text:
                items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))
        chroma_store.put_entities_batch(items)
        # 6. 返回结果
        return BuildResult(
            files_scanned=len(py_files),
            entities_created=len(all_entities),
            relations_created=len(relations),
        )

    def query(self, text: str, n_results: int = 10, entity_type: str | None = None) -> list[dict]:
        """语义搜索。"""
        chroma_store = self._get_chroma_store()
        where = {"entity_type": entity_type} if entity_type else None
        return chroma_store.search(text, n_results=n_results, where=where)

    def info(self) -> dict:
        """获取存储统计信息。"""
        result = {"config": {...}}
        chroma_store = self._get_chroma_store()
        result["chroma_count"] = chroma_store.count()
        return result

    # --- 辅助方法 ---

    @staticmethod
    def _scan_python_files(repo_path: Path) -> list[Path]:
        """扫描 Python 文件，跳过隐藏目录。"""
        skip_dirs = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache"}
        files = []
        for p in repo_path.rglob("*.py"):
            if any(part in skip_dirs for part in p.parts):
                continue
            files.append(p)
        return sorted(files)

    @staticmethod
    def _entity_to_dict(entity: CodeEntity) -> dict:
        """将 CodeEntity 转为 Neo4j 属性字典。"""
        d = {"id": entity.id, "name": entity.name, "entity_type": entity.entity_type}
        if entity.file_path: d["file_path"] = entity.file_path
        if entity.start_line is not None: d["start_line"] = entity.start_line
        if entity.end_line is not None: d["end_line"] = entity.end_line
        if entity.language: d["language"] = entity.language
        return d

    @staticmethod
    def _entity_to_text(entity: CodeEntity) -> str | None:
        """提取实体的可嵌入文本。"""
        if entity.source:
            return entity.source
        # 对于没有 source 的实体，构造描述文本
        parts = [f"{entity.entity_type} {entity.name}"]
        if entity.file_path:
            parts.append(f"in {entity.file_path}")
        return " ".join(parts)

    def close(self) -> None:
        if self._graph_store:
            self._graph_store.close()
        if self._chroma_store:
            self._chroma_store.close()

    def __enter__(self) -> LayerKGBuilder: ...
    def __exit__(self, *exc) -> None: self.close()


@dataclass
class BuildResult:
    """构建结果。"""
    files_scanned: int
    entities_created: int
    relations_created: int
```

---

### Task 2: CLI 入口 (cli.py)

```python
@click.group()
@click.option("--verbose", "-v", is_flag=True, help="详细输出")
def main(verbose: bool) -> None:
    """LayerKG — 本体驱动的可更新知识图谱引擎。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
def build(repo_path: str) -> None:
    """全量构建知识图谱。"""
    config = LayerKGConfig.from_env()
    with LayerKGBuilder(config) as builder:
        result = builder.build(Path(repo_path))
        click.echo(f"✅ Build complete: {result.files_scanned} files, "
                    f"{result.entities_created} entities, {result.relations_created} relations")

@main.command()
@click.argument("text")
@click.option("--type", "-t", "entity_type", help="实体类型过滤")
@click.option("--limit", "-n", default=10, help="返回数量")
def query(text: str, entity_type: str | None, limit: int) -> None:
    """语义搜索。"""
    config = LayerKGConfig.from_env()
    with LayerKGBuilder(config) as builder:
        results = builder.query(text, n_results=limit, entity_type=entity_type)
        if not results:
            click.echo("No results found.")
            return
        for r in results:
            click.echo(f"  [{r['metadata'].get('entity_type')}] {r['metadata'].get('name')} "
                        f"(distance: {r.get('distance', 'N/A'):.4f})")

@main.command()
def info() -> None:
    """显示配置和存储状态。"""
    config = LayerKGConfig.from_env()
    click.echo(f"Neo4j: {config.neo4j_uri}")
    click.echo(f"Ollama: {config.ollama_base_url}")
    click.echo(f"Model: {config.embedding_model}")
    click.echo(f"ChromaDB: {config.chroma_persist_dir}")
    with LayerKGBuilder(config) as builder:
        info_data = builder.info()
        click.echo(f"Entities in ChromaDB: {info_data.get('chroma_count', 'N/A')}")
```

---

### Task 3: Builder 单元测试 (test_builder.py)

**策略**: Mock Neo4jGraphStore 和 ChromaStore，只测试 Builder 逻辑。

```python
@pytest.fixture
def mock_stores():
    """Mock Neo4jGraphStore 和 ChromaStore。"""
    with patch("layerkg.builder.Neo4jGraphStore") as mock_graph, \
         patch("layerkg.builder.ChromaStore") as mock_chroma:
        yield mock_graph, mock_chroma

@pytest.fixture
def builder(mock_stores):
    config = LayerKGConfig(neo4j_password="test")
    return LayerKGBuilder(config)
```

**测试用例 (~15 tests)**:

1. `test_scan_python_files_finds_py_files` — 扫描 .py 文件
2. `test_scan_skips_hidden_dirs` — 跳过 .git, __pycache__ 等
3. `test_scan_empty_dir_returns_empty` — 空目录
4. `test_build_parses_all_files` — 全量构建解析所有文件
5. `test_build_skips_error_files` — 跳过解析失败的文件
6. `test_build_writes_to_graph_store` — 写 Neo4j
7. `test_build_writes_to_chroma_store` — 写 ChromaDB
8. `test_build_calls_ensure_constraints` — 确保约束
9. `test_build_returns_correct_counts` — 返回正确计数
10. `test_entity_to_dict_contains_required_fields` — entity→dict 转换
11. `test_entity_to_text_with_source` — 有 source 的实体
12. `test_entity_to_text_without_source` — 无 source 构造描述
13. `test_query_searches_chroma` — 查询调用 ChromaDB
14. `test_query_with_type_filter` — 带类型过滤的查询
15. `test_context_manager_closes_stores` — with 语句关闭资源

---

### Task 4: CLI 单元测试 (test_cli.py)

**策略**: 使用 Click 的 `CliRunner`，mock LayerKGBuilder。

```python
@pytest.fixture
def runner():
    return CliRunner()

@pytest.fixture
def mock_builder():
    with patch("layerkg.cli.LayerKGBuilder") as mock:
        yield mock
```

**测试用例 (~10 tests)**:

1. `test_main_help` — `layerkg --help` 正常输出
2. `test_build_command_with_valid_path` — 正常构建
3. `test_build_command_nonexistent_path_fails` — 路径不存在报错
4. `test_build_command_shows_summary` — 显示摘要
5. `test_query_command_returns_results` — 查询有结果
6. `test_query_command_no_results` — 查询无结果
7. `test_query_with_type_option` — --type 选项
8. `test_query_with_limit_option` — --limit 选项
9. `test_info_command_shows_config` — info 显示配置
10. `test_verbose_flag_sets_debug` — -v 设置 DEBUG

---

### Task 5: ruff + 全量测试 + 覆盖率

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest tests/ -v
uv run pytest tests/ --cov=layerkg --cov-report=term-missing
```

---

## 依赖关系
```
Task 1 (LayerKGBuilder)
  └── Task 2 (CLI)
       └── Task 3 (Builder 测试, 可与 Task 4 并行)
            └── Task 4 (CLI 测试)
                 └── Task 5 (验证)
```

## 预期结果
- `src/layerkg/builder.py` (~150 行)
- `src/layerkg/cli.py` (~120 行)
- `tests/unit/test_builder.py` (~350 行, 15 tests)
- `tests/unit/test_cli.py` (~200 行, 10 tests)
- 全量测试 165 + 25 = **190 tests**
- Phase 0 完成 🎉
