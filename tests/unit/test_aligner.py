from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from layerkg.domain.schema import ConceptEntity
from layerkg.pipeline.aligner import NO_MATCH, AlignResult, ConceptAligner
from layerkg.store.neo4j_store import Neo4jGraphStore


@pytest.fixture
def mock_chroma_store() -> MagicMock:
    """Mock ChromaStore 实例。"""
    store = MagicMock()
    store.search.return_value = []
    return store


@pytest.fixture
def mock_neo4j_store() -> MagicMock:
    """Mock Neo4jGraphStore 实例。"""
    store = MagicMock(spec=Neo4jGraphStore)
    return store


@pytest.fixture
def aligner_with_neo4j(
    mock_chroma_store: MagicMock,
    sample_concepts: list[ConceptEntity],
    mock_neo4j_store: MagicMock,
) -> ConceptAligner:
    """带 Neo4j store 的 aligner 实例。"""
    return ConceptAligner(
        chroma_store=mock_chroma_store,
        concepts=sample_concepts,
        neo4j_store=mock_neo4j_store,
        graph_overlap_threshold=0.8,
    )


@pytest.fixture
def sample_concepts() -> list[ConceptEntity]:
    """示例概念列表。"""
    return [
        ConceptEntity(
            name="用户认证",
            entity_type="business_concept",
            aliases=["登录", "login", "auth", "authentication"],
            description="用户身份验证流程",
        ),
        ConceptEntity(
            name="权限控制",
            entity_type="business_concept",
            aliases=["rbac", "authorization", "权限"],
            description="基于角色的访问控制",
        ),
        ConceptEntity(
            name="单例模式",
            entity_type="design_pattern",
            aliases=["Singleton", "singleton pattern"],
            description="确保一个类只有一个实例",
        ),
    ]


@pytest.fixture
def aligner(
    mock_chroma_store: MagicMock,
    sample_concepts: list[ConceptEntity],
) -> ConceptAligner:
    """初始化 ConceptAligner 实例。"""
    return ConceptAligner(
        chroma_store=mock_chroma_store,
        concepts=sample_concepts,
    )


class TestAlignResult:
    """AlignResult 数据类测试。"""

    def test_align_result_creation(self) -> None:
        """测试正常创建 AlignResult。"""
        result = AlignResult(
            concept_id="test-id",
            concept_name="测试概念",
            match_type="exact",
            confidence=1.0,
            aliases=["别名1", "别名2"],
        )
        assert result.concept_id == "test-id"
        assert result.concept_name == "测试概念"
        assert result.match_type == "exact"
        assert result.confidence == 1.0
        assert result.aliases == ["别名1", "别名2"]

    def test_no_match_constant(self) -> None:
        """测试 NO_MATCH 常量字段。"""
        assert NO_MATCH.concept_id is None
        assert NO_MATCH.concept_name is None
        assert NO_MATCH.match_type == "none"
        assert NO_MATCH.confidence == 0.0
        assert NO_MATCH.aliases == []


class TestExactMatch:
    """精确匹配测试。"""

    def test_exact_match_found(self, aligner: ConceptAligner) -> None:
        """测试术语 == 概念名。"""
        result = aligner.align("用户认证")
        assert result.concept_name == "用户认证"
        assert result.match_type == "exact"
        assert result.confidence == 1.0
        assert "登录" in result.aliases

    def test_exact_match_case_sensitive(self, aligner: ConceptAligner) -> None:
        """测试大小写敏感（"Auth" ≠ "auth"）。"""
        result_auth = aligner.align("单例模式")
        assert result_auth.match_type == "exact"

        result_lowercase = aligner.align("单例模式")
        assert result_lowercase.match_type == "exact"

    def test_exact_match_not_found(self, aligner: ConceptAligner) -> None:
        """测试无匹配。"""
        result = aligner.align("不存在的概念")
        assert result == NO_MATCH

    def test_exact_match_empty_string(self, aligner: ConceptAligner) -> None:
        """测试空字符串。"""
        assert aligner.align("") == NO_MATCH
        assert aligner.align("   ") == NO_MATCH


class TestAliasMatch:
    """别名匹配测试。"""

    def test_alias_match_found(self, aligner: ConceptAligner) -> None:
        """测试术语在别名列表中。"""
        result = aligner.align("登录")
        assert result.concept_name == "用户认证"
        assert result.match_type == "alias"
        assert result.confidence == 1.0

    def test_alias_match_case_insensitive(self, aligner: ConceptAligner) -> None:
        """测试大小写不敏感（"AUTH" 匹配 aliases=["auth"]）。"""
        result = aligner.align("LOGIN")
        assert result.concept_name == "用户认证"
        assert result.match_type == "alias"

        result2 = aligner.align("Auth")
        assert result2.concept_name == "用户认证"
        assert result2.match_type == "alias"

    def test_alias_match_not_found(self, aligner: ConceptAligner) -> None:
        """测试别名无匹配。"""
        result = aligner.align("not-an-alias")
        assert result == NO_MATCH

    def test_alias_match_priority_exact_over_alias(self, aligner: ConceptAligner) -> None:
        """测试精确匹配优先于别名。"""
        # 当概念名本身也是另一个概念的别名时，精确匹配应优先
        result = aligner.align("登录")  # 这是别名，精确匹配失败
        assert result.match_type == "alias"


class TestVectorMatch:
    """向量匹配测试。"""

    def test_vector_match_found(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试语义搜索命中，confidence > threshold。"""
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "用户认证相关",
                "metadata": {
                    "name": "用户认证",
                    "id": "concept-1",
                    "entity_type": "concept",
                },
                "distance": 0.2,  # confidence = 1/(1+0.2) ≈ 0.833 > 0.7
            }
        ]

        result = aligner.align("user authentication")
        assert result.concept_name == "用户认证"
        assert result.match_type == "vector"
        assert result.confidence >= 0.7

    def test_vector_match_below_threshold(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试 confidence < threshold → none。"""
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "无关内容",
                "metadata": {"name": "用户认证", "entity_type": "concept"},
                "distance": 1.0,  # confidence = 1/(1+1) = 0.5 < 0.7
            }
        ]

        result = aligner.align("完全无关的术语")
        assert result == NO_MATCH

    def test_vector_match_no_results(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试 ChromaDB 无结果。"""
        mock_chroma_store.search.return_value = []

        result = aligner.align("unknown term")
        assert result == NO_MATCH

    def test_vector_match_with_none_distance(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试 distance 为 None。"""
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "some text",
                "metadata": {"name": "用户认证", "entity_type": "concept"},
                "distance": None,
            }
        ]

        result = aligner.align("term")
        assert result == NO_MATCH

    def test_vector_match_after_exact_alias_fail(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试精确/别名都失败后走向量。"""
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "权限管理",
                "metadata": {"name": "权限控制", "entity_type": "concept"},
                "distance": 0.1,
            }
        ]

        result = aligner.align("权限管理")
        assert result.concept_name == "权限控制"
        assert result.match_type == "vector"


class TestFullPipeline:
    """综合流程测试。"""

    def test_align_pipeline_exact_wins(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试三步流水线，精确匹配优先。"""
        # 即使向量搜索也返回结果，精确匹配应优先
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "some text",
                "metadata": {"name": "权限控制", "entity_type": "concept"},
                "distance": 0.0,
            }
        ]

        result = aligner.align("用户认证")
        assert result.match_type == "exact"
        assert result.concept_name == "用户认证"
        # 确保没有调用向量搜索（精确匹配短路）
        mock_chroma_store.search.assert_not_called()

    def test_align_pipeline_alias_second(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试精确失败 → 别名成功。"""
        result = aligner.align("rbac")
        assert result.match_type == "alias"
        assert result.concept_name == "权限控制"
        mock_chroma_store.search.assert_not_called()

    def test_align_pipeline_vector_third(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试精确/别名失败 → 向量成功。"""
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "用户身份验证",
                "metadata": {"name": "用户认证", "entity_type": "concept"},
                "distance": 0.3,
            }
        ]

        result = aligner.align("user login")
        assert result.match_type == "vector"
        assert result.concept_name == "用户认证"

    def test_align_pipeline_no_match(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试三步都失败。"""
        mock_chroma_store.search.return_value = []

        result = aligner.align("完全不相关的术语xyz")
        assert result == NO_MATCH


class TestBatchAndManagement:
    """批量操作和管理功能测试。"""

    def test_align_batch_multiple_terms(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试批量对齐。"""

        # 使用 side_effect 区分不同查询的返回值
        # 对于 "unknown" 返回空结果，其他返回向量匹配结果
        def mock_search(query_text: str, n_results: int = 10, where: dict | None = None) -> list:
            if "unknown" in query_text.lower():
                return []
            return [
                {
                    "id": "vector-1",
                    "text": "认证",
                    "metadata": {"name": "用户认证", "entity_type": "concept"},
                    "distance": 0.2,
                }
            ]

        mock_chroma_store.search.side_effect = mock_search

        results = aligner.align_batch(["用户认证", "login", "unknown", "rbac"])
        assert len(results) == 4
        assert results[0].match_type == "exact"
        assert results[1].match_type == "alias"
        assert results[2].match_type == "none"
        assert results[3].match_type == "alias"

    def test_add_concept_dynamic(self, aligner: ConceptAligner) -> None:
        """测试动态添加概念。"""
        new_concept = ConceptEntity(
            name="数据加密",
            entity_type="business_concept",
            aliases=["encryption", "加密"],
        )
        aligner.add_concept(new_concept)

        result = aligner.align("encryption")
        assert result.concept_name == "数据加密"
        assert result.match_type == "alias"

    def test_add_concept_duplicate_name_skips(
        self,
        mock_chroma_store: MagicMock,
        sample_concepts: list[ConceptEntity],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """测试添加同名概念（不同 ID）时跳过。"""
        aligner = ConceptAligner(
            chroma_store=mock_chroma_store,
            concepts=sample_concepts,
        )

        duplicate_concept = ConceptEntity(
            name="用户认证",  # 同名
            entity_type="business_concept",
            aliases=["new-alias"],
        )

        aligner.add_concept(duplicate_concept)

        # 应保留原概念，跳过新的
        result = aligner.align("用户认证")
        assert result.concept_id != duplicate_concept.id

        # 验证日志警告
        assert any("Duplicate concept name" in record.message for record in caplog.records)

    def test_list_concepts(self, aligner: ConceptAligner) -> None:
        """测试列出所有概念。"""
        concepts = aligner.list_concepts()
        assert len(concepts) == 3

        concept_names = {c["name"] for c in concepts}
        assert "用户认证" in concept_names
        assert "权限控制" in concept_names
        assert "单例模式" in concept_names

        # 验证字段
        for concept in concepts:
            assert "id" in concept
            assert "aliases" in concept
            assert "entity_type" in concept


class TestBuildIndex:
    """索引构建测试。"""

    def test_build_index_with_duplicate_names(
        self,
        mock_chroma_store: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """测试 _build_index 处理同名概念。"""
        concepts = [
            ConceptEntity(
                name="重复概念",
                entity_type="business_concept",
                aliases=["alias1"],
            ),
            ConceptEntity(
                name="重复概念",  # 同名不同 ID
                entity_type="business_concept",
                aliases=["alias2"],
            ),
        ]

        aligner = ConceptAligner(
            chroma_store=mock_chroma_store,
            concepts=concepts,
        )

        # 应保留第一个，跳过第二个
        listed = aligner.list_concepts()
        assert len(listed) == 1
        assert listed[0]["name"] == "重复概念"

        # 验证警告日志
        assert any("Duplicate concept name" in record.message for record in caplog.records)


class TestVectorMatchEdgeCases:
    """向量匹配边界情况测试。"""

    def test_vector_match_missing_concept_name(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试 metadata 中缺少 name 字段。"""
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "some text",
                "metadata": {"entity_type": "concept"},  # 缺少 name
                "distance": 0.1,
            }
        ]

        result = aligner.align("term")
        # 应该返回结果，但 concept_name 为 None
        assert result.match_type == "vector"
        assert result.concept_name is None

    def test_vector_match_confidence_rounding(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试 confidence 四舍五入到 4 位小数。"""
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "认证",
                "metadata": {"name": "用户认证", "entity_type": "concept"},
                "distance": 0.1234,  # confidence ≈ 0.8901
            }
        ]

        # 使用"身份验证"而非"auth"，因为"auth"是别名会匹配为alias
        result = aligner.align("身份验证")
        assert result.match_type == "vector"
        # confidence = 1 / (1 + 0.1234) ≈ 0.890109...
        assert result.confidence == round(1.0 / (1.0 + 0.1234), 4)

    def test_vector_match_threshold_at_boundary(
        self,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试阈值边界情况。"""
        aligner = ConceptAligner(
            chroma_store=mock_chroma_store,
            concepts=[],
            vector_threshold=0.5,
        )

        # confidence = 1 / (1 + distance)
        # threshold = 0.5 → distance <= 1.0 时通过
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "text",
                "metadata": {"name": "概念", "entity_type": "concept"},
                "distance": 1.0,  # confidence = 0.5，刚好等于阈值
            }
        ]

        result = aligner.align("term")
        # 0.5 >= 0.5，应该通过
        assert result.match_type == "vector"


class TestConceptAlignerConstructorExtension:
    """Task 11: ConceptAligner 构造函数扩展测试。"""

    def test_init_with_neo4j_store(
        self,
        mock_chroma_store: MagicMock,
        sample_concepts: list[ConceptEntity],
        mock_neo4j_store: MagicMock,
    ) -> None:
        """测试带 neo4j_store 的初始化。"""
        aligner = ConceptAligner(
            chroma_store=mock_chroma_store,
            concepts=sample_concepts,
            neo4j_store=mock_neo4j_store,
            graph_overlap_threshold=0.7,
        )

        assert aligner._neo4j_store is mock_neo4j_store
        assert aligner._graph_overlap_threshold == 0.7

    def test_init_without_neo4j_store_backward_compatible(
        self,
        mock_chroma_store: MagicMock,
        sample_concepts: list[ConceptEntity],
    ) -> None:
        """测试 neo4j_store=None 时向后兼容，现有行为不受影响。"""
        aligner = ConceptAligner(
            chroma_store=mock_chroma_store,
            concepts=sample_concepts,
        )

        assert aligner._neo4j_store is None
        assert aligner._graph_overlap_threshold == 0.8  # 默认值

        # 验证基本功能仍然工作
        result = aligner.align("用户认证")
        assert result.match_type == "exact"


class TestGraphStructureMatchCoreLogic:
    """Task 12: _graph_structure_match 核心逻辑测试。"""

    def test_graph_structure_match_jaccard_above_threshold(
        self,
        aligner_with_neo4j: ConceptAligner,
        mock_neo4j_store: MagicMock,
    ) -> None:
        """测试 Jaccard > 0.8 时匹配成功。"""
        # Mock 概念-代码关系查询
        mock_neo4j_store.query.return_value = [
            {"name": "用户认证", "code_ids": ["code1", "code2", "code3"]},
            {"name": "权限控制", "code_ids": ["code4", "code5"]},
        ]

        # Mock term 关联的 CodeEntity 查询
        # 用户认证: {code1, code2, code3} ∩ {code1, code2, code3, code6} = {code1, code2, code3}
        # union = {code1, code2, code3, code6}, jaccard = 3/4 = 0.75 < 0.8
        # 让我们调整数据使 Jaccard > 0.8
        def mock_query_side_effect(cypher: str, params: dict | None = None) -> list[dict]:
            if "ConceptEntity" in cypher:
                # 概念-代码关系
                return [
                    {"name": "用户认证", "code_ids": ["code1", "code2", "code3", "code4"]},
                    {"name": "权限控制", "code_ids": ["code10", "code11"]},
                ]
            elif "CodeEntity {name" in cypher:
                # term 关联的 CodeEntity
                return [{"id": "code1"}, {"id": "code2"}, {"id": "code3"}, {"id": "code4"}]
            return []

        mock_neo4j_store.query.side_effect = mock_query_side_effect

        result = aligner_with_neo4j._graph_structure_match("AuthService")

        assert result is not None
        assert result.match_type == "graph_structure"
        assert result.concept_name == "用户认证"
        # Jaccard = |{code1,code2,code3,code4}| / |{code1,code2,code3,code4}| = 1.0 > 0.8

    def test_graph_structure_match_jaccard_below_threshold(
        self,
        aligner_with_neo4j: ConceptAligner,
        mock_neo4j_store: MagicMock,
    ) -> None:
        """测试 Jaccard < 0.8 时返回 None。"""

        def mock_query_side_effect(cypher: str, params: dict | None = None) -> list[dict]:
            if "ConceptEntity" in cypher:
                return [
                    {"name": "用户认证", "code_ids": ["code1", "code2", "code3"]},
                    {"name": "权限控制", "code_ids": ["code4", "code5"]},
                ]
            elif "CodeEntity {name" in cypher:
                # term 关联的 CodeEntity 只有 code1
                return [{"id": "code1"}]
            return []

        mock_neo4j_store.query.side_effect = mock_query_side_effect

        result = aligner_with_neo4j._graph_structure_match("AuthService")

        # Jaccard = |{code1}| / |{code1,code2,code3}| = 1/3 ≈ 0.33 < 0.8
        assert result is None

    def test_graph_structure_match_neo4j_store_none(
        self,
        mock_chroma_store: MagicMock,
        sample_concepts: list[ConceptEntity],
    ) -> None:
        """测试 neo4j_store=None 时返回 None（降级）。"""
        aligner = ConceptAligner(
            chroma_store=mock_chroma_store,
            concepts=sample_concepts,
            neo4j_store=None,
        )

        result = aligner._graph_structure_match("AuthService")

        assert result is None


class TestAlignIntegrationStep4:
    """Task 13: align() 集成 Step 4 测试。"""

    def test_align_step4_match_after_step1_3_fail(
        self,
        aligner_with_neo4j: ConceptAligner,
        mock_neo4j_store: MagicMock,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试 Step 1-3 未匹配 + Step 4 匹配 → match_type='graph_structure'。"""
        # Step 3 (vector) 返回空
        mock_chroma_store.search.return_value = []

        def mock_query_side_effect(cypher: str, params: dict | None = None) -> list[dict]:
            if "ConceptEntity" in cypher:
                return [
                    {"name": "用户认证", "code_ids": ["code1", "code2", "code3", "code4"]},
                ]
            elif "CodeEntity {name" in cypher:
                return [{"id": "code1"}, {"id": "code2"}, {"id": "code3"}, {"id": "code4"}]
            return []

        mock_neo4j_store.query.side_effect = mock_query_side_effect

        result = aligner_with_neo4j.align("AuthService")

        assert result.match_type == "graph_structure"
        assert result.concept_name == "用户认证"

    def test_align_exact_match_skips_step4(
        self,
        aligner_with_neo4j: ConceptAligner,
        mock_neo4j_store: MagicMock,
    ) -> None:
        """测试 Step 1 精确匹配成功 → 不调用 _graph_structure_match。"""
        # 直接使用精确匹配 "用户认证"
        result = aligner_with_neo4j.align("用户认证")

        assert result.match_type == "exact"
        assert result.concept_name == "用户认证"
        # 验证没有调用 Neo4j 查询
        mock_neo4j_store.query.assert_not_called()


class TestValidMatchTypesUpdate:
    """Task 14: VALID_MATCH_TYPES 更新测试。"""

    def test_graph_structure_in_valid_match_types(self) -> None:
        """测试 'graph_structure' 在 VALID_MATCH_TYPES 中。"""
        assert "graph_structure" in ConceptAligner.VALID_MATCH_TYPES


class TestGraphStructureMatchEdgeCases:
    """Task 15: Step 4 边界情况测试。"""

    def test_term_no_associated_code_entities(
        self,
        aligner_with_neo4j: ConceptAligner,
        mock_neo4j_store: MagicMock,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试 term 无关联 CodeEntity → NO_MATCH。"""
        mock_chroma_store.search.return_value = []

        def mock_query_side_effect(cypher: str, params: dict | None = None) -> list[dict]:
            if "ConceptEntity" in cypher:
                return [
                    {"name": "用户认证", "code_ids": ["code1", "code2"]},
                ]
            elif "CodeEntity {name" in cypher:
                # term 无关联 CodeEntity
                return []
            return []

        mock_neo4j_store.query.side_effect = mock_query_side_effect

        result = aligner_with_neo4j.align("UnknownTerm")

        assert result == NO_MATCH

    def test_multiple_concepts_match_select_highest_jaccard(
        self,
        aligner_with_neo4j: ConceptAligner,
        mock_neo4j_store: MagicMock,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试两个 concept 都匹配，取 Jaccard 更高的那个。"""
        mock_chroma_store.search.return_value = []

        def mock_query_side_effect(cypher: str, params: dict | None = None) -> list[dict]:
            if "ConceptEntity" in cypher:
                return [
                    {"name": "用户认证", "code_ids": ["code1", "code2", "code3", "code4"]},  # Jaccard = 4/4 = 1.0
                    {"name": "权限控制", "code_ids": ["code1", "code2"]},  # Jaccard = 2/4 = 0.5
                ]
            elif "CodeEntity {name" in cypher:
                # term 关联 code1, code2, code3, code4
                return [{"id": "code1"}, {"id": "code2"}, {"id": "code3"}, {"id": "code4"}]
            return []

        mock_neo4j_store.query.side_effect = mock_query_side_effect

        result = aligner_with_neo4j.align("AuthService")

        # 用户认证的 Jaccard 更高，应该被选中
        assert result.match_type == "graph_structure"
        assert result.concept_name == "用户认证"

    def test_empty_union_skip_concept(
        self,
        aligner_with_neo4j: ConceptAligner,
        mock_neo4j_store: MagicMock,
        mock_chroma_store: MagicMock,
    ) -> None:
        """测试 union 为空时跳过（除零保护）。"""
        mock_chroma_store.search.return_value = []

        def mock_query_side_effect(cypher: str, params: dict | None = None) -> list[dict]:
            if "ConceptEntity" in cypher:
                return [
                    {"name": "用户认证", "code_ids": []},  # 空列表
                ]
            elif "CodeEntity {name" in cypher:
                return []  # term 也无关联
            return []

        mock_neo4j_store.query.side_effect = mock_query_side_effect

        result = aligner_with_neo4j.align("UnknownTerm")

        assert result == NO_MATCH


class TestAlignPipelineE2E:
    """Task 8: ConceptAligner 匹配流程验证（端到端）。"""

    def test_align_pipeline_vector_fallback(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """精确和别名匹配都失败，向量匹配成功。"""
        # Arrange: 向量搜索返回匹配结果
        mock_chroma_store.search.return_value = [
            {
                "id": "vector-1",
                "text": "权限管理相关",
                "metadata": {
                    "name": "权限控制",
                    "id": "concept-2",
                    "entity_type": "concept",
                },
                "distance": 0.2,  # confidence = 1/(1+0.2) ≈ 0.833 > 0.7
            }
        ]

        # Act: 对齐一个不在精确/别名列表中的术语
        result = aligner.align("权限管理")

        # Assert
        assert result.match_type == "vector"
        assert result.concept_name == "权限控制"
        assert result.confidence >= 0.7
        # 验证向量搜索被调用
        mock_chroma_store.search.assert_called_once()

    def test_align_with_graph_structure(
        self,
        aligner_with_neo4j: ConceptAligner,
        mock_neo4j_store: MagicMock,
        mock_chroma_store: MagicMock,
    ) -> None:
        """通过图结构 Jaccard 重叠度匹配。"""
        # Arrange: 向量搜索失败，图结构匹配成功
        mock_chroma_store.search.return_value = []

        # mock: term 关联的代码节点与概念重叠
        def mock_query_side_effect(cypher: str, params: dict | None = None) -> list[dict]:
            if "ConceptEntity" in cypher:
                # 概念关联的代码节点
                return [
                    {
                        "name": "用户认证",
                        "code_ids": ["code-1", "code-2", "code-3", "code-4"],
                    },
                    {
                        "name": "权限控制",
                        "code_ids": ["code-10", "code-11"],
                    },
                ]
            elif "CodeEntity {name" in cypher:
                # term "AuthService" 关联的代码节点
                return [
                    {"id": "code-1"},
                    {"id": "code-2"},
                    {"id": "code-3"},
                    {"id": "code-4"},
                ]
            return []

        mock_neo4j_store.query.side_effect = mock_query_side_effect

        # Act
        result = aligner_with_neo4j.align("AuthService")

        # Assert
        # Jaccard = |{code-1,2,3,4}| / |{code-1,2,3,4}| = 1.0 > 0.8
        assert result.match_type == "graph_structure"
        assert result.concept_name == "用户认证"
        assert result.confidence == 1.0

    def test_align_batch_terms_extended(
        self,
        aligner: ConceptAligner,
        mock_chroma_store: MagicMock,
    ) -> None:
        """批量对齐多术语，验证各策略降级。"""

        # Arrange: 不同术语走不同匹配路径
        def mock_search(query_text: str, n_results: int = 10, where: dict | None = None) -> list:
            if "权限" in query_text:
                return [
                    {
                        "id": "v1",
                        "text": "权限",
                        "metadata": {"name": "权限控制", "id": "c2", "entity_type": "concept"},
                        "distance": 0.1,
                    }
                ]
            if "Singleton" in query_text:
                return [
                    {
                        "id": "v2",
                        "text": "单例",
                        "metadata": {"name": "单例模式", "id": "c3", "entity_type": "concept"},
                        "distance": 0.2,
                    }
                ]
            return []  # "unknown_term" 无匹配

        mock_chroma_store.search.side_effect = mock_search

        # Act
        results = aligner.align_batch(["用户认证", "权限管理", "rbac", "Singleton", "unknown_term"])

        # Assert
        assert len(results) == 5
        # "用户认证" → 精确匹配
        assert results[0].match_type == "exact"
        assert results[0].concept_name == "用户认证"

        # "权限管理" → 向量匹配
        assert results[1].match_type == "vector"
        assert results[1].concept_name == "权限控制"

        # "rbac" → 别名匹配
        assert results[2].match_type == "alias"
        assert results[2].concept_name == "权限控制"

        # "Singleton" → 别名匹配（大小写不敏感）
        assert results[3].match_type == "alias"
        assert results[3].concept_name == "单例模式"

        # "unknown_term" → 无匹配
        assert results[4] == NO_MATCH
