"""文档解析器：将 Markdown/RST 文件解析为 DocEntity 列表。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from layerkg.domain.schema import DocEntity

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

    # Markdown 标题正则：# / ## / ###
    _MD_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

    # RST 标题正则：下划线标题语法
    _RST_TITLE_RE = re.compile(r"^(.+)\n([=:`~\-]{3,})\s*$", re.MULTILINE)

    def parse_file(self, file_path: Path) -> DocParseResult:
        """解析文件，返回 DocParseResult。"""
        # 检查文件大小
        if file_path.stat().st_size > _MAX_DOC_SIZE:
            logger.warning(f"Skipping large file: {file_path} (>{_MAX_DOC_SIZE / 1024 / 1024}MB)")
            return DocParseResult(file_path=str(file_path))

        # 读文件：UTF-8 优先，失败回退 latin-1
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="latin-1")
            except Exception as e:
                return DocParseResult(file_path=str(file_path), error=str(e))

        # 按后缀分发
        suffix = file_path.suffix.lower()
        if suffix == ".md":
            entities = self._parse_markdown(content, str(file_path))
        elif suffix == ".rst":
            entities = self._parse_rst(content, str(file_path))
        else:
            return DocParseResult(file_path=str(file_path), error=f"Unsupported suffix: {suffix}")

        return DocParseResult(file_path=str(file_path), entities=entities)

    def parse_source(self, source: str, file_path: str = "<string>") -> DocParseResult:
        """解析源字符串，返回 DocParseResult。"""
        # 按 file_path 后缀判断类型
        if file_path.endswith(".md"):
            entities = self._parse_markdown(source, file_path)
        elif file_path.endswith(".rst"):
            entities = self._parse_rst(source, file_path)
        else:
            # 默认当作 Markdown
            entities = self._parse_markdown(source, file_path)

        return DocParseResult(file_path=file_path, entities=entities)

    def _detect_doc_type(self, file_path: str) -> str:
        """启发式检测文档类型。"""
        path_lower = file_path.lower()

        # README
        if "readme" in path_lower:
            return "readme"

        # docs/ + api
        if "docs/" in path_lower and "api" in path_lower:
            return "api_doc"

        # docs/ + 其他
        if "docs/" in path_lower:
            return "module_doc"

        # arch/design
        if "arch" in path_lower or "design" in path_lower:
            return "architecture_doc"

        # 默认
        return "comment"

    def _parse_markdown(self, source: str, file_path: str) -> list[DocEntity]:
        """Markdown 解析：按标题拆分 section → DocEntity。"""
        entities: list[DocEntity] = []

        # 空文件
        if not source.strip():
            return entities

        # 按标题拆分
        doc_type = self._detect_doc_type(file_path)

        # 查找所有标题位置
        matches = list(self._MD_HEADING_RE.finditer(source))
        if not matches:
            # 无标题：整文件一个 DocEntity
            name = Path(file_path).stem
            entities.append(
                DocEntity(
                    name=name,
                    entity_type=doc_type,
                    content=source,
                    file_path=file_path,
                    language="markdown",
                )
            )
            return entities

        # 按标题拆分 section
        for i, match in enumerate(matches):
            heading_text = match.group(2).strip()
            start_pos = match.end()
            # 下一个标题位置或文件末尾
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(source)

            # 提取内容（跳过标题后的空行）
            content = source[start_pos:end_pos].strip()

            entities.append(
                DocEntity(
                    name=heading_text,
                    entity_type=doc_type,
                    content=content,
                    file_path=file_path,
                    language="markdown",
                )
            )

        return entities

    def _parse_rst(self, source: str, file_path: str) -> list[DocEntity]:
        """RST 解析：按下划线标题拆分 → DocEntity。"""
        entities: list[DocEntity] = []

        # 空文件
        if not source.strip():
            return entities

        doc_type = self._detect_doc_type(file_path)

        # 查找所有 RST 标题
        matches = list(self._RST_TITLE_RE.finditer(source))
        if not matches:
            # 无标题：整文件一个 DocEntity
            name = Path(file_path).stem
            entities.append(
                DocEntity(
                    name=name,
                    entity_type=doc_type,
                    content=source,
                    file_path=file_path,
                    language="rst",
                )
            )
            return entities

        # 按标题拆分 section
        for i, match in enumerate(matches):
            heading_text = match.group(1).strip()
            start_pos = match.end()
            # 下一个标题位置或文件末尾
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(source)

            # 提取内容
            content = source[start_pos:end_pos].strip()

            entities.append(
                DocEntity(
                    name=heading_text,
                    entity_type=doc_type,
                    content=content,
                    file_path=file_path,
                    language="rst",
                )
            )

        return entities
