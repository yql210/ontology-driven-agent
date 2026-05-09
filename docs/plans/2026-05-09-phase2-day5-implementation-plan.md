# Phase 2 Day 5 实施计划：文档摄入 (DocParser + describes 关系)

> 基于 `docs/plans/2026-05-09-phase2-day5-doc-intake-proposal.md` 方案（审核评分 9.2/10）

## 概览

- **新增文件**：`src/layerkg/parser/doc_parser.py`，`tests/unit/test_doc_parser.py`
- **修改文件**：`src/layerkg/builder.py`，`src/layerkg/parser/__init__.py`
- **目标**：616 → ~630 tests

## Task 1：DocParseResult dataclass + DocParser 骨架

**文件**：`src/layerkg/parser/doc_parser.py`（新建）

```python
"""文档解析器：将 Markdown/RST 文件解析为 DocEntity 列表。"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path

from layerkg.schema import DocEntity

logger = logging.getLogger(__name__)

_MAX_DOC_SIZE = 5 * 1024 * 1024  # 5MB


@dataclass
class DocParseResult:
    """文档解析结果。"""

    file_path: str
    entities: list[DocEntity] = field(default_factory=list)
    error: str | None = None


class DocParser:
    """Markdown/RST 文档解析器，产出 DocEntity。"""

    def parse_file(self, file_path: Path) -> DocParseResult:
        """解析文件，返回 DocParseResult。"""
        ...

    def parse_source(self, source: str, file_path: str = "<string>") -> DocParseResult:
        """解析源字符串，返回 DocParseResult。"""
        ...

    def _detect_doc_type(self, file_path: str) -> str:
        """启发式检测文档类型。"""
        ...

    def _parse_markdown(self, source: str, file_path: str) -> list[DocEntity]:
        """Markdown 解析：按标题拆分 section → DocEntity。"""
        ...

    def _parse_rst(self, source: str, file_path: str) -> list[DocEntity]:
        """RST 解析：按下划线标题拆分 → DocEntity。"""
        ...
```

**文件**：`tests/unit/test_doc_parser.py`（新建）

```python
"""DocParser 单元测试。"""

from pathlib import Path
from layerkg.parser.doc_parser import DocParser, DocParseResult


class TestDocParseResult:
    def test_doc_parse_result_dataclass(self):
        """DocParseResult 基础 dataclass 功能。"""
        result = DocParseResult(file_path="test.md")
        assert result.file_path == "test.md"
        assert result.entities == []
        assert result.error is None
```

**验证**：`uv run pytest tests/unit/test_doc_parser.py -v`

---

## Task 2：Markdown 解析

**文件**：`src/layerkg/parser/doc_parser.py`

实现 `_parse_markdown()`：
- 按 `# ` / `## ` / `### ` 标题正则拆分 section
- 每个 section → 一个 DocEntity（name=标题文本, content=正文+代码块）
- 无标题文件 → 整文件作为一个 DocEntity（name=文件名去掉扩展名）
- 空文件 → 返回空列表
- 编码：UTF-8 优先，失败回退 latin-1

实现 `parse_file()`：
- 检查文件大小 > `_MAX_DOC_SIZE` → warning + 返回空 entities
- 读文件（UTF-8 + latin-1 回退）
- 按 `.md` / `.rst` 后缀分发到对应解析器

实现 `parse_source()`：
- 按 file_path 后缀判断 Markdown/RST
- 直接调用 `_parse_markdown()` 或 `_parse_rst()`

**文件**：`tests/unit/test_doc_parser.py`

```python
class TestDocParserMarkdown:
    def test_doc_parser_markdown_basic(self, tmp_path):
        """README → 多个 DocEntity（按标题拆分）。"""
        md = tmp_path / "README.md"
        md.write_text("# Intro\nSome intro text\n## Usage\nHow to use\n")
        parser = DocParser()
        result = parser.parse_file(md)
        assert len(result.entities) == 2
        assert result.entities[0].name == "Intro"
        assert "intro text" in result.entities[0].content
        assert result.entities[1].name == "Usage"

    def test_doc_parser_markdown_no_title(self, tmp_path):
        """无标题 → 整文件一个 DocEntity，name=文件名。"""
        md = tmp_path / "notes.md"
        md.write_text("Just some text without headers\n")
        parser = DocParser()
        result = parser.parse_file(md)
        assert len(result.entities) == 1
        assert result.entities[0].name == "notes"

    def test_doc_parser_code_blocks(self):
        """代码块包含在 content 中。"""
        source = "# Guide\n```python\nprint('hello')\n```\n"
        parser = DocParser()
        result = parser.parse_source(source, "guide.md")
        assert len(result.entities) == 1
        assert "print('hello')" in result.entities[0].content

    def test_doc_parser_empty_content(self, tmp_path):
        """有标题无正文仍创建 DocEntity（content 为空字符串）。"""
        md = tmp_path / "sparse.md"
        md.write_text("# Title Only\n# Next\n")
        parser = DocParser()
        result = parser.parse_file(md)
        assert len(result.entities) == 2

    def test_doc_parser_large_file_skip(self, tmp_path):
        """超大文件（>5MB）跳过。"""
        big = tmp_path / "huge.md"
        big.write_bytes(b"x" * (6 * 1024 * 1024))
        parser = DocParser()
        result = parser.parse_file(big)
        assert result.entities == []
        assert result.error is None  # 不算错误，是 warning 跳过
```

**验证**：`uv run pytest tests/unit/test_doc_parser.py -v`

---

## Task 3：文档类型检测 + RST 解析

**文件**：`src/layerkg/parser/doc_parser.py`

实现 `_detect_doc_type()`：
- 路径含 `README`（不区分大小写）→ `"readme"`
- 路径含 `docs/` + 文件名含 `api` → `"api_doc"`
- 路径含 `docs/` + 其他 → `"module_doc"`
- 路径含 `arch` 或 `design` → `"architecture_doc"`
- 其他 → `"comment"`

实现 `_parse_rst()`：
- section 标题识别：`===` / `---` / `~~~` 下划线语法
- 其余逻辑与 markdown 类似

**文件**：`tests/unit/test_doc_parser.py`

```python
class TestDocParserTypeDetection:
    def test_type_detection_readme(self):
        parser = DocParser()
        assert parser._detect_doc_type("README.md") == "readme"
        assert parser._detect_doc_type("docs/readme.rst") == "readme"

    def test_type_detection_api_doc(self):
        parser = DocParser()
        assert parser._detect_doc_type("docs/api_overview.md") == "api_doc"

    def test_type_detection_module_doc(self):
        parser = DocParser()
        assert parser._detect_doc_type("docs/guide.md") == "module_doc"

    def test_type_detection_architecture(self):
        parser = DocParser()
        assert parser._detect_doc_type("design/arch.md") == "architecture_doc"

    def test_type_detection_comment(self):
        parser = DocParser()
        assert parser._detect_doc_type("notes/something.md") == "comment"


class TestDocParserRST:
    def test_doc_parser_rst_basic(self, tmp_path):
        """.rst → DocEntity。"""
        rst = tmp_path / "index.rst"
        rst.write_text("Introduction\n============\nSome text here\n")
        parser = DocParser()
        result = parser.parse_file(rst)
        assert len(result.entities) == 1
        assert result.entities[0].name == "Introduction"
```

**验证**：`uv run pytest tests/unit/test_doc_parser.py -v`

---

## Task 4：Builder _scan_files() + _stage_parse() 扩展

**文件**：`src/layerkg/builder.py`

1. `_scan_python_files()` → `_scan_files()`，返回 `tuple[list[Path], list[Path]]`
   - Python: `*.py`，现有 skip_dirs
   - Doc: `*.md`, `*.rst`，同样 skip_dirs + 额外跳过 `site/`

2. `_stage_parse()` 返回值从三元组扩展为四元组：
   ```python
   def _stage_parse(self, repo_path: Path) -> tuple[list[CodeEntity], list[DocEntity], list[Relation], int]:
   ```
   - 内部新增 DocParser lazy init（`_get_doc_parser()`）和文档解析循环
   - 文档解析错误 warning + continue，不中止

3. 新增 `_get_doc_parser()` 方法：
   ```python
   def _get_doc_parser(self) -> DocParser:
       from layerkg.parser.doc_parser import DocParser
       return DocParser()
   ```

4. **同步更新 build() 第 279 行解包**：
   ```python
   # 修改前：
   all_entities, relations, files_scanned = self._stage_parse(repo_path)
   # 修改后：
   all_entities, doc_entities, relations, files_scanned = self._stage_parse(repo_path)
   ```

**文件**：`tests/unit/test_doc_parser.py`

```python
class TestScanFiles:
    def test_scan_files_returns_both(self, tmp_path):
        """同时返回 .py 和 .md/.rst 文件。"""
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.md").write_text("# Hello")
        (tmp_path / "c.rst").write_text("Title\n=====\n")
        py, doc = LayerKGBuilder._scan_files(tmp_path)
        assert len(py) == 1
        assert len(doc) == 2

    def test_scan_files_skips_site(self, tmp_path):
        """跳过 site/ 目录（MkDocs/Sphinx 产物）。"""
        site = tmp_path / "site"
        site.mkdir()
        (site / "index.html").write_text("<html>")
        (site / "page.md").write_text("# Built")
        (tmp_path / "guide.md").write_text("# Guide")
        py, doc = LayerKGBuilder._scan_files(tmp_path)
        assert len(doc) == 1  # 只有 guide.md，site/page.md 跳过
```

**验证**：`uv run pytest tests/unit/test_doc_parser.py tests/unit/test_builder.py -v`

---

## Task 5：Builder _doc_entity_to_dict() + _stage_write_structural() 扩展

**文件**：`src/layerkg/builder.py`

1. 新增 `_doc_entity_to_dict()` 静态方法：
   ```python
   @staticmethod
   def _doc_entity_to_dict(entity: DocEntity) -> dict[str, Any]:
       d: dict[str, Any] = {"id": entity.id, "name": entity.name, "entity_type": entity.entity_type}
       if entity.file_path:
           d["file_path"] = entity.file_path
       if entity.content:
           d["content"] = entity.content
       if entity.language:
           d["language"] = entity.language
       return d
   ```

2. `_stage_write_structural()` 增加 `doc_entities` 参数：
   ```python
   def _stage_write_structural(
       self,
       all_entities: list[CodeEntity],
       doc_entities: list[DocEntity],
       relations: list[Relation],
   ) -> Neo4jGraphStore:
   ```
   - 在写入 CodeEntity 之后，循环写入 DocEntity：
   ```python
   for doc in doc_entities:
       graph_store.merge_node("DocEntity", self._doc_entity_to_dict(doc))
   ```

**文件**：`tests/unit/test_doc_parser.py`

```python
class TestDocEntityToDict:
    def test_doc_entity_to_dict(self):
        """_doc_entity_to_dict 正确序列化 DocEntity。"""
        doc = DocEntity(name="Guide", entity_type="readme", content="Hello", file_path="README.md")
        d = LayerKGBuilder._doc_entity_to_dict(doc)
        assert d["id"] == doc.id
        assert d["name"] == "Guide"
        assert d["entity_type"] == "readme"
        assert d["content"] == "Hello"
        assert d["file_path"] == "README.md"
```

**验证**：`uv run pytest tests/unit/test_doc_parser.py tests/unit/test_builder.py -v`

---

## Task 6：Builder _link_docs_to_code() + describes 关系

**文件**：`src/layerkg/builder.py`

1. 新增 `_link_docs_to_code()` 方法：
   - 参数：`doc_entities: list[DocEntity]`, `entity_index: dict[tuple[str, str, str], list[str]]`
   - 返回：`list[Relation]`
   - 路径匹配：带边界检查（`_BOUNDARY_CHARS`），防止子串误匹配
   - 函数名匹配：从代码块提取标识符，匹配 entity_index 中的 name（长度 > 3）
   - 每个 DocEntity 上限 50 条 describes 关系

2. 新增 `_extract_identifiers_from_code()` 辅助方法：
   ```python
   _CODE_BLOCK_RE = re.compile(r'```python\n(.*?)\n```', re.DOTALL)
   _IDENTIFIER_RE = re.compile(r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b')

   def _extract_identifiers_from_code(self, code: str) -> set[str]:
       """从 Markdown 代码块中提取 Python 标识符。"""
       identifiers: set[str] = set()
       for match in self._CODE_BLOCK_RE.finditer(code):
           for id_match in self._IDENTIFIER_RE.finditer(match.group(1)):
               identifiers.add(id_match.group(1))
       return identifiers
   ```

3. 更新 `build()` 流水线：
   - Stage 2.5（新增）：在 Stage 2 之后、Stage 3 之前
   ```python
   # Stage 2.5: 文档→代码关联
   entity_index = self._build_entity_index(all_entities, repo_path)
   describes_rels = self._link_docs_to_code(doc_entities, entity_index)
   for rel in describes_rels:
       graph_store.merge_relation(rel.source_id, rel.target_id, rel.relation_type)
   ```
   - `relations_created` 增加 `len(describes_rels)`
   - `doc_entities_created = len(doc_entities)`
   - **注意**：`_build_entity_index(all_entities, repo_path)` 只传 CodeEntity，不传 DocEntity（避免 DocEntity 污染 index）

**文件**：`tests/unit/test_doc_parser.py`

```python
class TestLinkDocsToCode:
    def test_link_docs_to_code_path_match(self):
        """路径匹配 → describes 关系。"""
        # 构造 doc_entities + entity_index，验证路径匹配生成 Relation
        ...

    def test_link_docs_to_code_name_match(self):
        """函数名匹配（路径范围内）。"""
        ...

    def test_link_docs_to_code_path_collision(self):
        """路径名子串不误匹配（foo.py vs foo_backup.py）。"""
        ...
```

**验证**：`uv run pytest tests/unit/test_doc_parser.py tests/unit/test_builder.py -v`

---

## Task 7：Builder _write_all_vectors() 扩展 + parser/__init__.py 更新

**文件**：`src/layerkg/builder.py`

1. `_write_all_vectors()` 新增 `doc_entities: list[DocEntity]` 参数：
   ```python
   def _write_all_vectors(
       self,
       all_entities: list[CodeEntity],
       doc_entities: list[DocEntity],
       new_concepts: list[ConceptEntity],
       clusters: list[ModuleCluster],
   ) -> None:
   ```
   - DocEntity 向量写入：
   ```python
   for doc in doc_entities:
       text = (doc.content or "")[:2000]
       if text.strip():
           items.append((doc.id, text, {"entity_type": doc.entity_type, "name": doc.name}))
   ```
   - 更新 `build()` 中的调用：`self._write_all_vectors(all_entities, doc_entities, new_concepts, clusters)`

**文件**：`src/layerkg/parser/__init__.py`

```python
from layerkg.parser.doc_parser import DocParser, DocParseResult

__all__ = [..., "DocParser", "DocParseResult"]
```

**文件**：`tests/unit/test_doc_parser.py`

```python
class TestDocEntitiesInBuildResult:
    def test_doc_entities_in_build_result(self, tmp_path, mock_graph_store, mock_chroma_store):
        """BuildResult.doc_entities_created > 0。"""
        # 创建含 .md 文件的临时目录
        # mock builder 的依赖
        # 验证 result.doc_entities_created > 0
        ...
```

**验证**：`uv run pytest tests/ -v`

---

## Task 8：质量收尾

1. 全量回归：`uv run pytest tests/ -v`
2. ruff 检查：`uv run ruff check src/ tests/` + `uv run ruff format --check src/ tests/`
3. 修复所有 lint 问题
4. 确认测试数量：616 → ~630

---

## 执行批次

| 批次 | Tasks | 内容 | max-turns |
|------|-------|------|-----------|
| Batch 1 | 1-3 | DocParser 完整实现 + 测试 | 50 |
| Batch 2 | 4-7 | Builder 集成（scan/parse/write/link/vector） | 50 |
| Batch 3 | 8 | 质量收尾 | 15 |
