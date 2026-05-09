"""Incremental update orchestrator for LayerKG."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from layerkg.change_detector import ChangeType
from layerkg.config import LayerKGConfig

if TYPE_CHECKING:
    from layerkg.impact_propagator import ImpactReport

_logger = logging.getLogger(__name__)


@dataclass
class UpdateReport:
    """增量更新结果报告。

    Attributes:
        changes_detected: Stage 1 检测到的变更文件数。
        nodes_added: Stage 3 新增节点数。
        nodes_updated: Stage 3 更新节点数。
        nodes_deleted: Stage 3 删除节点数。
        relations_rebuilt: Stage 3 重建关系数。
        vectors_updated: Stage 3 更新向量数。
        impacted_nodes_count: Stage 2 受影响节点数。
        orphans_removed: 始终为 0（DETACH DELETE 自动清理）。
        changeset_id: ChangeSetEntity ID（空字符串 if dry_run）。
        elapsed_ms: 总耗时（毫秒）。
        parse_errors: 解析失败的文件数。
        failed_files: 解析失败的文件路径列表。
        concepts_flagged: 被标记重提取的概念数。
        docs_flagged: 被标记重生成的文档数。
        integrity_warnings: 完整性检查警告数。
    """

    changes_detected: int
    nodes_added: int
    nodes_updated: int
    nodes_deleted: int
    relations_rebuilt: int
    vectors_updated: int
    impacted_nodes_count: int
    orphans_removed: int
    changeset_id: str
    elapsed_ms: float
    parse_errors: int = 0
    failed_files: list[str] | None = None
    concepts_flagged: int = 0
    docs_flagged: int = 0
    integrity_warnings: int = 0

    def __post_init__(self) -> None:
        """设置默认值。"""
        if self.failed_files is None:
            self.failed_files = []

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "changes_detected": self.changes_detected,
            "nodes_added": self.nodes_added,
            "nodes_updated": self.nodes_updated,
            "nodes_deleted": self.nodes_deleted,
            "relations_rebuilt": self.relations_rebuilt,
            "vectors_updated": self.vectors_updated,
            "impacted_nodes_count": self.impacted_nodes_count,
            "orphans_removed": self.orphans_removed,
            "changeset_id": self.changeset_id,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "parse_errors": self.parse_errors,
            "failed_files": self.failed_files,
            "concepts_flagged": self.concepts_flagged,
            "docs_flagged": self.docs_flagged,
            "integrity_warnings": self.integrity_warnings,
        }


class IncrementalUpdater:
    """增量更新编排器。

    编排四阶段流水线：变更检测 → 影响传播 → 选择性重生成 → 图验证+持久化。
    """

    def __init__(self, config: LayerKGConfig, repo_path: Path | None = None) -> None:
        """初始化。

        Args:
            config: LayerKG 配置。
            repo_path: Git 仓库根目录路径（用于 GitChangeDetector）。
        """
        self._config = config
        self._repo_path = repo_path or Path.cwd()
        # Lazy init stores
        self._graph_store = None
        self._chroma_store = None
        self._change_detector = None
        self._impact_propagator = None
        # Eager init parser and extractor
        from layerkg.extractor.relation import RelationExtractor
        from layerkg.parser.python_parser import PythonParser

        self._parser = PythonParser()
        self._extractor = RelationExtractor()
        self._logger = _logger

    def close(self) -> None:
        """关闭所有存储连接。"""
        if self._graph_store:
            self._graph_store.close()
        if self._chroma_store:
            self._chroma_store.close()

    def __enter__(self) -> IncrementalUpdater:
        """进入 context manager。

        Returns:
            self。
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """退出 context manager，关闭资源。"""
        self.close()

    def _get_change_detector(self):
        """获取或创建 GitChangeDetector（lazy init）。

        Returns:
            GitChangeDetector 实例。
        """
        if self._change_detector is None:
            from layerkg.change_detector import GitChangeDetector

            self._change_detector = GitChangeDetector(repo_path=self._repo_path)
        return self._change_detector

    def _get_graph_store(self):
        """获取或创建 GraphStore（lazy init）。

        Returns:
            GraphStore 实例。
        """
        if self._graph_store is None:
            from layerkg.neo4j_store import Neo4jGraphStore

            self._graph_store = Neo4jGraphStore(
                uri=self._config.neo4j_uri,
                user=self._config.neo4j_user,
                password=self._config.neo4j_password,
            )
        return self._graph_store

    def _get_chroma_store(self):
        """获取或创建 ChromaStore（lazy init）。

        Returns:
            ChromaStore 实例。
        """
        if self._chroma_store is None:
            from layerkg.chroma_store import ChromaStore

            self._chroma_store = ChromaStore(
                persist_dir=self._config.chroma_persist_dir,
                ollama_url=self._config.ollama_base_url,
                embedding_model=self._config.embedding_model,
            )
        return self._chroma_store

    def _detect_changes(self, since: str, *, full_scan: bool = False) -> list:
        """Stage 1: 调用 GitChangeDetector 检测变更。

        Args:
            since: Git ref 对比基准。
            full_scan: 是否使用全量扫描替代 Git diff。

        Returns:
            ChangedFile 列表。
        """
        detector = self._get_change_detector()
        if full_scan:
            return detector.full_scan()
        return detector.detect_changes(since)

    def _get_impact_propagator(self):
        """获取或创建 ImpactPropagator（lazy init）。

        Returns:
            ImpactPropagator 实例。
        """
        if self._impact_propagator is None:
            from layerkg.impact_propagator import ImpactPropagator

            self._impact_propagator = ImpactPropagator(self._get_graph_store())
        return self._impact_propagator

    def _propagate_impact(self, changes: list) -> ImpactReport:
        """Stage 2: 调用 ImpactPropagator 传播影响。

        Args:
            changes: ChangedFile 列表。

        Returns:
            ImpactReport 影响报告。
        """
        from layerkg.impact_propagator import ImpactReport

        if not changes:
            return ImpactReport(
                changed_files=[],
                changed_node_ids=[],
                impacted_nodes=[],
                total_analyzed=0,
                propagation_time_ms=0.0,
            )
        propagator = self._get_impact_propagator()
        return propagator.propagate(changes)

    @staticmethod
    def _entity_to_dict(entity):
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
    def _entity_to_text(entity):
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
        return " ".join(parts) if parts else None

    def _apply_added(self, change):
        """处理新增文件：解析 → 写入图谱 + 向量。

        Args:
            change: ChangedFile 实例。

        Returns:
            计数器 dict: nodes_added, relations_rebuilt, vectors_updated。
        """
        abs_path = Path(change.path)
        if not abs_path.exists():
            return {"nodes_added": 0, "relations_rebuilt": 0, "vectors_updated": 0}

        parse_result = self._parser.parse_file(abs_path)
        if parse_result.error:
            return {"nodes_added": 0, "relations_rebuilt": 0, "vectors_updated": 0, "parse_error": True}

        entities = parse_result.entities
        self._extractor.add_parse_result(entities, parse_result.relations)
        relations = self._extractor.resolve(entities)

        graph_store = self._get_graph_store()
        chroma_store = self._get_chroma_store()

        # 写 Neo4j
        for entity in entities:
            graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))
        for rel in relations:
            graph_store.merge_relation(
                rel.source_id, rel.target_id, rel.relation_type, source_label="CodeEntity", target_label="CodeEntity"
            )

        # 写 ChromaDB
        items = []
        for entity in entities:
            text = self._entity_to_text(entity)
            if text:
                items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))
        if items:
            chroma_store.put_entities_batch(items)

        return {
            "nodes_added": len(entities),
            "relations_rebuilt": len(relations),
            "vectors_updated": len(items),
        }

    def _apply_deleted(self, change) -> dict:
        """处理删除文件：删除节点（DETACH DELETE）+ 删除向量。

        Args:
            change: ChangedFile 实例。

        Returns:
            计数器 dict: nodes_deleted, relations_rebuilt(=0), vectors_updated(=0)。
        """
        graph_store = self._get_graph_store()
        chroma_store = self._get_chroma_store()

        # 查询匹配 file_path 的节点
        cypher = "MATCH (n {file_path: $fp}) RETURN n.id AS id"
        nodes = graph_store.query(cypher, {"fp": change.path})

        nodes_deleted = 0
        for node in nodes:
            node_id = node.get("id")
            if node_id:
                graph_store.delete_node(node_id)
                nodes_deleted += 1

        # 删除向量
        chroma_store.delete_entities_by_metadata({"file_path": change.path})

        return {
            "nodes_deleted": nodes_deleted,
            "relations_rebuilt": 0,
            "vectors_updated": 0,
        }

    def _apply_modified(self, change) -> dict:
        """处理 SIGNATURE/BODY/DOC_ONLY 变更。

        Args:
            change: ChangedFile 实例。

        Returns:
            计数器 dict: nodes_updated, relations_rebuilt, vectors_updated。
        """
        # DOC_ONLY: 只更新向量
        if change.change_type == ChangeType.DOC_ONLY:
            return self._update_vectors_only(change)

        # SIGNATURE/BODY: 查旧节点→删旧关系→重建节点+关系+向量
        abs_path = Path(change.path)
        if not abs_path.exists():
            return {"nodes_updated": 0, "relations_rebuilt": 0, "vectors_updated": 0}

        parse_result = self._parser.parse_file(abs_path)
        if parse_result.error:
            return {"nodes_updated": 0, "relations_rebuilt": 0, "vectors_updated": 0, "parse_error": True}

        graph_store = self._get_graph_store()
        chroma_store = self._get_chroma_store()

        # 查旧节点（同 file_path）
        cypher = "MATCH (n {file_path: $fp}) RETURN n.id AS id"
        old_nodes = graph_store.query(cypher, {"fp": change.path})

        # 对旧节点：只删除关系
        for node in old_nodes:
            node_id = node.get("id")
            if node_id:
                # 删除所有 outgoing 和 incoming 关系
                relations = graph_store.get_relations(source_id=node_id)
                for rel in relations:
                    graph_store.delete_relation(
                        rel["source_id"],
                        rel["target_id"],
                        rel["rel_type"],
                    )
                # 删除 incoming 关系
                incoming_rels = graph_store.get_relations(target_id=node_id)
                for rel in incoming_rels:
                    graph_store.delete_relation(
                        rel["source_id"],
                        rel["target_id"],
                        rel["rel_type"],
                    )

        # 对新解析的 entities：merge_node 写入/更新
        entities = parse_result.entities
        self._extractor.add_parse_result(entities, parse_result.relations)
        relations = self._extractor.resolve(entities)

        for entity in entities:
            graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))

        # 对新解析的 relations：merge_relation 写入
        for rel in relations:
            graph_store.merge_relation(
                rel.source_id, rel.target_id, rel.relation_type, source_label="CodeEntity", target_label="CodeEntity"
            )

        # ChromaDB：先删旧向量，再写新向量
        chroma_store.delete_entities_by_metadata({"file_path": change.path})

        items = []
        for entity in entities:
            text = self._entity_to_text(entity)
            if text:
                items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))
        if items:
            chroma_store.put_entities_batch(items)

        return {
            "nodes_updated": len(entities),
            "relations_rebuilt": len(relations),
            "vectors_updated": len(items),
        }

    def _update_vectors_only(self, change) -> dict:
        """DOC_ONLY 变更：只更新向量，不修改节点和关系。

        Args:
            change: ChangedFile 实例（change_type=DOC_ONLY）。

        Returns:
            计数器 dict: nodes_updated=0, relations_rebuilt=0, vectors_updated=N。
        """
        abs_path = Path(change.path)
        if not abs_path.exists():
            return {"nodes_updated": 0, "relations_rebuilt": 0, "vectors_updated": 0}

        parse_result = self._parser.parse_file(abs_path)
        if parse_result.error:
            return {"nodes_updated": 0, "relations_rebuilt": 0, "vectors_updated": 0, "parse_error": True}

        chroma_store = self._get_chroma_store()

        # 只删除和更新向量
        chroma_store.delete_entities_by_metadata({"file_path": change.path})

        items = []
        for entity in parse_result.entities:
            text = self._entity_to_text(entity)
            if text:
                items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))
        if items:
            chroma_store.put_entities_batch(items)

        return {
            "nodes_updated": 0,
            "relations_rebuilt": 0,
            "vectors_updated": len(items),
        }

    def _flag_concept_reextraction(self, impacted_concept_ids: list[str]) -> int:
        """标记受影响的 ConceptEntity 需要重提取。

        Args:
            impacted_concept_ids: 需要标记的 ConceptEntity ID 列表。

        Returns:
            标记的节点数量。
        """
        if not impacted_concept_ids:
            return 0

        graph_store = self._get_graph_store()
        cypher = "MATCH (n:ConceptEntity) WHERE n.id IN $ids SET n.needs_reextraction = true RETURN count(n) AS count"
        result = graph_store.query(cypher, {"ids": impacted_concept_ids})
        return result[0]["count"] if result else 0

    def _flag_doc_regeneration(self, impacted_doc_ids: list[str]) -> int:
        """标记受影响的 DocEntity 需要重生成。

        Args:
            impacted_doc_ids: 需要标记的 DocEntity ID 列表。

        Returns:
            标记的节点数量。
        """
        if not impacted_doc_ids:
            return 0

        graph_store = self._get_graph_store()
        cypher = "MATCH (n:DocEntity) WHERE n.id IN $ids SET n.needs_regeneration = true RETURN count(n) AS count"
        result = graph_store.query(cypher, {"ids": impacted_doc_ids})
        return result[0]["count"] if result else 0

    def _validate_graph_integrity(self) -> dict:
        """检查图谱完整性（软检查，只记录警告不阻断更新）。

        查询没有关系的孤立 CodeEntity 节点，用于监控图谱健康度。

        Returns:
            dict: {"warnings": int, "orphan_code_entities": list[str]}。
                查询失败时返回 {"warnings": 0, "orphan_code_entities": []}。
        """
        try:
            graph_store = self._get_graph_store()
            # Cypher: 查找没有任何关系的孤立 CodeEntity
            cypher = """
                MATCH (n:CodeEntity)
                WHERE NOT (n)--()
                RETURN n.id AS id, n.name AS name
                LIMIT 100
            """
            orphan_nodes = graph_store.query(cypher)

            warnings = len(orphan_nodes)
            orphan_ids = [node["id"] for node in orphan_nodes if node.get("id")]

            if warnings > 0:
                self._logger.warning(
                    "检测到 %d 个孤立 CodeEntity 节点 (无任何关系)",
                    warnings,
                )

            return {"warnings": warnings, "orphan_code_entities": orphan_ids}
        except Exception as e:
            # 完整性检查失败不影响主流程，记录错误后返回安全值
            self._logger.warning("完整性检查失败: %s", e)
            return {"warnings": 0, "orphan_code_entities": []}

    def _record_changeset(self, changes: list, impact_report, stage3: dict) -> str:
        """记录 ChangeSetEntity 到 Neo4j。

        Args:
            changes: ChangedFile 列表。
            impact_report: Stage 2 影响传播报告。
            stage3: Stage 3 计数器字典。

        Returns:
            changeset_id: 生成的变更集 ID，格式为 "cs-{12hex}"。
        """
        import uuid

        changeset_id = f"cs-{uuid.uuid4().hex[:12]}"
        graph_store = self._get_graph_store()
        graph_store.merge_node(
            "ChangeSetEntity",
            {
                "id": changeset_id,
                "commit_hash": "incremental",
                "files_changed": [c.path for c in changes],
                "impacted_count": len(impact_report.impacted_nodes),
                "nodes_added": stage3.get("nodes_added", 0),
                "nodes_updated": stage3.get("nodes_updated", 0),
                "nodes_deleted": stage3.get("nodes_deleted", 0),
            },
        )
        return changeset_id

    def update(
        self,
        since: str = "HEAD~1",
        *,
        full_scan: bool = False,
        dry_run: bool = False,
    ) -> UpdateReport:
        """执行完整四阶段增量更新。

        Args:
            since: Git ref 对比基准。
            full_scan: 是否全量扫描替代 Git diff。
            dry_run: 只检测不执行（跳过 Stage 3/4）。

        Returns:
            UpdateReport 包含完整的统计信息。
        """
        start_time = time.time()

        # Stage 1: 变更检测
        changes = self._detect_changes(since, full_scan=full_scan)

        # Stage 2: 影响传播
        impact_report = self._propagate_impact(changes)

        # early return: 空变更或 dry_run
        if not changes or dry_run:
            elapsed = (time.time() - start_time) * 1000
            return UpdateReport(
                changes_detected=len(changes),
                nodes_added=0,
                nodes_updated=0,
                nodes_deleted=0,
                relations_rebuilt=0,
                vectors_updated=0,
                impacted_nodes_count=len(impact_report.impacted_nodes),
                orphans_removed=0,
                changeset_id="",
                elapsed_ms=elapsed,
                concepts_flagged=0,
                docs_flagged=0,
                integrity_warnings=0,
            )

        # Stage 3: 选择性重生成
        stage3 = {
            "nodes_added": 0,
            "nodes_updated": 0,
            "nodes_deleted": 0,
            "relations_rebuilt": 0,
            "vectors_updated": 0,
            "parse_errors": 0,
            "failed_files": [],
        }
        for change in changes:
            if change.change_type == ChangeType.ADDED:
                result = self._apply_added(change)
                stage3["nodes_added"] += result["nodes_added"]
            elif change.change_type == ChangeType.DELETED:
                result = self._apply_deleted(change)
                stage3["nodes_deleted"] += result["nodes_deleted"]
            else:
                result = self._apply_modified(change)
                stage3["nodes_updated"] += result.get("nodes_updated", 0)
            stage3["relations_rebuilt"] += result.get("relations_rebuilt", 0)
            stage3["vectors_updated"] += result.get("vectors_updated", 0)
            if result.get("parse_error"):
                stage3["parse_errors"] += 1
                stage3["failed_files"].append(change.path)

        # 标记受影响的 ConceptEntity 需要重提取
        concept_ids = [n.node_id for n in impact_report.impacted_nodes if n.node_label == "ConceptEntity"]
        concepts_flagged = self._flag_concept_reextraction(concept_ids) if concept_ids else 0

        # 标记受影响的 DocEntity 需要重生成
        doc_ids = [n.node_id for n in impact_report.impacted_nodes if n.node_label == "DocEntity"]
        docs_flagged = self._flag_doc_regeneration(doc_ids) if doc_ids else 0

        # Stage 4: 验证持久化
        changeset_id = self._record_changeset(changes, impact_report, stage3)

        # 完整性检查（软检查，不影响主流程）
        integrity = self._validate_graph_integrity()
        integrity_warnings = integrity["warnings"]

        detector = self._get_change_detector()
        detector.update_cache(changes)

        elapsed = (time.time() - start_time) * 1000
        return UpdateReport(
            changes_detected=len(changes),
            nodes_added=stage3["nodes_added"],
            nodes_updated=stage3["nodes_updated"],
            nodes_deleted=stage3["nodes_deleted"],
            relations_rebuilt=stage3["relations_rebuilt"],
            vectors_updated=stage3["vectors_updated"],
            impacted_nodes_count=len(impact_report.impacted_nodes),
            orphans_removed=0,
            changeset_id=changeset_id,
            elapsed_ms=elapsed,
            parse_errors=stage3["parse_errors"],
            failed_files=stage3["failed_files"],
            concepts_flagged=concepts_flagged,
            docs_flagged=docs_flagged,
            integrity_warnings=integrity_warnings,
        )
