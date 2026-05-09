"""DocParser 单元测试。"""

from __future__ import annotations

from pathlib import Path

from layerkg.builder import LayerKGBuilder
from layerkg.parser.doc_parser import DocParser, DocParseResult
from layerkg.schema import DocEntity


class TestDocParseResult:
    def test_doc_parse_result_dataclass(self):
        """DocParseResult 基础 dataclass 功能。"""
        result = DocParseResult(file_path="test.md")
        assert result.file_path == "test.md"
        assert result.entities == []
        assert result.error is None

    def test_doc_parse_result_with_entities(self):
        """DocParseResult 带 DocEntity 列表。"""
        doc = DocEntity(name="Guide", entity_type="readme")
        result = DocParseResult(file_path="README.md", entities=[doc])
        assert len(result.entities) == 1
        assert result.entities[0].name == "Guide"

    def test_doc_parse_result_with_error(self):
        """DocParseResult 带错误信息。"""
        result = DocParseResult(file_path="missing.md", error="File not found")
        assert result.error == "File not found"
        assert result.entities == []


class TestDocParserMarkdown:
    def test_doc_parser_markdown_basic(self, tmp_path: Path):
        """README → 多个 DocEntity（按标题拆分）。"""
        md = tmp_path / "README.md"
        md.write_text("# Intro\nSome intro text\n## Usage\nHow to use\n", encoding="utf-8")
        parser = DocParser()
        result = parser.parse_file(md)
        assert len(result.entities) == 2
        assert result.entities[0].name == "Intro"
        assert "intro text" in result.entities[0].content.lower()
        assert result.entities[1].name == "Usage"

    def test_doc_parser_markdown_no_title(self, tmp_path: Path):
        """无标题 → 整文件一个 DocEntity，name=文件名。"""
        md = tmp_path / "notes.md"
        md.write_text("Just some text without headers\n", encoding="utf-8")
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

    def test_doc_parser_empty_content(self, tmp_path: Path):
        """有标题无正文仍创建 DocEntity（content 为空字符串）。"""
        md = tmp_path / "sparse.md"
        md.write_text("# Title Only\n# Next\n", encoding="utf-8")
        parser = DocParser()
        result = parser.parse_file(md)
        assert len(result.entities) == 2

    def test_doc_parser_large_file_skip(self, tmp_path: Path):
        """超大文件（>5MB）跳过。"""
        big = tmp_path / "huge.md"
        big.write_bytes(b"x" * (6 * 1024 * 1024))
        parser = DocParser()
        result = parser.parse_file(big)
        assert result.entities == []
        assert result.error is None  # 不算错误，是 warning 跳过


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
    def test_doc_parser_rst_basic(self, tmp_path: Path):
        """.rst → DocEntity。"""
        rst = tmp_path / "index.rst"
        rst.write_text("Introduction\n============\nSome text here\n", encoding="utf-8")
        parser = DocParser()
        result = parser.parse_file(rst)
        assert len(result.entities) == 1
        assert result.entities[0].name == "Introduction"


class TestScanFiles:
    def test_scan_files_returns_both(self, tmp_path: Path):
        """同时返回 .py 和 .md/.rst 文件。"""
        from layerkg.config import LayerKGConfig

        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.md").write_text("# Hello")
        (tmp_path / "c.rst").write_text("Title\n=====\n")
        config = LayerKGConfig(build_include_docs=True)
        builder = LayerKGBuilder(config)
        _py, doc = builder._scan_files(tmp_path)
        assert len(_py) == 1
        assert len(doc) == 2

    def test_scan_files_skips_site(self, tmp_path: Path):
        """跳过 site/ 目录（MkDocs/Sphinx 产物）。"""
        from layerkg.config import LayerKGConfig

        site = tmp_path / "site"
        site.mkdir()
        (site / "index.html").write_text("<html>")
        (site / "page.md").write_text("# Built")
        (tmp_path / "guide.md").write_text("# Guide")
        config = LayerKGConfig(build_include_docs=True)
        builder = LayerKGBuilder(config)
        _py, doc = builder._scan_files(tmp_path)
        assert len(doc) == 1  # 只有 guide.md，site/page.md 跳过


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


class TestExtractIdentifiersFromCode:
    def test_extract_identifiers_from_code_basic(self):
        """从 Python 代码块提取标识符。"""
        code = "```python\ndef hello_world():\n    pass\n```"
        ids = LayerKGBuilder._extract_identifiers_from_code(code)
        assert "hello_world" in ids
        # 注意：当前实现不区分关键字和标识符，"def" 也会被匹配

    def test_extract_identifiers_from_code_min_length(self):
        """只提取长度 >=3 的标识符。"""
        code = "```python\ndef ab():\n    xyz = 1\n```"
        ids = LayerKGBuilder._extract_identifiers_from_code(code)
        assert "xyz" in ids
        assert "ab" not in ids  # 长度 < 3


class TestLinkDocsToCode:
    def test_link_docs_to_code_path_match(self):
        """路径匹配 → describes 关系。"""
        # 创建 doc_entities，content 包含 "module1.py"
        doc = DocEntity(
            name="Module Guide",
            entity_type="module_doc",
            content="See module1.py for details",
            file_path="docs/guide.md",
        )
        # 创建 entity_index，包含 module1.py 的实体
        entity_index = {
            ("function", "module1.py", "foo"): ["code-id-1"],
        }
        builder = LayerKGBuilder.__new__(LayerKGBuilder)
        rels = builder._link_docs_to_code([doc], entity_index)
        assert len(rels) == 1
        assert rels[0].source_id == doc.id
        assert rels[0].target_id == "code-id-1"
        assert rels[0].relation_type == "describes"

    def test_link_docs_to_code_name_match(self):
        """函数名匹配（从代码块提取标识符）。"""
        doc = DocEntity(
            name="API Reference",
            entity_type="api_doc",
            content="```python\ndef process_data():\n    pass\n```",
            file_path="docs/api.md",
        )
        entity_index = {
            ("function", "", "process_data"): ["code-id-2"],
        }
        builder = LayerKGBuilder.__new__(LayerKGBuilder)
        rels = builder._link_docs_to_code([doc], entity_index)
        assert len(rels) == 1
        assert rels[0].target_id == "code-id-2"

    def test_link_docs_to_code_path_collision(self):
        """路径名子串不误匹配（foo.py vs foo_backup.py）。"""
        doc = DocEntity(
            name="Foo Guide",
            entity_type="module_doc",
            content="Content",
            file_path="docs/foo_guide.md",
        )
        # foo.py 和 foo_guide.md 有子串关系 "foo"
        entity_index = {
            ("function", "foo.py", "bar"): ["code-id-3"],
            ("function", "foo_guide.py", "baz"): ["code-id-4"],
        }
        builder = LayerKGBuilder.__new__(LayerKGBuilder)
        rels = builder._link_docs_to_code([doc], entity_index)
        # 由于有边界检查，不应该匹配到 foo.py
        # 只应该匹配到 foo_guide.py（如果存在）
        assert all(rel.target_id != "code-id-3" for rel in rels)

    def test_link_docs_to_code_max_rels_per_doc(self):
        """每个文档最多 50 条 describes 关系。"""
        doc = DocEntity(
            name="Many Entities",
            entity_type="module_doc",
            content="Content",
            file_path="docs/many.md",
        )
        # 创建超过 50 个实体
        entity_index = {("function", f"file{i}.py", f"func{i}"): [f"code-id-{i}"] for i in range(100)}
        builder = LayerKGBuilder.__new__(LayerKGBuilder)
        rels = builder._link_docs_to_code([doc], entity_index)
        assert len(rels) <= 50
