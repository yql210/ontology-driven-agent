from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from layerkg.domain.schema import CodeEntity


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
    """源码解析器抽象基类。"""

    @abstractmethod
    def parse_file(self, file_path: Path) -> ParseResult:
        """解析单个文件。

        Args:
            file_path: 源文件路径。

        Returns:
            ParseResult 包含提取的实体和关系。
        """

    @abstractmethod
    def parse_source(self, source: bytes, file_path: str = "<string>") -> ParseResult:
        """解析源码字符串。

        Args:
            source: 源码字节流。
            file_path: 虚拟文件路径（用于定位）。

        Returns:
            ParseResult 包含提取的实体和关系。
        """

    @property
    @abstractmethod
    def language(self) -> str:
        """解析器支持的语言名称。"""
