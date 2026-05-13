from __future__ import annotations

import dataclasses
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from layerkg.aligner import ConceptAligner
from layerkg.chroma_store import ChromaStore
from layerkg.config import LayerKGConfig
from layerkg.extractor.relation import RelationExtractor
from layerkg.extractor.semantic import SemanticExtractor, SemanticRelation
from layerkg.module_clustering import ModuleCluster, ModuleClustering
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.parser.python_parser import PythonParser
from layerkg.schema import CodeEntity, ConceptEntity, DocEntity, Relation

if TYPE_CHECKING:
    from layerkg.parser.base import ExtractedRelation
    from layerkg.parser.doc_parser import DocParser

# 路径边界字符（用于防止子串误匹配）
_BOUNDARY_CHARS = " ./\\-_"

# entity_type 到 Neo4j 标签的映射（用于 merge_relation label 优化）
ENTITY_TYPE_TO_LABEL: dict[str, str] = {
    "function": "CodeEntity",
    "class": "CodeEntity",
    "interface": "CodeEntity",
    "module": "CodeEntity",
    "file": "CodeEntity",
    "enum": "CodeEntity",
    "record": "CodeEntity",
    "field": "CodeEntity",
    "readme": "DocEntity",
    "module_doc": "DocEntity",
    "api_doc": "DocEntity",
    "comment": "DocEntity",
    "wiki": "DocEntity",
    "architecture_doc": "DocEntity",
    "business_concept": "ConceptEntity",
    "design_pattern": "ConceptEntity",
    "api_contract": "ConceptEntity",
    "data_model": "ConceptEntity",
    "process": "ConceptEntity",
}

# 概念类型的 entity_type 集合
_CONCEPT_ENTITY_TYPES = frozenset(
    {
        "business_concept",
        "design_pattern",
        "api_contract",
        "data_model",
        "process",
    }
)

# 代码类型的 entity_type 集合（路径 B 可处理的）
_CODE_ENTITY_TYPES = frozenset(
    {
        "function",
        "class",
        "interface",
        "module",
        "file",
        "enum",
        "record",
        "field",
    }
)


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
        aborted: 是否在关键阶段失败中止（Phase 2）。
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
    aborted: bool = False
    elapsed_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

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
        self._semantic_extractor: SemanticExtractor | None = None
        self._aligner: ConceptAligner | None = None
        self._clustering: ModuleClustering | None = None
        self._doc_parser: DocParser | None = None
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

    def _get_doc_parser(self) -> DocParser:
        """Lazy init DocParser（缓存实例）。

        Returns:
            DocParser 实例。
        """
        from layerkg.parser.doc_parser import DocParser

        if self._doc_parser is None:
            self._doc_parser = DocParser()
        return self._doc_parser

    def _stage_parse(
        self, repo_path: Path
    ) -> tuple[list[CodeEntity], list[DocEntity], list[Relation], int, list[ExtractedRelation]]:
        """Stage 1: 扫描 + 解析文件 + 提取结构关系。

        Args:
            repo_path: 仓库根目录路径。

        Returns:
            (all_entities, doc_entities, relations, files_scanned, unresolved_imports) 五元组。
        """
        self._repo_root = repo_path
        py_files, doc_files = self._scan_files(repo_path)
        self._logger.info("Scanned %d Python files, %d doc files", len(py_files), len(doc_files))

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

        # 解析文档文件
        doc_parser = self._get_doc_parser()
        doc_entities: list[DocEntity] = []
        for file_path in doc_files:
            result = doc_parser.parse_file(file_path)
            if result.error:
                self._logger.warning("Skip doc %s: %s", file_path, result.error)
                skipped_files += 1
                continue
            doc_entities.extend(result.entities)

        self._logger.info("Parsed %d doc entities", len(doc_entities))

        relations, unresolved_imports = self._extractor.resolve_with_unresolved(all_entities)
        self._logger.info("Resolved %d relations, %d unresolved imports", len(relations), len(unresolved_imports))

        return all_entities, doc_entities, relations, len(py_files) + len(doc_files), unresolved_imports

    def _stage_write_structural(
        self,
        all_entities: list[CodeEntity],
        doc_entities: list[DocEntity],
        relations: list[Relation],
        unresolved_imports: list[ExtractedRelation],
    ) -> tuple[Neo4jGraphStore, int, int]:
        """Stage 2: 写入结构实体和关系到 Neo4j。

        关键路径：失败则抛出 RuntimeError 中止构建。

        Args:
            all_entities: 代码实体列表。
            doc_entities: 文档实体列表。
            relations: 关系列表。
            unresolved_imports: 未解析的外部导入列表。

        Returns:
            (Neo4jGraphStore 实例, 外部实体数量, 外部关系数量) 三元组。

        Raises:
            RuntimeError: 写入失败时。
        """
        graph_store = self._get_graph_store()

        try:
            graph_store.ensure_constraints()
            for i, entity in enumerate(all_entities, 1):
                graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))
                if i % 200 == 0:
                    self._logger.info("[Neo4j] Merged %d/%d CodeEntities", i, len(all_entities))
            self._logger.info("[Neo4j] Merged %d CodeEntities complete", len(all_entities))
            for i, doc in enumerate(doc_entities, 1):
                graph_store.merge_node("DocEntity", self._doc_entity_to_dict(doc))
                if i % 200 == 0:
                    self._logger.info("[Neo4j] Merged %d/%d DocEntities", i, len(doc_entities))
            self._logger.info("[Neo4j] Merged %d DocEntities complete", len(doc_entities))
            for rel in relations:
                graph_store.merge_relation(
                    rel.source_id,
                    rel.target_id,
                    rel.relation_type,
                    source_label="CodeEntity",
                    target_label="CodeEntity",
                )
            self._logger.info("[Neo4j] Wrote %d structural relations", len(relations))

            # 处理外部 import：创建虚拟节点和关系
            name_to_id = {e.name: e.id for e in all_entities}
            external_modules: dict[str, str] = {}  # name → id

            for ext_name in sorted({rel.target_name for rel in unresolved_imports}):
                ext_entity = CodeEntity(
                    name=ext_name,
                    entity_type="module",
                    file_path="__external__",
                    language="python",
                )
                graph_store.merge_node("CodeEntity", self._entity_to_dict(ext_entity))
                external_modules[ext_name] = ext_entity.id

            ext_rel_count = 0
            for rel in unresolved_imports:
                source_id = name_to_id.get(rel.source_name)
                target_id = external_modules.get(rel.target_name)
                if source_id and target_id:
                    graph_store.merge_relation(
                        source_id,
                        target_id,
                        "imports",
                        source_label="CodeEntity",
                        target_label="CodeEntity",
                    )
                    ext_rel_count += 1

            self._logger.info(
                "[Neo4j] Created %d external module nodes, %d external import relations",
                len(external_modules),
                ext_rel_count,
            )
        except Exception as e:
            raise RuntimeError(f"Stage 2 structural write failed: {e}") from e

        return graph_store, len(external_modules), ext_rel_count

    def _stage_semantic(
        self,
        all_entities: list[CodeEntity],
        graph_store: Neo4jGraphStore,
        repo_path: Path,
        *,
        doc_entities: list[DocEntity] | None = None,
    ) -> tuple[int, int, bool, list[str], list[ConceptEntity]]:
        """Stage 3: 语义提取 + 概念对齐 + 写入 Neo4j。

        可降级：失败不中止构建，跳过并记录错误。

        Args:
            all_entities: 代码实体列表。
            graph_store: Neo4j 存储实例。
            repo_path: 仓库根目录。

        Returns:
            (concepts_created, semantic_relations_created, skipped_semantic, errors, new_concepts) 元组。
        """
        concepts_created = 0
        semantic_relations_created = 0
        skipped_semantic = False
        errors: list[str] = []
        new_concepts: list[ConceptEntity] = []

        if self._check_ollama():
            try:
                extractor = self._init_semantic_extractor()
                extraction = extractor.extract(all_entities, doc_entities=doc_entities)
                if extraction.relations:
                    entity_index = self._build_entity_index(all_entities, repo_path)
                    new_concepts, semantic_rels, _sem_skipped, type_mapping = self._process_semantic_relations(
                        extraction.relations, entity_index, repo_root=repo_path
                    )
                    # 写入新概念到 Neo4j
                    if new_concepts:
                        for concept in new_concepts:
                            try:
                                graph_store.merge_node(
                                    "ConceptEntity",
                                    {
                                        "id": concept.id,
                                        "name": concept.name,
                                        "entity_type": concept.entity_type,
                                        "description": concept.description or "",
                                    },
                                )
                            except Exception as e:
                                self._logger.warning("Failed to write concept %s to Neo4j: %s", concept.name, e)
                                errors.append(f"Neo4j concept write error: {e}")
                    # 写入语义关系到 Neo4j
                    for rel in semantic_rels:
                        try:
                            source_type, target_type = type_mapping.get((rel.source_id, rel.target_id), ("", ""))
                            source_label = ENTITY_TYPE_TO_LABEL.get(source_type, "")
                            target_label = ENTITY_TYPE_TO_LABEL.get(target_type, "")
                            graph_store.merge_relation(
                                rel.source_id,
                                rel.target_id,
                                rel.relation_type,
                                source_label=source_label,
                                target_label=target_label,
                            )
                        except Exception as e:
                            self._logger.warning("Failed to write semantic relation: %s", e)
                            errors.append(f"Neo4j relation write error: {e}")
                    concepts_created = len(new_concepts)
                    semantic_relations_created = len(semantic_rels)
            except Exception as e:
                self._logger.error("Semantic extraction failed: %s", e)
                errors.append(f"Semantic extraction error: {e}")
        else:
            skipped_semantic = True
            self._logger.info("Ollama unavailable, skipping semantic extraction")

        self._logger.info(
            "[Semantic] Stage complete: %d concepts, %d relations, %d errors",
            concepts_created,
            semantic_relations_created,
            len(errors),
        )
        return concepts_created, semantic_relations_created, skipped_semantic, errors, new_concepts

    def build(
        self,
        repo_path: Path,
        *,
        skip_semantic: bool = False,
        skip_clustering: bool = False,
        clear: bool = False,
    ) -> BuildResult:
        """全量构建：5阶段流水线编排。

        阶段:
            1. 解析: 扫描 + AST 解析 + 结构关系提取
            2. 结构写入: 实体 + 关系 → Neo4j (关键路径)
            3. 语义提取: LLM 概念 + 语义关系 (可降级)
            4. 模块聚类: 社区检测 → ModuleEntity (可降级)
            5. 向量写入: 统一 ChromaDB (可降级)

        Args:
            repo_path: 仓库根目录路径。
            skip_semantic: 跳过语义提取（Stage 3）。
            skip_clustering: 跳过模块聚类（Stage 4）。

        Returns:
            构建结果统计。
        """
        import time

        t0 = time.monotonic()
        all_errors: list[str] = []
        aborted = False

        # Pre-build: 清库（如果指定）
        if clear:
            graph_store = self._get_graph_store()
            cleared = graph_store.clear_all()
            self._logger.info("═══ Pre-build: Cleared %d existing nodes ═══", cleared)

        # Stage 1: 解析
        self._logger.info("═══ Stage 1/5: Parse ═══")
        all_entities, doc_entities, relations, files_scanned, unresolved_imports = self._stage_parse(repo_path)

        # Stage 2: 结构写入（关键路径）
        self._logger.info("═══ Stage 2/5: Structural Write ═══")
        try:
            graph_store, ext_entity_count, ext_rel_count = self._stage_write_structural(
                all_entities, doc_entities, relations, unresolved_imports
            )
        except RuntimeError as e:
            all_errors.append(str(e))
            aborted = True
            elapsed = (time.monotonic() - t0) * 1000
            return BuildResult(
                files_scanned=files_scanned,
                entities_created=0,
                relations_created=0,
                doc_entities_created=0,
                aborted=aborted,
                elapsed_ms=elapsed,
                errors=all_errors,
            )

        # Stage 2.5: 文档→代码关联
        self._logger.info("═══ Stage 2.5/5: Doc-Code Link ═══")
        entity_index = self._build_entity_index(all_entities, repo_path)
        describes_rels = self._link_docs_to_code(doc_entities, entity_index)
        for rel in describes_rels:
            graph_store.merge_relation(
                rel.source_id, rel.target_id, rel.relation_type, source_label="DocEntity", target_label="CodeEntity"
            )
        self._logger.info("═══ Stage 2.5/5 complete: %d DESCRIBES relations ═══", len(describes_rels))

        # Stage 3: 语义提取（可降级）
        self._logger.info("═══ Stage 3/5: Semantic Extraction (may take a while...) ═══")
        if skip_semantic:
            concepts_created = 0
            semantic_rels_created = 0
            skipped_semantic = True
            sem_errors = []
            new_concepts = []
        else:
            concepts_created, semantic_rels_created, skipped_semantic, sem_errors, new_concepts = self._stage_semantic(
                all_entities, graph_store, repo_path, doc_entities=doc_entities
            )
        all_errors.extend(sem_errors)
        self._logger.info(
            "═══ Stage 3/5 complete: %d concepts, %d semantic relations ═══", concepts_created, semantic_rels_created
        )

        # Stage 4: 模块聚类（可降级）
        self._logger.info("═══ Stage 4/5: Module Clustering ═══")
        if skip_clustering:
            clusters_count = 0
            clusters = []
        else:
            try:
                clusters_count, clusters = self._detect_and_write_modules(graph_store, all_entities)
            except Exception as e:
                self._logger.warning("Module clustering failed: %s", e)
                all_errors.append(f"Module clustering error: {e}")
                clusters_count = 0
                clusters = []
        self._logger.info("═══ Stage 4/5 complete: %d modules ═══", clusters_count)

        # Stage 5: 向量写入（可降级）
        self._logger.info("═══ Stage 5/5: Vector Index ═══")
        try:
            self._write_all_vectors(all_entities, doc_entities, new_concepts, clusters)
        except Exception as e:
            self._logger.warning("Vector write failed: %s", e)
            all_errors.append(f"Vector write error: {e}")

        elapsed = (time.monotonic() - t0) * 1000
        return BuildResult(
            files_scanned=files_scanned,
            entities_created=len(all_entities) + len(doc_entities) + ext_entity_count,
            relations_created=len(relations) + len(describes_rels) + ext_rel_count,
            concepts_created=concepts_created,
            semantic_relations_created=semantic_rels_created,
            modules_created=clusters_count,
            doc_entities_created=len(doc_entities),
            skipped_semantic=skipped_semantic,
            aborted=aborted,
            elapsed_ms=elapsed,
            errors=all_errors,
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

    def _scan_files(self, repo_path: Path) -> tuple[list[Path], list[Path]]:
        """扫描 Python 和文档文件，跳过隐藏目录。

        Args:
            repo_path: 仓库根目录路径。

        Returns:
            (py_files, doc_files) 元组，均为已排序的路径列表。
        """
        skip_dirs = self._config.build_skip_dirs
        py_files: list[Path] = []
        doc_files: list[Path] = []

        # 扫描 Python 文件
        for p in repo_path.rglob("*.py"):
            if any(skip in p.parts or skip in p.name for skip in skip_dirs):
                continue
            py_files.append(p)

        # 扫描文档文件（仅在 build_include_docs 为 True 时）
        if self._config.build_include_docs:
            for ext in self._config.build_doc_extensions:
                for p in repo_path.rglob(f"*{ext}"):
                    if any(skip in p.parts or skip in p.name for skip in skip_dirs):
                        continue
                    doc_files.append(p)

        return sorted(py_files), sorted(doc_files)

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
        if entity.docstring:
            d["docstring"] = entity.docstring
        if entity.parameters:
            d["code_parameters"] = entity.parameters
        return d

    @staticmethod
    def _doc_entity_to_dict(entity: DocEntity) -> dict:
        """将 DocEntity 转为 Neo4j 属性字典。

        Args:
            entity: 文档实体。

        Returns:
            属性字典。
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

    # 代码块和标识符正则
    _CODE_BLOCK_RE = re.compile(r"```python\n(.*?)\n```", re.DOTALL)
    _IDENTIFIER_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b")

    @staticmethod
    def _extract_identifiers_from_code(code: str) -> set[str]:
        """从 Markdown 代码块中提取 Python 标识符。

        Args:
            code: Markdown 文本内容。

        Returns:
            标识符集合。
        """
        identifiers: set[str] = set()
        for match in LayerKGBuilder._CODE_BLOCK_RE.finditer(code):
            for id_match in LayerKGBuilder._IDENTIFIER_RE.finditer(match.group(1)):
                identifiers.add(id_match.group(0))
        return identifiers

    def _link_docs_to_code(
        self,
        doc_entities: list[DocEntity],
        entity_index: dict[tuple[str, str, str], list[str]],
    ) -> list[Relation]:
        """链接文档到代码实体，生成 describes 关系。

        Args:
            doc_entities: 文档实体列表。
            entity_index: 实体索引，键为 (entity_type, file_path, name)。

        Returns:
            describes 关系列表。
        """
        describes_rels: list[Relation] = []
        max_rels_per_doc = 50

        for doc in doc_entities:
            count = 0
            content = doc.content or ""

            # 路径匹配：检查 doc.content 是否包含代码文件的 file_path
            for (_entity_type, file_path, name), entity_ids in entity_index.items():
                if count >= max_rels_per_doc:
                    break

                # 路径匹配（需要 file_path）
                if file_path:
                    # 边界检查：防止子串误匹配
                    doc_content = doc.content or ""
                    if file_path in doc_content:
                        # 检查边界
                        doc_idx = doc_content.find(file_path)
                        left_ok = doc_idx == 0 or doc_content[doc_idx - 1] in _BOUNDARY_CHARS
                        right_ok = (
                            doc_idx + len(file_path) >= len(doc_content)
                            or doc_content[doc_idx + len(file_path)] in _BOUNDARY_CHARS
                        )
                        if left_ok and right_ok:
                            describes_rels.append(
                                Relation(
                                    source_id=doc.id,
                                    target_id=entity_ids[0],
                                    relation_type="describes",
                                )
                            )
                            count += 1
                            continue

                    # 文件名匹配：检查 doc.content 是否包含文件 basename
                    # 用于处理只提到文件名而非完整路径的情况
                    filename = os.path.basename(file_path)
                    if filename and filename != file_path and filename in doc_content:  # 避免重复匹配根目录文件
                        # 检查边界
                        doc_idx = doc_content.find(filename)
                        left_ok = doc_idx == 0 or doc_content[doc_idx - 1] in _BOUNDARY_CHARS
                        right_ok = (
                            doc_idx + len(filename) >= len(doc_content)
                            or doc_content[doc_idx + len(filename)] in _BOUNDARY_CHARS
                        )
                        if left_ok and right_ok:
                            describes_rels.append(
                                Relation(
                                    source_id=doc.id,
                                    target_id=entity_ids[0],
                                    relation_type="describes",
                                )
                            )
                            count += 1
                            continue

                # 函数名匹配：从代码块提取标识符（不依赖 file_path）
                if name in self._extract_identifiers_from_code(content) and len(name) > 3:
                    describes_rels.append(
                        Relation(
                            source_id=doc.id,
                            target_id=entity_ids[0],
                            relation_type="describes",
                        )
                    )
                    count += 1

        return describes_rels

    def _check_ollama(self) -> bool:
        """检查 Ollama 服务是否可用。

        Returns:
            True 表示服务可用，False 表示不可用。
        """
        try:
            resp = httpx.get(f"{self._config.ollama_base_url}/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    def _init_semantic_extractor(self) -> SemanticExtractor:
        """Lazy init SemanticExtractor。

        Returns:
            SemanticExtractor 实例。
        """
        if self._semantic_extractor is None:
            self._semantic_extractor = SemanticExtractor(
                ollama_url=self._config.ollama_base_url,
                model=self._config.llm_model,
            )
        return self._semantic_extractor

    def _init_concept_aligner(self) -> ConceptAligner:
        """Lazy init ConceptAligner（从空概念列表开始）。

        Returns:
            ConceptAligner 实例。
        """
        if self._aligner is None:
            self._aligner = ConceptAligner(
                chroma_store=self._get_chroma_store(),
                concepts=[],
            )
        return self._aligner

    def _init_clustering(self) -> ModuleClustering:
        """Lazy init ModuleClustering。

        Returns:
            ModuleClustering 实例。
        """
        if self._clustering is None:
            self._clustering = ModuleClustering(
                neo4j_store=self._get_graph_store(),
            )
        return self._clustering

    def _detect_and_write_modules(
        self,
        graph_store: Neo4jGraphStore,
        all_entities: list[CodeEntity],
    ) -> tuple[int, list[ModuleCluster]]:
        """Stage 4: 检测模块聚类并写入 Neo4j.

        Args:
            graph_store: Neo4j 存储实例。
            all_entities: 所有代码实体（用于补全 ModuleEntity 属性）。

        Returns:
            (clusters_count, clusters) 元组。

        Raises:
            RuntimeError: 聚类失败时。
        """
        clustering = self._init_clustering()
        clusters = clustering.detect_modules()
        if clusters:
            clustering.save_modules(clusters, all_entities)
        return len(clusters), clusters

    def _write_all_vectors(
        self,
        all_entities: list[CodeEntity],
        doc_entities: list[DocEntity],
        new_concepts: list[ConceptEntity],
        clusters: list[ModuleCluster],
    ) -> None:
        """Stage 5: 统一向量写入 ChromaDB.

        Args:
            all_entities: 所有代码实体。
            doc_entities: 所有文档实体。
            new_concepts: 新创建的概念实体。
            clusters: 模块聚类列表。
        """
        chroma_store = self._get_chroma_store()
        items: list[tuple[str, str, dict[str, str]]] = []

        # CodeEntity
        for entity in all_entities:
            text = self._entity_to_text(entity)
            if text:
                items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))

        # DocEntity
        for doc in doc_entities:
            text = (doc.content or "")[: self._config.build_doc_max_length]
            if text.strip():
                items.append((doc.id, text, {"entity_type": doc.entity_type, "name": doc.name}))

        # ConceptEntity
        for concept in new_concepts:
            text = concept.description or concept.name
            items.append((concept.id, text, {"entity_type": concept.entity_type, "name": concept.name}))

        # ModuleCluster → cluster.module
        for cluster in clusters:
            module = cluster.module
            text = module.description or module.name
            # ModuleEntity 无 entity_type 字段，硬编码
            items.append((module.id, text, {"entity_type": "module", "name": module.name}))

        if items:
            self._logger.info("[Vector] Writing %d items to ChromaDB...", len(items))
            chroma_store.put_entities_batch(items)
            self._logger.info("[Vector] Wrote %d vectors complete", len(items))

    def _fuzzy_lookup_entity(
        self,
        entity_index: dict[tuple[str, str, str], list[str]],
        entity_type: str,
        name: str,
    ) -> list[str]:
        """模糊查找实体 ID：按 (type, name) 匹配，忽略 file_path。

        Args:
            entity_index: 实体索引。
            entity_type: 实体类型。
            name: 实体名称。

        Returns:
            匹配到的 ID 列表。
        """
        candidates: list[str] = []
        for key, ids in entity_index.items():
            if key[0] == entity_type and key[2] == name:
                candidates.extend(ids)
        return candidates

    def _process_semantic_relations(
        self,
        relations: list[SemanticRelation],
        entity_index: dict[tuple[str, str, str], list[str]],
        repo_root: Path,
    ) -> tuple[list[ConceptEntity], list[Relation], int, dict[tuple[str, str], tuple[str, str]]]:
        """处理语义关系：概念对齐 + ID 解析。

        Args:
            relations: SemanticExtractor 提取的语义关系。
            entity_index: Stage 1-2 构建的实体索引。
            repo_root: 仓库根目录。

        Returns:
            (新概念列表, Relation列表, 跳过数量, 类型映射) 元组。
            类型映射: {(source_id, target_id): (source_type, target_type)}
        """
        aligner = self._init_concept_aligner()
        new_concepts: list[ConceptEntity] = []
        resolved: list[Relation] = []
        skipped = 0
        type_mapping: dict[
            tuple[str, str], tuple[str, str]
        ] = {}  # (source_id, target_id) -> (source_type, target_type)

        # 路径 A：收集所有概念目标的 unique target_name
        concept_targets: dict[str, tuple[str, str]] = {}  # target_name → (target_type, reasoning)
        concept_relations: list[SemanticRelation] = []
        code_relations: list[SemanticRelation] = []

        for rel in relations:
            if rel.target_type in _CONCEPT_ENTITY_TYPES:
                concept_targets.setdefault(rel.target_name, (rel.target_type, rel.reasoning or ""))
                concept_relations.append(rel)
            elif rel.target_type in _CODE_ENTITY_TYPES:
                code_relations.append(rel)
            else:
                # ResourceEntity, DocEntity 等 → 跳过
                skipped += 1

        # 路径 A：批量对齐概念
        if concept_targets:
            align_results = aligner.align_batch(list(concept_targets.keys()))
            concept_id_map: dict[str, str] = {}  # target_name → concept_id

            for target_name, align_result in zip(concept_targets.keys(), align_results, strict=True):
                if align_result.match_type == "none":
                    # 创建新 ConceptEntity
                    _target_type, _description = concept_targets[target_name]
                    concept = ConceptEntity(
                        name=target_name,
                        entity_type=_target_type,
                        description=_description,
                    )
                    new_concepts.append(concept)
                    concept_id_map[target_name] = concept.id
                    aligner.add_concept(concept)
                else:
                    concept_id_map[target_name] = align_result.concept_id

            # 构建路径 A 的 Relation
            for rel in concept_relations:
                source_key = (
                    rel.source_type,
                    self._normalize_path(rel.source_file_path, repo_root),
                    rel.source_name,
                )
                source_ids = entity_index.get(source_key, [])
                if not source_ids:
                    skipped += 1
                    continue
                target_id = concept_id_map.get(rel.target_name)
                if not target_id:
                    skipped += 1
                    continue
                source_id = source_ids[0]
                resolved.append(
                    Relation(
                        source_id=source_id,
                        target_id=target_id,
                        relation_type=rel.relation_type,
                    )
                )
                type_mapping[(source_id, target_id)] = (rel.source_type, concept_targets[rel.target_name][0])

        # 路径 B：代码目标，用 entity_index 解析
        for rel in code_relations:
            source_key = (
                rel.source_type,
                self._normalize_path(rel.source_file_path, repo_root),
                rel.source_name,
            )
            source_ids = entity_index.get(source_key, [])
            if not source_ids:
                source_ids = self._fuzzy_lookup_entity(entity_index, rel.source_type, rel.source_name)
            target_ids = self._fuzzy_lookup_entity(entity_index, rel.target_type, rel.target_name)
            if not source_ids or not target_ids:
                skipped += 1
                continue
            source_id = source_ids[0]
            target_id = target_ids[0]
            resolved.append(
                Relation(
                    source_id=source_id,
                    target_id=target_id,
                    relation_type=rel.relation_type,
                )
            )
            type_mapping[(source_id, target_id)] = (rel.source_type, rel.target_type)

        return new_concepts, resolved, skipped, type_mapping

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
            file_path = getattr(e, "file_path", None)  # ConceptEntity 没有 file_path
            entity_type = e.entity_type
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

        Note:
            如果 source_file_path 为空或多个同名实体存在于不同文件中，
            source 可能匹配到错误的实体。这是已知的近似匹配限制，
            后续可用 source_context 或 qualified_name 增强。
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
        if self._semantic_extractor:
            self._semantic_extractor.close()

    def __enter__(self) -> LayerKGBuilder:
        """进入 context manager。

        Returns:
            self。
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """退出 context manager，关闭资源。"""
        self.close()
