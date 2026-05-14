# Day 3 实现计划：Builder 多语言支持

> 目标：让 LayerKGBuilder 能用 JavaParser 解析 .java 文件，接入构建流水线。

## 当前状态
- `LayerKGBuilder.__init__` 第 141 行：`self._parser = PythonParser()` — 硬编码单 parser
- `_stage_parse` 第 204 行：`py_files, doc_files = self._scan_files(repo_path)` → 只扫 .py
- `_scan_files` 返回 `(py_files, doc_files)` — 多个测试依赖此签名
- 874 tests passed

## 设计方案

**不改 `_scan_files` 签名**。改其内部实现和 `_stage_parse` 消费方式。

### 改动 1：`_scan_files` 内部扩展扫描 .java

文件：`src/layerkg/builder.py`，第 571-598 行

**当前**：
```python
def _scan_files(self, repo_path: Path) -> tuple[list[Path], list[Path]]:
    skip_dirs = self._config.build_skip_dirs
    py_files: list[Path] = []
    doc_files: list[Path] = []
    # 扫描 Python 文件
    for p in repo_path.rglob("*.py"):
        ...
        py_files.append(p)
    # 扫描文档文件
    ...
    return sorted(py_files), sorted(doc_files)
```

**改为**：
```python
def _scan_files(self, repo_path: Path) -> tuple[list[Path], list[Path]]:
    skip_dirs = self._config.build_skip_dirs
    code_files: list[Path] = []  # 所有代码文件（.py + .java）
    doc_files: list[Path] = []

    # 扫描代码文件
    for p in repo_path.rglob("*"):
        if any(skip in p.parts or skip in p.name for skip in skip_dirs):
            continue
        if p.suffix == ".py" or p.suffix == ".java":
            code_files.append(p)

    # 扫描文档文件
    if self._config.build_include_docs:
        for ext in self._config.build_doc_extensions:
            for p in repo_path.rglob(f"*{ext}"):
                if any(skip in p.parts or skip in p.name for skip in skip_dirs):
                    continue
                doc_files.append(p)

    return sorted(code_files), sorted(doc_files)
```

**注意**：返回值第一个仍是"代码文件列表"，但不再只有 .py。现有测试中 `py_files` 变量名可能变成混合 .py 和 .java。

### 改动 2：`__init__` 改为多 parser 注册

第 140-141 行：
```python
# 当前:
self._parser = PythonParser()

# 改为:
self._parsers: dict[str, BaseParser] = {}
self._register_parser(PythonParser())
self._register_parser(JavaParser())

def _register_parser(self, parser: BaseParser) -> None:
    """注册解析器。"""
    # 按扩展名映射：parser.language → 扩展名
    lang_to_ext = {"python": ".py", "java": ".java"}
    ext = lang_to_ext.get(parser.language)
    if ext:
        self._parsers[ext] = parser

def _get_parser(self, file_path: Path) -> BaseParser | None:
    """根据文件扩展名获取对应解析器。"""
    return self._parsers.get(file_path.suffix)
```

### 改动 3：`_stage_parse` 使用多 parser

第 204-216 行：
```python
# 当前:
py_files, doc_files = self._scan_files(repo_path)
self._logger.info("Scanned %d Python files, %d doc files", len(py_files), len(doc_files))
all_entities: list[CodeEntity] = []
skipped_files = 0
for file_path in py_files:
    result = self._parser.parse_file(file_path)
    ...

# 改为:
code_files, doc_files = self._scan_files(repo_path)
# 统计各语言文件数
lang_counts: dict[str, int] = {}
for f in code_files:
    lang = f.suffix.lstrip(".")
    lang_counts[lang] = lang_counts.get(lang, 0) + 1
self._logger.info("Scanned %s code files, %d doc files", lang_counts, len(doc_files))

all_entities: list[CodeEntity] = []
skipped_files = 0
for file_path in code_files:
    parser = self._get_parser(file_path)
    if parser is None:
        self._logger.warning("No parser for %s, skipping", file_path.suffix)
        skipped_files += 1
        continue
    result = parser.parse_file(file_path)
    ...
```

### 改动 4：import 更新

第 20 行：
```python
# 当前:
from layerkg.parser.python_parser import PythonParser

# 改为:
from layerkg.parser.base import BaseParser
from layerkg.parser.java_parser import JavaParser
from layerkg.parser.python_parser import PythonParser
```

### 改动 5：外部 import 的 language 字段

第 288 行附近，创建外部 module 实体时硬编码了 `language="python"`：
```python
ext_entity = CodeEntity(
    name=ext_name,
    entity_type="module",
    file_path="__external__",
    language="python",  # 需要改为动态或用 "unknown"
)
```

改为 `language="unknown"` 或从 unresolved_import 上下文推断。

## 不修改的文件
- `schema.py` — 已在 Day 0 改完
- `java_parser.py` — 已在 Day 1-2 完成
- `python_parser.py` — 不碰
- `parser/__init__.py` — 已在 Day 1 导出 JavaParser

## 测试更新

### `tests/unit/test_builder.py`

现有 `_scan_files` 测试（第 56-139 行）需要注意：
- `test_scan_files_finds_py_files` — 现在返回 `code_files`，可能包含 .java
- 这些测试创建的临时文件只有 .py，所以行为不变
- 但变量名 `py_files` 改成 `code_files` 后要更新断言语句
- **关键是**：如果改了 `_scan_files` 实现为 `rglob("*")` + 后缀过滤，性能可能变差。更好的做法是分别 rglob `.py` 和 `.java`：

```python
# 扫描代码文件
for suffix in (".py", ".java"):
    for p in repo_path.rglob(f"*{suffix}"):
        if any(skip in p.parts or skip in p.name for skip in skip_dirs):
            continue
        code_files.append(p)
```

### 新增测试

在 `tests/unit/test_builder.py` 追加：

```python
# 多语言支持测试
test_scan_files_finds_java_files           # .java 文件被扫描到
test_scan_files_mixed_py_and_java          # .py + .java 混合
test_get_parser_python                     # .py → PythonParser
test_get_parser_java                       # .java → JavaParser
test_get_parser_unknown_suffix             # .rs → None
test_stage_parse_uses_java_parser          # _stage_parse 能解析 .java
test_builder_external_import_language      # 外部 import 的 language 不是硬编码 "python"
```

## 执行顺序

1. 基线测试确认 874 passed
2. 修改 `builder.py`：
   - import 更新
   - `__init__` 多 parser 注册
   - `_scan_files` 扫描 .java
   - `_stage_parse` 多 parser 消费
   - 外部 import language 修正
3. 更新 `tests/unit/test_builder.py`（变量名等）
4. 追加新测试
5. 跑测试直到全绿
6. `ruff check` + `ruff format`
7. 全量测试确认 880+ passed
8. `git add -A && git commit -m "feat(builder): multi-language parser registry for Python + Java (Day 3)"`

## 验证标准

- `uv run pytest tests/ -v --tb=no -q` 全绿
- `builder._get_parser(Path("Foo.java")).language == "java"`
- `builder._get_parser(Path("foo.py")).language == "python"`
- `builder._get_parser(Path("foo.rs"))` is None
- `_scan_files` 扫描目录同时返回 .py 和 .java 文件
- `_stage_parse` 对 .java 文件使用 JavaParser 解析
