"""Builder 流水线中无状态的纯工具函数。

这些函数从 ``LayerKGBuilder`` 抽离出来，便于复用与单元测试，不依赖任何 builder 实例状态。
"""

from __future__ import annotations

import re
from pathlib import Path

from layerkg.domain.schema import CodeEntity, DocEntity

# 路径边界字符（用于防止子串误匹配）
BOUNDARY_CHARS = " ./\\-_"

# Markdown Python 代码块 + 标识符正则
CODE_BLOCK_RE = re.compile(r"```python\n(.*?)\n```", re.DOTALL)
IDENTIFIER_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b")


def entity_to_dict(entity: CodeEntity) -> dict:
    """将 CodeEntity 转为 Neo4j 属性字典。

    Args:
        entity: 代码实体。

    Returns:
        属性字典，仅包含非空字段。
    """
    d: dict[str, object] = {
        "id": entity.id,
        "name": entity.name,
        "entity_type": entity.entity_type,
    }
    if entity.file_path:
        d["file_path"] = entity.file_path
    if entity.start_line is not None:
        d["start_line"] = entity.start_line
    if entity.end_line is not None:
        d["end_line"] = entity.end_line
    if entity.language:
        d["language"] = entity.language
    if entity.docstring:
        d["docstring"] = entity.docstring
    if entity.parameters:
        d["code_parameters"] = entity.parameters
    return d


def doc_entity_to_dict(entity: DocEntity) -> dict:
    """将 DocEntity 转为 Neo4j 属性字典。

    Args:
        entity: 文档实体。

    Returns:
        属性字典，仅包含非空字段。
    """
    d: dict[str, str] = {
        "id": entity.id,
        "name": entity.name,
        "entity_type": entity.entity_type,
    }
    if entity.file_path:
        d["file_path"] = entity.file_path
    if entity.content:
        d["content"] = entity.content
    if entity.language:
        d["language"] = entity.language
    return d


def entity_to_text(entity: CodeEntity, max_length: int) -> str | None:
    """提取实体的可嵌入文本。

    优先返回截断后的源代码；若无源码，则构造 ``"<type> <name> in <path>"`` 形式的描述。

    Args:
        entity: 代码实体。
        max_length: 源代码最大长度（截断）。

    Returns:
        可嵌入的文本（永不为空字符串；无内容时返回构造的最小描述）。
    """
    if entity.source:
        return entity.source[:max_length]
    parts = [f"{entity.entity_type} {entity.name}"]
    if entity.file_path:
        parts.append(f"in {entity.file_path}")
    return " ".join(parts)


def extract_identifiers_from_code(code: str) -> set[str]:
    """从 Markdown 中的 Python 代码块提取标识符。

    Args:
        code: Markdown 文本。

    Returns:
        标识符集合（长度 >= 3）。
    """
    identifiers: set[str] = set()
    for match in CODE_BLOCK_RE.finditer(code):
        for id_match in IDENTIFIER_RE.finditer(match.group(1)):
            identifiers.add(id_match.group(0))
    return identifiers


def normalize_path(file_path: str | None, repo_root: Path) -> str:
    """规范化文件路径为相对于仓库根目录的路径。

    Args:
        file_path: 原始文件路径（可能是绝对或相对路径）。
        repo_root: 仓库根目录路径。

    Returns:
        规范化后的相对路径；空字符串表示无路径；无法相对化时原样返回。
    """
    if not file_path:
        return ""
    try:
        return str(Path(file_path).relative_to(repo_root))
    except ValueError:
        return file_path


def scan_files(
    repo_path: Path,
    skip_dirs: set[str],
    include_docs: bool,
    doc_extensions: list[str],
) -> tuple[list[Path], list[Path]]:
    """扫描代码文件（.py, .java）和文档文件，跳过隐藏/排除目录。

    Args:
        repo_path: 仓库根目录路径。
        skip_dirs: 需要跳过的目录名集合（匹配路径任一段或文件名）。
        include_docs: 是否扫描文档文件。
        doc_extensions: 文档文件扩展名列表（如 ``[".md", ".rst"]``）。

    Returns:
        ``(code_files, doc_files)`` 元组，均为已排序的路径列表。
    """
    code_files: list[Path] = []
    doc_files: list[Path] = []

    for suffix in (".py", ".java"):
        for p in repo_path.rglob(f"*{suffix}"):
            if any(skip in p.parts or skip in p.name for skip in skip_dirs):
                continue
            code_files.append(p)

    if include_docs:
        for ext in doc_extensions:
            for p in repo_path.rglob(f"*{ext}"):
                if any(skip in p.parts or skip in p.name for skip in skip_dirs):
                    continue
                doc_files.append(p)

    return sorted(code_files), sorted(doc_files)
