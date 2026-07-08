from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ontoagent.domain.schema import CodeEntity


@dataclass
class ExtractedRelation:
    """解析阶段提取的关系（使用实体名称，非 UUID）。

    这是中间表示。存储到 GraphStore 前，需要通过 EntityResolver
    将 source_name/target_name 解析为 UUID，转换为 schema.Relation。

    Attributes:
        source_name: 源实体名称。
        source_type: 源实体类型（function/class/module）。
        target_name: 目标实体名称。
        target_type: 目标实体类型。
        relation_type: 关系类型（contains/extends/imports）。
        file_path: 所属文件路径。
    """

    source_name: str
    source_type: str
    target_name: str
    target_type: str
    relation_type: str
    file_path: str


@dataclass
class ParseResult:
    """单个文件的解析结果。

    Attributes:
        file_path: 源文件路径。
        entities: 提取到的 CodeEntity 列表（强类型）。
        relations: 提取到的关系信息列表（中间表示）。
        language: 文件语言。
        error: 解析错误信息（可选）。
    """

    file_path: str
    entities: list[CodeEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
    language: str = "python"
    error: str | None = None


class BaseParser(ABC):
    """源码解析器抽象基类。

    Implements a template method pattern: ``parse_source`` and ``parse_file``
    are concrete methods in the base class. Subclasses provide language-specific
    hooks (``_create_root_entity``, ``_extract_external_calls``, ``_pre_scan``,
    ``_walk``, ``_on_parse_error``).
    """

    def parse_file(self, file_path: Path) -> ParseResult:
        """解析单个文件。"""
        if not file_path.exists():
            return ParseResult(file_path=str(file_path), error=f"File not found: {file_path}")
        try:
            source_bytes = file_path.read_bytes()
            return self.parse_source(source_bytes, str(file_path))
        except OSError as e:
            return ParseResult(file_path=str(file_path), error=f"Failed to read file: {e}")

    def parse_source(self, source: bytes, file_path: str = "<string>") -> ParseResult:
        """解析源码字节流（模板方法）。

        Common skeleton: create root entity → parse AST → walk → extract
        external calls. Subclasses only implement the hook methods.
        """
        entities: list[CodeEntity] = []
        relations: list[ExtractedRelation] = []

        # 创建根实体（module/file）
        root_entity = self._create_root_entity(source, file_path)
        entities.append(root_entity)

        try:
            tree = self._parser.parse(source)
            root_node = tree.root_node

            # 子类预扫描返回 root_name（Python: module name, Java: package name or None）
            pre_scan_result = self._pre_scan(root_node, source, file_path, entities, relations)
            # walk 接收 pre_scan 原始结果（可能为 None）；extract_external_calls 用 fallback
            root_name = pre_scan_result if pre_scan_result is not None else root_entity.name

            # 递归遍历 AST
            self._walk(
                root_node,
                source,
                file_path,
                entities,
                relations,
                pre_scan_result,
                parent_class_name=None,
            )

            # 外部调用提取
            relations.extend(self._extract_external_calls(root_node, source, file_path, root_name))

        except Exception as e:
            self._on_parse_error(file_path, e)

        return ParseResult(
            file_path=file_path,
            entities=entities,
            relations=relations,
            language=self.language,
        )

    # -- Hooks for subclasses --

    @abstractmethod
    def _create_root_entity(self, source: bytes, file_path: str) -> CodeEntity:
        """Create the root entity (module for Python, file for Java)."""

    def _pre_scan(self, root_node, source, file_path, entities, relations) -> str | None:
        """Optional pre-scan before AST walk. Returns root name override or None."""
        return None

    @abstractmethod
    def _walk(self, node, source, file_path, entities, relations, module_name, parent_class_name=None) -> None:
        """Recursively walk the AST tree and populate entities + relations."""

    @abstractmethod
    def _extract_external_calls(self, root_node, source, file_path, module_name) -> list[ExtractedRelation]:
        """Extract external call relations from the AST."""

    def _on_parse_error(self, file_path: str, error: Exception) -> None:
        """Handle parse errors. Default: log warning."""
        import logging
        logging.getLogger(__name__).warning("Parse failed for %s: %s", file_path, error)

    @property
    @abstractmethod
    def language(self) -> str:
        """解析器支持的语言名称。"""
