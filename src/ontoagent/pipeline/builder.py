from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ontoagent.config import OntoAgentConfig
from ontoagent.domain.provenance import add_provenance, clamp_confidence
from ontoagent.domain.schema import CodeEntity, ConceptEntity, DocEntity, Relation
from ontoagent.parsing.extractor.relation import RelationExtractor
from ontoagent.parsing.extractor.semantic import SemanticExtractor
from ontoagent.parsing.parser.base import BaseParser
from ontoagent.parsing.parser.java_parser import JavaParser
from ontoagent.parsing.parser.python_parser import PythonParser
from ontoagent.pipeline.builder_utils import (
    doc_entity_to_dict,
    entity_to_dict,
    entity_to_text,
    extract_identifiers_from_code,
    normalize_path,
    scan_files,
)
from ontoagent.pipeline.module_clustering import ModuleCluster, ModuleClustering
from ontoagent.pipeline.semantic_linker import (
    build_entity_index,
    check_llm_available,
    fuzzy_lookup_entity,
    link_docs_to_code,
    make_concept_aligner,
    make_semantic_extractor,
    process_semantic_relations,
    resolve_semantic_names,
)
from ontoagent.store.chroma_store import ChromaStore
from ontoagent.store.neo4j_store import Neo4jGraphStore

if TYPE_CHECKING:
    from ontoagent.parsing.extractor.semantic import SemanticRelation
    from ontoagent.parsing.parser.base import ExtractedRelation
    from ontoagent.parsing.parser.doc_parser import DocParser
    from ontoagent.pipeline.aligner import ConceptAligner

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


class OntoAgentBuilder:
    """OntoAgent 构建器，组装解析 → 提取 → 存储流水线。"""

    def __init__(self, config: OntoAgentConfig) -> None:
        """初始化构建器。

        Args:
            config: OntoAgent 配置。
        """
        self._config = config
        self._parsers: dict[str, BaseParser] = {}
        self._register_parser(PythonParser())
        self._register_parser(JavaParser())
        self._extractor = RelationExtractor()
        self._graph_store: Neo4jGraphStore | None = None
        self._chroma_store: ChromaStore | None = None
        self._semantic_extractor: SemanticExtractor | None = None
        self._aligner: ConceptAligner | None = None
        self._clustering: ModuleClustering | None = None
        self._doc_parser: DocParser | None = None
        self._logger = logging.getLogger(__name__)
        self._repo_root: Path | None = None

    def _register_parser(self, parser: BaseParser) -> None:
        """注册解析器。

        Args:
            parser: 解析器实例。
        """
        lang_to_ext: dict[str, str] = {"python": ".py", "java": ".java"}
        ext = lang_to_ext.get(parser.language)
        if ext:
            self._parsers[ext] = parser

    def _get_parser(self, file_path: Path) -> BaseParser | None:
        """根据文件扩展名获取对应解析器。

        Args:
            file_path: 文件路径。

        Returns:
            对应的解析器，如果不存在则返回 None。
        """
        return self._parsers.get(file_path.suffix)

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
        from ontoagent.parsing.parser.doc_parser import DocParser

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

        return all_entities, doc_entities, relations, len(code_files) + len(doc_files), unresolved_imports

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
        batch_time = datetime.now(UTC).isoformat()

        try:
            graph_store.ensure_constraints()

            # Batch write CodeEntities
            code_dicts = [add_provenance(self._entity_to_dict(e), extracted_at=batch_time) for e in all_entities]
            graph_store.merge_nodes_batch("CodeEntity", code_dicts, batch_size=200)
            self._logger.info("[Neo4j] Merged %d CodeEntities complete", len(all_entities))

            # Batch write DocEntities
            doc_dicts = [add_provenance(self._doc_entity_to_dict(d), extracted_at=batch_time) for d in doc_entities]
            graph_store.merge_nodes_batch("DocEntity", doc_dicts, batch_size=200)
            self._logger.info("[Neo4j] Merged %d DocEntities complete", len(doc_entities))

            # Batch write structural relations
            rel_data = [
                {
                    "source_id": rel.source_id,
                    "target_id": rel.target_id,
                    "rel_type": rel.relation_type,
                    "source_label": "CodeEntity",
                    "target_label": "CodeEntity",
                    "properties": add_provenance(
                        {"weight": rel.weight},
                        source="ast_parser",
                        confidence=1.0,
                        extracted_at=batch_time,
                    ),
                }
                for rel in relations
            ]
            graph_store.merge_relations_batch(rel_data, batch_size=200)
            self._logger.info("[Neo4j] Wrote %d structural relations", len(relations))

            # 处理外部 import：创建虚拟节点和关系
            name_to_id = {e.name: e.id for e in all_entities}
            external_modules: dict[str, str] = {}  # name → id
            external_names = sorted({rel.target_name for rel in unresolved_imports})

            # Batch create external module nodes
            ext_dicts = []
            for ext_name in external_names:
                ext_entity = CodeEntity(
                    name=ext_name,
                    entity_type="module",
                    file_path="__external__",
                    language="unknown",
                )
                ext_dicts.append(
                    add_provenance(self._entity_to_dict(ext_entity), source="imported", extracted_at=batch_time)
                )
                external_modules[ext_name] = ext_entity.id
            if ext_dicts:
                graph_store.merge_nodes_batch("CodeEntity", ext_dicts, batch_size=200)

            # Batch create external import relations
            ext_rel_data = []
            for rel in unresolved_imports:
                source_id = name_to_id.get(rel.source_name)
                target_id = external_modules.get(rel.target_name)
                if source_id and target_id:
                    ext_rel_data.append(
                        {
                            "source_id": source_id,
                            "target_id": target_id,
                            "rel_type": "imports",
                            "source_label": "CodeEntity",
                            "target_label": "CodeEntity",
                            "properties": add_provenance(
                                {},
                                source="imported",
                                confidence=1.0,
                                extracted_at=batch_time,
                            ),
                        }
                    )
            ext_rel_count = 0
            if ext_rel_data:
                ext_rel_count = graph_store.merge_relations_batch(ext_rel_data, batch_size=200)

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
        batch_time = datetime.now(UTC).isoformat()

        concepts_created = 0
        semantic_relations_created = 0
        skipped_semantic = False
        errors: list[str] = []
        new_concepts: list[ConceptEntity] = []

        if self._check_llm_available():
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
                            rel_props = add_provenance(
                                {"weight": rel.weight},
                                source="llm_extraction",
                                confidence=clamp_confidence(rel.weight),
                                extracted_at=batch_time,
                            )
                            graph_store.merge_relation(
                                rel.source_id,
                                rel.target_id,
                                rel.relation_type,
                                properties=rel_props,
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

        # Schema 版本检查（lazy import 避免循环引用）
        try:
            from ontoagent.domain.exceptions import OntoAgentError
            from ontoagent.store.migrations.registry import MigrationRegistry
            from ontoagent.store.migrations.runner import MigrationRunner
            from ontoagent.store.schema_version import (
                CURRENT_SCHEMA_VERSION,
                SchemaStatus,
                check_schema_version,
                get_current_db_version,
            )

            graph_store = self._get_graph_store()
            status = check_schema_version(graph_store)
            if status in (SchemaStatus.BEHIND, SchemaStatus.EMPTY):
                registry = MigrationRegistry()
                runner = MigrationRunner(graph_store, registry)
                applied = runner.run_pending()
                if applied:
                    self._logger.info("Auto-applied %d schema migrations: %s", len(applied), applied)
            elif status == SchemaStatus.AHEAD:
                db_ver = get_current_db_version(graph_store)
                raise OntoAgentError(
                    f"Database schema ({db_ver}) is ahead of code ({CURRENT_SCHEMA_VERSION}). Please update OntoAgent."
                )
        except Exception as e:
            self._logger.debug("Schema version check skipped (store unavailable or check failed): %s", e)

        t0 = time.monotonic()
        all_errors: list[str] = []
        aborted = False

        # Pre-build: 清库（如果指定）
        if clear:
            graph_store = self._get_graph_store()
            cleared = graph_store.clear_all()
            self._logger.info("═══ Pre-build: Cleared %d existing nodes ═══", cleared)
            # Bug #1 fix: 同步清理 ChromaDB
            try:
                chroma_store = self._get_chroma_store()
                chroma_cleared = chroma_store.clear_all()
                self._logger.info("═══ Pre-build: Cleared %d ChromaDB vectors ═══", chroma_cleared)
            except Exception as e:
                self._logger.warning("Pre-build: ChromaDB clear failed: %s", e)

        # 批次时间戳（用于溯源字段）
        batch_time = datetime.now(UTC).isoformat()

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
            rel_props = add_provenance(
                {"weight": rel.weight},
                source="ast_parser",
                confidence=1.0,
                extracted_at=batch_time,
            )
            graph_store.merge_relation(
                rel.source_id,
                rel.target_id,
                rel.relation_type,
                properties=rel_props,
                source_label="DocEntity",
                target_label="CodeEntity",
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
        """扫描代码文件（.py, .java）和文档文件，跳过隐藏目录。

        Args:
            repo_path: 仓库根目录路径。

        Returns:
            (code_files, doc_files) 元组，均为已排序的路径列表。
        """
        return scan_files(
            repo_path,
            self._config.build_skip_dirs,
            self._config.build_include_docs,
            self._config.build_doc_extensions,
        )

    @staticmethod
    def _entity_to_dict(entity: CodeEntity) -> dict:
        """将 CodeEntity 转为 Neo4j 属性字典。

        Args:
            entity: 代码实体。

        Returns:
            属性字典。
        """
        return entity_to_dict(entity)

    @staticmethod
    def _doc_entity_to_dict(entity: DocEntity) -> dict:
        """将 DocEntity 转为 Neo4j 属性字典。

        Args:
            entity: 文档实体。

        Returns:
            属性字典。
        """
        return doc_entity_to_dict(entity)

    def _entity_to_text(self, entity: CodeEntity) -> str | None:
        """提取实体的可嵌入文本。

        Args:
            entity: 代码实体。

        Returns:
            可嵌入的文本，无内容时返回构造的最小描述。
        """
        return entity_to_text(entity, self._config.build_source_max_length)

    @staticmethod
    def _extract_identifiers_from_code(code: str) -> set[str]:
        """从 Markdown 代码块中提取 Python 标识符。

        Args:
            code: Markdown 文本内容。

        Returns:
            标识符集合。
        """
        return extract_identifiers_from_code(code)

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
        return link_docs_to_code(doc_entities, entity_index)

    def _check_llm_available(self) -> bool:
        """检查语义提取 LLM 服务是否可用。

        Returns:
            True 表示服务可用，False 表示不可用。
        """
        return check_llm_available(self._config)

    def _init_semantic_extractor(self) -> SemanticExtractor:
        """Lazy init SemanticExtractor。

        Returns:
            SemanticExtractor 实例。
        """
        if self._semantic_extractor is None:
            self._semantic_extractor = make_semantic_extractor(self._config)
        return self._semantic_extractor

    def _init_concept_aligner(self) -> ConceptAligner:
        """Lazy init ConceptAligner（从空概念列表开始）。

        Returns:
            ConceptAligner 实例。
        """
        if self._aligner is None:
            self._aligner = make_concept_aligner(self._get_chroma_store())
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
        return fuzzy_lookup_entity(entity_index, entity_type, name)

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
        return process_semantic_relations(relations, entity_index, repo_root, aligner)

    def _normalize_path(self, file_path: str | None, repo_root: Path) -> str:
        """规范化文件路径为相对于仓库根目录的路径。

        Args:
            file_path: 原始文件路径（可能是绝对或相对路径）。
            repo_root: 仓库根目录路径。

        Returns:
            规范化后的相对路径，空字符串表示无路径。
        """
        return normalize_path(file_path, repo_root)

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
        return build_entity_index(entities, repo_root)

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
        return resolve_semantic_names(
            relations,
            index,
            self._repo_root or Path("."),
            self._logger,
        )

    def close(self) -> None:
        """关闭所有存储连接。"""
        if self._graph_store:
            self._graph_store.close()
        if self._chroma_store:
            self._chroma_store.close()
        if self._semantic_extractor:
            self._semantic_extractor.close()

    def __enter__(self) -> OntoAgentBuilder:
        """进入 context manager。

        Returns:
            self。
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """退出 context manager，关闭资源。"""
        self.close()
