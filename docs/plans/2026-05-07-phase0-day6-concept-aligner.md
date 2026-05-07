# Phase 0 Day 6: 概念对齐器 v1

## 目标
实现概念对齐器（ConceptAligner），解决"同一概念每次名字不同"的问题。这是连接底层知识库和上层 Agent 的关键桥梁，也是架构文档强调的"关键创新"。

## 背景
V3.1 架构设计定义了四步概念对齐流水线：
1. 精确匹配 → "用户认证" == "用户认证" ✓
2. 别名匹配 → "登录" ∈ aliases["用户认证"] ✓
3. 向量相似度 → embedding("auth") ≈ embedding("认证") ✓
4. 图结构匹配 → 连接的 CodeEntity 重叠度 > 80% ✓

**Day 6 范围**: Step 1-3（精确+别名+向量）。Step 4（图结构匹配）留到 Phase 1，需要更复杂的图遍历。

## 设计决策

### 1. ConceptAligner 独立于 Builder
概念对齐器是一个独立组件，不依赖 LayerKGBuilder。它接收 ChromaStore + 一组已知 ConceptEntity，对外暴露 `align(term)` 方法。

### 2. AlignResult 数据结构
```python
@dataclass
class AlignResult:
    concept_id: str | None     # 匹配到的 ConceptEntity.id
    concept_name: str | None   # 匹配到的 ConceptEntity.name
    match_type: str            # "exact" / "alias" / "vector" / "none"
    confidence: float          # 1.0 for exact/alias, cosine similarity for vector
    aliases: list[str]         # 该概念的所有别名
```

### 3. 向量匹配的置信度阈值
- exact → confidence = 1.0
- alias → confidence = 1.0
- vector → confidence = cosine_distance（ChromaDB 返回），阈值默认 0.5
- none → confidence = 0.0

### 4. 概念存储方式
ConceptEntity 存储在 ChromaDB 中（用 `entity_type="concept"` 标记），同时也维护一个内存索引用于精确/别名匹配（启动时从 ChromaDB 加载）。

## 文件清单

### 新增文件
| 文件 | 预估行数 | 说明 |
|------|---------|------|
| `src/layerkg/aligner.py` | ~200 | ConceptAligner + AlignResult |
| `tests/unit/test_aligner.py` | ~400 | 概念对齐器测试（~20 tests） |

### 修改文件
| 文件 | 变更 |
|------|------|
| 无 | aligner 是独立模块，不修改已有文件 |

## 实现计划

---

### Task 1: ConceptAligner (aligner.py)

```python
from __future__ import annotations

import logging
from dataclasses import dataclass

from layerkg.chroma_store import ChromaStore
from layerkg.schema import ConceptEntity


@dataclass
class AlignResult:
    """概念对齐结果。

    Attributes:
        concept_id: 匹配到的 ConceptEntity ID，无匹配时为 None。
        concept_name: 匹配到的 ConceptEntity 名称，无匹配时为 None。
        match_type: 匹配类型："exact" / "alias" / "vector" / "none"。
        confidence: 置信度 [0, 1]。
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
    3. 向量相似度 — ChromaDB 语义搜索
    """

    VALID_MATCH_TYPES = {"exact", "alias", "vector", "none"}

    def __init__(
        self,
        chroma_store: ChromaStore,
        concepts: list[ConceptEntity] | None = None,
        vector_threshold: float = 0.5,
    ) -> None:
        """初始化对齐器。

        Args:
            chroma_store: ChromaDB 存储实例（用于向量匹配）。
            concepts: 已知概念列表（用于精确+别名匹配）。
            vector_threshold: 向量匹配的最低置信度阈值。
        """
        self._chroma_store = chroma_store
        self._concepts: dict[str, ConceptEntity] = {}  # name -> ConceptEntity
        self._alias_index: dict[str, str] = {}  # alias_lowercase -> concept_name
        self._vector_threshold = vector_threshold
        self._logger = logging.getLogger(__name__)

        if concepts:
            self._build_index(concepts)

    def _build_index(self, concepts: list[ConceptEntity]) -> None:
        """构建精确匹配和别名匹配的内存索引。

        Args:
            concepts: 概念实体列表。
        """
        for concept in concepts:
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
            self._logger.debug("Vector match: '%s' → '%s' (confidence=%.4f)",
                               term, result.concept_name, result.confidence)
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

        # ChromaDB 返回的是 distance（越小越相似），转为 confidence
        # 默认使用 L2 距离时 distance ≥ 0
        # 简单方案：confidence = 1 / (1 + distance)
        confidence = 1.0 / (1.0 + distance)

        if confidence < self._vector_threshold:
            return None

        metadata = best.get("metadata", {})
        concept_name = metadata.get("name")

        # 从内存索引查找完整 ConceptEntity
        concept = self._concepts.get(concept_name) if concept_name else None

        return AlignResult(
            concept_id=best.get("id", concept.id if concept else None),
            concept_name=concept_name,
            match_type="vector",
            confidence=round(confidence, 4),
            aliases=list(concept.aliases) if concept else [],
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
```

---

### Task 2: 单元测试 (test_aligner.py)

**策略**: Mock ChromaStore，测试对齐逻辑。

**测试用例 (~20 tests)**:

#### AlignResult
1. `test_align_result_creation` — 正常创建
2. `test_no_match_constant` — NO_MATCH 字段验证

#### 精确匹配 (ExactMatch)
3. `test_exact_match_found` — 术语 == 概念名
4. `test_exact_match_case_sensitive` — 大小写敏感（"Auth" ≠ "auth"）
5. `test_exact_match_not_found` — 无匹配

#### 别名匹配 (AliasMatch)
6. `test_alias_match_found` — 术语在别名列表中
7. `test_alias_match_case_insensitive` — 大小写不敏感（"AUTH" 匹配 aliases=["auth"]）
8. `test_alias_match_not_found` — 无匹配
9. `test_alias_match_priority_exact_over_alias` — 精确匹配优先于别名

#### 向量匹配 (VectorMatch)
10. `test_vector_match_found` — 语义搜索命中，confidence > threshold
11. `test_vector_match_below_threshold` — confidence < threshold → none
12. `test_vector_match_no_results` — ChromaDB 无结果
13. `test_vector_match_with_none_distance` — distance 为 None
14. `test_vector_match_after_exact_alias_fail` — 精确/别名都失败后走向量

#### 综合流程 (Full Pipeline)
15. `test_align_pipeline_exact_wins` — 三步流水线，精确匹配优先
16. `test_align_pipeline_alias_second` — 精确失败 → 别名成功
17. `test_align_pipeline_vector_third` — 精确/别名失败 → 向量成功
18. `test_align_pipeline_no_match` — 三步都失败
19. `test_align_empty_term_returns_no_match` — 空术语

#### 批量和管理
20. `test_align_batch_multiple_terms` — 批量对齐
21. `test_add_concept_dynamic` — 动态添加概念
22. `test_list_concepts` — 列出所有概念

---

### Task 3: ruff + 全量测试

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest tests/ -v
```

---

## 依赖关系
```
Task 1 (ConceptAligner)
  └── Task 2 (测试)
       └── Task 3 (验证)
```

## 预期结果
- `src/layerkg/aligner.py` (~200 行)
- `tests/unit/test_aligner.py` (~400 行, ~22 tests)
- 全量测试 198 + 22 = **220 tests**
- Phase 0 Day 6 完成，概念对齐器就绪 🎉
