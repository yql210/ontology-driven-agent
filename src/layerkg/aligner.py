from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from layerkg.chroma_store import ChromaStore
from layerkg.schema import ConceptEntity

if TYPE_CHECKING:
    from layerkg.neo4j_store import Neo4jGraphStore


@dataclass
class AlignResult:
    """概念对齐结果。

    Attributes:
        concept_id: 匹配到的 ConceptEntity ID，无匹配时为 None。
        concept_name: 匹配到的 ConceptEntity 名称，无匹配时为 None。
        match_type: 匹配类型："exact" / "alias" / "vector" / "none"。
        confidence: 置信度 [0, 1]。exact/alias 为 1.0，vector 为 cosine similarity。
        aliases: 该概念的所有别名。
    """

    concept_id: str | None
    concept_name: str | None
    match_type: str  # "exact" | "alias" | "vector" | "none"
    confidence: float
    aliases: list[str]


# 无匹配时的常量结果
NO_MATCH = AlignResult(
    concept_id=None,
    concept_name=None,
    match_type="none",
    confidence=0.0,
    aliases=[],
)


class ConceptAligner:
    """概念对齐器：解决术语漂移问题。

    三步对齐流水线：
    1. 精确匹配 — term == concept.name
    2. 别名匹配 — term in concept.aliases
    3. 向量相似度 — ChromaDB 语义搜索（cosine distance）
    """

    VALID_MATCH_TYPES = {"exact", "alias", "vector", "graph_structure", "none"}

    def __init__(
        self,
        chroma_store: ChromaStore,
        concepts: list[ConceptEntity] | None = None,
        vector_threshold: float = 0.7,
        neo4j_store: Neo4jGraphStore | None = None,
        graph_overlap_threshold: float = 0.8,
    ) -> None:
        """初始化对齐器。

        Args:
            chroma_store: ChromaDB 存储实例（用于向量匹配）。
            concepts: 已知概念列表（用于精确+别名匹配）。
            vector_threshold: 向量匹配的最低置信度阈值（默认 0.7）。
            neo4j_store: Neo4j 图数据库实例（用于图结构匹配，可选）。
            graph_overlap_threshold: 图结构匹配的最低重叠度阈值（默认 0.8）。
        """
        self._chroma_store = chroma_store
        self._concepts: dict[str, ConceptEntity] = {}  # name -> ConceptEntity
        self._alias_index: dict[str, str] = {}  # alias_lowercase -> concept_name
        self._vector_threshold = vector_threshold
        self._neo4j_store = neo4j_store
        self._graph_overlap_threshold = graph_overlap_threshold
        self._concept_code_map: dict[str, set[str]] | None = None
        self._logger = logging.getLogger(__name__)

        if concepts:
            self._build_index(concepts)

    def _build_index(self, concepts: list[ConceptEntity]) -> None:
        """构建精确匹配和别名匹配的内存索引。

        Args:
            concepts: 概念实体列表。
        """
        for concept in concepts:
            # 同名概念保护：保留先入的，跳过后者
            if concept.name in self._concepts:
                existing = self._concepts[concept.name]
                if existing.id != concept.id:
                    self._logger.warning(
                        "Duplicate concept name '%s': existing=%s, skipping=%s",
                        concept.name,
                        existing.id,
                        concept.id,
                    )
                    continue

            # 精确匹配索引（name → ConceptEntity）
            self._concepts[concept.name] = concept
            # 别名索引（alias → concept.name），全部小写化
            for alias in concept.aliases:
                self._alias_index[alias.lower()] = concept.name

    def add_concept(self, concept: ConceptEntity) -> None:
        """动态添加新概念到索引。

        Args:
            concept: 新概念实体。
        """
        # 同名概念保护
        if concept.name in self._concepts:
            existing = self._concepts[concept.name]
            if existing.id != concept.id:
                self._logger.warning(
                    "Duplicate concept name '%s': existing=%s, skipping=%s",
                    concept.name,
                    existing.id,
                    concept.id,
                )
                return

        self._concepts[concept.name] = concept
        for alias in concept.aliases:
            self._alias_index[alias.lower()] = concept.name

    def align(self, term: str) -> AlignResult:
        """对齐术语到已知概念。

        按优先级依次尝试：精确匹配 → 别名匹配 → 向量相似度。

        Args:
            term: 待对齐的术语。

        Returns:
            对齐结果，无匹配时返回 match_type="none"。
        """
        if not term or not term.strip():
            return NO_MATCH

        # Step 1: 精确匹配
        result = self._exact_match(term)
        if result is not None:
            self._logger.debug("Exact match: '%s' → '%s'", term, result.concept_name)
            return result

        # Step 2: 别名匹配
        result = self._alias_match(term)
        if result is not None:
            self._logger.debug("Alias match: '%s' → '%s'", term, result.concept_name)
            return result

        # Step 3: 向量相似度匹配
        result = self._vector_match(term)
        if result is not None:
            self._logger.debug(
                "Vector match: '%s' → '%s' (confidence=%.4f)", term, result.concept_name, result.confidence
            )
            return result

        # Step 4: 图结构匹配（仅当 neo4j_store 可用时）
        result = self._graph_structure_match(term)
        if result is not None:
            self._logger.debug(
                "Graph structure match: '%s' → '%s' (confidence=%.4f)",
                term,
                result.concept_name,
                result.confidence,
            )
            return result

        self._logger.debug("No match for '%s'", term)
        return NO_MATCH

    def align_batch(self, terms: list[str]) -> list[AlignResult]:
        """批量对齐术语。

        Args:
            terms: 待对齐的术语列表。

        Returns:
            对齐结果列表，与输入顺序一一对应。
        """
        return [self.align(term) for term in terms]

    def _exact_match(self, term: str) -> AlignResult | None:
        """精确匹配。

        Args:
            term: 待匹配术语。

        Returns:
            匹配结果，无匹配时返回 None。
        """
        concept = self._concepts.get(term)
        if concept is not None:
            return AlignResult(
                concept_id=concept.id,
                concept_name=concept.name,
                match_type="exact",
                confidence=1.0,
                aliases=list(concept.aliases),
            )
        return None

    def _alias_match(self, term: str) -> AlignResult | None:
        """别名匹配（大小写不敏感）。

        Args:
            term: 待匹配术语。

        Returns:
            匹配结果，无匹配时返回 None。
        """
        concept_name = self._alias_index.get(term.lower())
        if concept_name is not None:
            concept = self._concepts[concept_name]
            return AlignResult(
                concept_id=concept.id,
                concept_name=concept.name,
                match_type="alias",
                confidence=1.0,
                aliases=list(concept.aliases),
            )
        return None

    def _vector_match(self, term: str) -> AlignResult | None:
        """向量相似度匹配。

        通过 ChromaDB 语义搜索，限定 entity_type="concept"。
        假设 ChromaDB 使用 cosine distance（hnsw:space=cosine）。

        Args:
            term: 待匹配术语。

        Returns:
            匹配结果，无匹配或低于阈值时返回 None。
        """
        results = self._chroma_store.search(
            query_text=term,
            n_results=1,
            where={"entity_type": "concept"},
        )
        if not results:
            return None

        best = results[0]
        distance = best.get("distance")
        if distance is None:
            return None

        # ChromaDB 返回的是 cosine distance（范围 [0, 2]），转为 similarity
        # cosine_similarity = 1 - cosine_distance
        # 但 ChromaDB 某些配置下可能返回其他距离度量
        # 这里使用通用转换：confidence = 1 / (1 + distance)
        # 当 distance=0（完全相同）→ confidence=1.0
        # 当 distance=1（正交）→ confidence=0.5
        confidence = 1.0 / (1.0 + distance)

        if confidence < self._vector_threshold:
            return None

        metadata = best.get("metadata", {})
        concept_name = metadata.get("name")

        # 从内存索引查找完整 ConceptEntity
        concept = self._concepts.get(concept_name) if concept_name else None

        # concept_id 优先级：metadata.id > concept.id > None
        concept_id = metadata.get("id")
        if concept_id is None and concept is not None:
            concept_id = concept.id

        return AlignResult(
            concept_id=concept_id,
            concept_name=concept_name,
            match_type="vector",
            confidence=round(confidence, 4),
            aliases=list(concept.aliases) if concept else [],
        )

    def _graph_structure_match(self, term: str) -> AlignResult | None:
        """Step 4: 图结构匹配。

        通过 Neo4j 图数据库，计算 term 关联的代码实体与已知概念关联的代码实体的
        Jaccard 重叠度，选择重叠度最高且超过阈值的概念。

        Args:
            term: 待匹配术语。

        Returns:
            匹配结果，无匹配或 neo4j_store 为 None 时返回 None。
        """
        if self._neo4j_store is None:
            return None

        # 懒加载概念-代码关系（缓存）
        if self._concept_code_map is None:
            cypher = """
            MATCH (concept:ConceptEntity)-[:DERIVED_FROM|SEMANTIC_IMPACT]-(code:CodeEntity)
            RETURN concept.name AS name, collect(DISTINCT code.id) AS code_ids
            """
            results = self._neo4j_store.query(cypher)
            self._concept_code_map = {
                row["name"]: set(row["code_ids"]) for row in results
            }

        # 查询 term（作为 CodeEntity name）关联的 CodeEntity
        term_cypher = """
        MATCH (c:CodeEntity {name: $term})-[*1..2]-(other:CodeEntity)
        RETURN DISTINCT other.id AS id
        """
        term_results = self._neo4j_store.query(term_cypher, {"term": term})
        term_codes: set[str] = {row["id"] for row in term_results}

        if not term_codes:
            return None

        # 对每个概念计算 Jaccard 重叠度
        best_concept_name: str | None = None
        best_jaccard = 0.0

        for concept_name, concept_codes in self._concept_code_map.items():
            if not concept_codes:
                continue  # 跳过空概念（除零保护）

            # Jaccard = |intersection| / |union|
            intersection = term_codes & concept_codes
            union = term_codes | concept_codes

            if not union:
                continue  # 除零保护

            jaccard = len(intersection) / len(union)

            if jaccard > best_jaccard and jaccard > self._graph_overlap_threshold:
                best_jaccard = jaccard
                best_concept_name = concept_name

        if best_concept_name is None:
            return None

        concept = self._concepts.get(best_concept_name)
        if concept is None:
            return None

        return AlignResult(
            concept_id=concept.id,
            concept_name=concept.name,
            match_type="graph_structure",
            confidence=round(best_jaccard, 4),
            aliases=list(concept.aliases),
        )

    def list_concepts(self) -> list[dict]:
        """列出所有已注册的概念。

        Returns:
            概念信息列表，每项包含 name, id, aliases, entity_type。
        """
        return [
            {
                "name": c.name,
                "id": c.id,
                "aliases": list(c.aliases),
                "entity_type": c.entity_type,
            }
            for c in self._concepts.values()
        ]
