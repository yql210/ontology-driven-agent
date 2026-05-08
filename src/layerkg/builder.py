from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from layerkg.chroma_store import ChromaStore
from layerkg.config import LayerKGConfig
from layerkg.extractor.relation import RelationExtractor
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.parser.python_parser import PythonParser
from layerkg.schema import CodeEntity, ConceptEntity, DocEntity, Relation

if TYPE_CHECKING:
    from layerkg.extractor.semantic import SemanticRelation


@dataclass
class BuildResult:
    """构建结果。

    Attributes:
        files_scanned: 扫描的文件数量。
        entities_created: 创建的实体数量。
        relations_created: 创建的关系数量。
        concepts_created: 概念实体数量（Phase 2）。
        semantic_relations_created: 语义关系数量（Phase 2）。
        modules_created: 模块数量（Phase 2）。
        doc_entities_created: 文档实体数量（Phase 2）。
        skipped_semantic: 是否跳过语义处理（Phase 2）。
        elapsed_ms: 耗时毫秒数（Phase 2）。
        errors: 错误列表（Phase 2）。
    """

    files_scanned: int
    entities_created: int
    relations_created: int
    concepts_created: int = 0
    semantic_relations_created: int = 0
    modules_created: int = 0
    doc_entities_created: int = 0
    skipped_semantic: bool = False
    elapsed_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class LayerKGBuilder:
    """LayerKG 构建器，组装解析 → 提取 → 存储流水线。"""

    def __init__(self, config: LayerKGConfig) -> None:
        """初始化构建器。

        Args:
            config: LayerKG 配置。
        """
        self._config = config
        self._parser = PythonParser()
        self._extractor = RelationExtractor()
        self._graph_store: Neo4jGraphStore | None = None
        self._chroma_store: ChromaStore | None = None
        self._logger = logging.getLogger(__name__)
        self._repo_root: Path | None = None

    def _get_graph_store(self) -> Neo4jGraphStore:
        """获取或创建 Neo4j 存储实例。

        Returns:
            Neo4jGraphStore 实例。
        """
        if self._graph_store is None:
            self._graph_store = Neo4jGraphStore(
                uri=self._config.neo4j_uri,
                user=self._config.neo4j_user,
                password=self._config.neo4j_password,
            )
        return self._graph_store

    def _get_chroma_store(self) -> ChromaStore:
        """获取或创建 ChromaDB 存储实例。

        Returns:
            ChromaStore 实例。
        """
        if self._chroma_store is None:
            self._chroma_store = ChromaStore(
                persist_dir=self._config.chroma_persist_dir,
                ollama_url=self._config.ollama_base_url,
                embedding_model=self._config.embedding_model,
            )
        return self._chroma_store

    def build(self, repo_path: Path) -> BuildResult:
        """全量构建：扫描 → 解析 → 写图 + 向量。

        Args:
            repo_path: 仓库根目录路径。

        Returns:
            构建结果统计。
        """
        self._repo_root = repo_path  # 供 _resolve_semantic_names 使用

        # 1. 扫描 .py 文件
        py_files = self._scan_python_files(repo_path)
        self._logger.info("Scanned %d Python files", len(py_files))

        # 2. 逐文件解析
        all_entities: list[CodeEntity] = []
        skipped_files = 0

        for file_path in py_files:
            result = self._parser.parse_file(file_path)
            if result.error:
                self._logger.warning("Skip %s: %s", file_path, result.error)
                skipped_files += 1
                continue
            all_entities.extend(result.entities)
            self._extractor.add_parse_result(result.entities, result.relations)

        self._logger.info("Parsed %d entities, skipped %d files", len(all_entities), skipped_files)

        # 3. 解析关系
        relations = self._extractor.resolve(all_entities)
        self._logger.info("Resolved %d relations", len(relations))

        # 4. 写 Neo4j
        graph_store = self._get_graph_store()
        graph_store.ensure_constraints()

        for entity in all_entities:
            graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))

        for rel in relations:
            graph_store.merge_relation(rel.source_id, rel.target_id, rel.relation_type)

        # 5. 写 ChromaDB（有文本内容的实体）
        chroma_store = self._get_chroma_store()
        items = []
        for entity in all_entities:
            text = self._entity_to_text(entity)
            if text:
                items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))

        if items:
            chroma_store.put_entities_batch(items)

        # 6. 返回结果
        return BuildResult(
            files_scanned=len(py_files),
            entities_created=len(all_entities),
            relations_created=len(relations),
        )

    def query(
        self,
        text: str,
        n_results: int = 10,
        entity_type: str | None = None,
    ) -> list[dict]:
        """语义搜索。

        Args:
            text: 查询文本。
            n_results: 返回结果数量。
            entity_type: 实体类型过滤（可选）。

        Returns:
            搜索结果列表，每项包含 id, text, metadata, distance。
        """
        chroma_store = self._get_chroma_store()
        where = {"entity_type": entity_type} if entity_type else None
        return chroma_store.search(text, n_results=n_results, where=where)

    def info(self) -> dict:
        """获取存储统计信息。

        Returns:
            包含配置和统计信息的字典。
        """
        result = {
            "config": {
                "neo4j_uri": self._config.neo4j_uri,
                "ollama_url": self._config.ollama_base_url,
                "model": self._config.embedding_model,
                "chroma_dir": self._config.chroma_persist_dir,
            }
        }
        chroma_store = self._get_chroma_store()
        result["chroma_count"] = chroma_store.count()
        return result

    @staticmethod
    def _scan_python_files(repo_path: Path) -> list[Path]:
        """扫描 Python 文件，跳过隐藏目录。

        Args:
            repo_path: 仓库根目录路径。

        Returns:
            Python 文件路径列表（已排序）。
        """
        skip_dirs = {
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            ".mypy_cache",
            ".pytest_cache",
            "node_modules",
            ".tox",
            "dist",
            "build",
            "*.egg-info",
        }
        files = []
        for p in repo_path.rglob("*.py"):
            # 检查路径中是否包含跳过的目录
            if any(skip in p.parts or skip in p.name for skip in skip_dirs):
                continue
            files.append(p)
        return sorted(files)

    @staticmethod
    def _entity_to_dict(entity: CodeEntity) -> dict:
        """将 CodeEntity 转为 Neo4j 属性字典。

        Args:
            entity: 代码实体。

        Returns:
            属性字典。
        """
        d = {
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
        return d

    @staticmethod
    def _entity_to_text(entity: CodeEntity) -> str | None:
        """提取实体的可嵌入文本。

        Args:
            entity: 代码实体。

        Returns:
            可嵌入的文本，无内容时返回 None。
        """
        if entity.source:
            return entity.source
        # 对于没有 source 的实体，构造描述文本
        parts = [f"{entity.entity_type} {entity.name}"]
        if entity.file_path:
            parts.append(f"in {entity.file_path}")
        return " ".join(parts)

    def _normalize_path(self, file_path: str | None, repo_root: Path) -> str:
        """规范化文件路径为相对于仓库根目录的路径。

        Args:
            file_path: 原始文件路径（可能是绝对或相对路径）。
            repo_root: 仓库根目录路径。

        Returns:
            规范化后的相对路径，空字符串表示无路径。
        """
        if not file_path:
            return ""
        try:
            return str(Path(file_path).relative_to(repo_root))
        except ValueError:
            return file_path

    def _build_entity_index(
        self,
        entities: list[CodeEntity | ConceptEntity | DocEntity],
        repo_root: Path,
    ) -> dict[tuple[str, str, str], list[str]]:
        """构建实体索引，用于名称解析。

        Args:
            entities: 实体列表。
            repo_root: 仓库根目录路径。

        Returns:
            索引字典，键为 (entity_type, file_path, name) 三元组，值为 ID 列表。
        """
        index: dict[tuple[str, str, str], list[str]] = {}
        for e in entities:
            file_path = getattr(e, "file_path", None)
            entity_type = getattr(e, "entity_type", "")
            key = (entity_type, self._normalize_path(file_path, repo_root), e.name)
            if key not in index:
                index[key] = []
            index[key].append(e.id)
        return index

    def _resolve_semantic_names(
        self,
        relations: list[SemanticRelation],
        index: dict[tuple[str, str, str], list[str]],
    ) -> tuple[list[Relation], int]:
        """将语义关系中的名称解析为实体 ID。

        Args:
            relations: 语义关系列表（使用名称）。
            index: 实体索引。

        Returns:
            (解析后的 Relation 列表, 跳过数量) 元组。
        """
        resolved: list[Relation] = []
        skipped = 0
        for rel in relations:
            source_key = (
                rel.source_type,
                self._normalize_path(rel.source_file_path, self._repo_root or Path(".")),
                rel.source_name,
            )
            target_key = (rel.target_type, "", rel.target_name)
            source_ids = index.get(source_key, [])
            target_ids = index.get(target_key, [])
            if not source_ids or not target_ids:
                self._logger.warning(
                    "Cannot resolve semantic relation: %s -> %s",
                    rel.source_name,
                    rel.target_name,
                )
                skipped += 1
                continue
            resolved.append(
                Relation(
                    source_id=source_ids[0],
                    target_id=target_ids[0],
                    relation_type=rel.relation_type,
                )
            )
        return resolved, skipped

    def close(self) -> None:
        """关闭所有存储连接。"""
        if self._graph_store:
            self._graph_store.close()
        if self._chroma_store:
            self._chroma_store.close()

    def __enter__(self) -> LayerKGBuilder:
        """进入 context manager。

        Returns:
            self。
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """退出 context manager，关闭资源。"""
        self.close()
