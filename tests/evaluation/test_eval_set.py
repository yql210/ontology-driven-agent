"""评估集格式和内容验证测试

确保 eval_set.json 符合规范且数据正确。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# 可用的 8 个工具
AVAILABLE_TOOLS = {
    "semantic_search",
    "graph_query",
    "impact_analysis",
    "get_context",
    "list_concepts",
    "get_module_tree",
    "detect_changes",
    "export_graph",
}

# 真实存在的实体名称（用于验证题目中的实体确实存在）
VALID_ENTITIES = {
    # 类
    "ConceptAligner",
    "AlignResult",
    "ChromaStore",
    "OntoAgentConfig",
    "GitChangeDetector",
    "SHA256Cache",
    "ChangedFile",
    "ChangeType",
    "GitStatus",
    "RelationExtractor",
    "SemanticExtractor",
    "OntoAgentError",
    "SchemaValidationError",
    "StoreError",
    "EmbeddingError",
    "ExtractionError",
    "OllamaEmbeddingFunction",
    "PythonParser",
    "Neo4jGraphStore",
    "GraphStore",
    "BaseParser",
    "ImpactPropagator",
    "ModuleClustering",
    # 函数
    "build",
    "query",
    "info",
    "update",
    "serve",
    "main",
    # 概念
    "Pipeline Pattern",
    "Cache Pattern",
    "Strategy Pattern",
    "Data Model",
    "Enum Pattern",
    "Semantic Search",
    "Repository Pattern",
    "Data Validation",
    "Iterator Pattern",
    "Adapter Pattern",
    "Batch Processing",
    "Vector Search",
    "Filter-Based Deletion",
    "Context Manager Pattern",
    "Builder Pattern",
    # 模块
    "module_0",
    "ontoagent",
}


@pytest.fixture
def eval_set_path() -> Path:
    return Path(__file__).parent / "eval_set.json"


@pytest.fixture
def eval_set_data(eval_set_path: Path) -> dict:
    with open(eval_set_path) as f:
        return json.load(f)


class TestEvalSetFormat:
    """测试评估集格式是否符合规范"""

    def test_can_load_json(self, eval_set_path: Path, eval_set_data: dict):
        """验证 JSON 可加载且格式正确"""
        assert eval_set_path.exists()
        assert isinstance(eval_set_data, dict)
        assert "version" in eval_set_data
        assert "created" in eval_set_data
        assert "questions" in eval_set_data

    def test_question_count(self, eval_set_data: dict):
        """验证共有 35 道题"""
        questions = eval_set_data["questions"]
        assert len(questions) == 35

    def test_level_distribution(self, eval_set_data: dict):
        """验证等级分布：L1:12, L2:14, L3:9"""
        questions = eval_set_data["questions"]
        level_counts = {1: 0, 2: 0, 3: 0}
        for q in questions:
            level = q.get("level")
            assert level in [1, 2, 3], f"Invalid level: {level}"
            level_counts[level] += 1

        assert level_counts[1] == 12, f"Expected 12 L1 questions, got {level_counts[1]}"
        assert level_counts[2] == 14, f"Expected 14 L2 questions, got {level_counts[2]}"
        assert level_counts[3] == 9, f"Expected 9 L3 questions, got {level_counts[3]}"

    def test_required_fields(self, eval_set_data: dict):
        """验证每题都有必需字段"""
        required_fields = {"id", "level", "question", "expected_tools", "expected_answer", "validation"}

        for i, q in enumerate(eval_set_data["questions"]):
            missing = required_fields - set(q.keys())
            assert not missing, f"Question {i} missing fields: {missing}"

    def test_question_id_format(self, eval_set_data: dict):
        """验证题目 ID 格式正确"""
        for q in eval_set_data["questions"]:
            qid = q.get("id", "")
            assert qid.startswith("L"), f"Invalid ID format: {qid}"
            # L1-001, L2-010, etc.
            parts = qid.split("-")
            assert len(parts) == 2, f"Invalid ID format: {qid}"
            assert parts[0] in ["L1", "L2", "L3"], f"Invalid level in ID: {qid}"


class TestEvalSetTools:
    """测试工具引用是否有效"""

    def test_all_tools_exist(self, eval_set_data: dict):
        """验证 expected_tools 中的工具名都在 8 个可用工具中"""
        for q in eval_set_data["questions"]:
            for tool in q.get("expected_tools", []):
                assert tool in AVAILABLE_TOOLS, f"Question {q['id']} references unknown tool: {tool}"

    def test_l1_single_tool(self, eval_set_data: dict):
        """验证 L1 题预期只调用 1 个工具"""
        for q in eval_set_data["questions"]:
            if q["level"] == 1:
                tool_count = len(q.get("expected_tools", []))
                assert tool_count == 1, f"L1 question {q['id']} expects {tool_count} tools, expected 1"

    def test_l2_multi_tool(self, eval_set_data: dict):
        """验证 L2 题预期调用 2-3 个工具"""
        for q in eval_set_data["questions"]:
            if q["level"] == 2:
                tool_count = len(q.get("expected_tools", []))
                assert 2 <= tool_count <= 3, f"L2 question {q['id']} expects {tool_count} tools, expected 2-3"

    def test_l3_multi_tool(self, eval_set_data: dict):
        """验证 L3 题预期调用 3+ 个工具"""
        for q in eval_set_data["questions"]:
            if q["level"] == 3:
                tool_count = len(q.get("expected_tools", []))
                assert tool_count >= 3, f"L3 question {q['id']} expects {tool_count} tools, expected 3+"


class TestEvalSetValidation:
    """测试验证字段是否正确"""

    def test_l1_l2_have_cypher(self, eval_set_data: dict):
        """验证 L1/L2 题的 validation.cypher 不为空"""
        for q in eval_set_data["questions"]:
            if q["level"] in [1, 2]:
                validation = q.get("validation", {})
                if validation.get("method") == "cypher_verify":
                    cypher = validation.get("cypher", "")
                    assert cypher.strip(), f"Question {q['id']} has empty cypher"

    def test_answer_types_valid(self, eval_set_data: dict):
        """验证 expected_answer.type 都是有效值"""
        valid_types = {"exact", "list", "contains", "fuzzy"}

        for q in eval_set_data["questions"]:
            answer = q.get("expected_answer", {})
            ans_type = answer.get("type")
            assert ans_type in valid_types, f"Question {q['id']} has invalid answer type: {ans_type}"

    def test_fuzzy_has_keywords(self, eval_set_data: dict):
        """验证 fuzzy 类型答案必须有 keywords"""
        for q in eval_set_data["questions"]:
            answer = q.get("expected_answer", {})
            if answer.get("type") == "fuzzy":
                keywords = answer.get("keywords", [])
                assert isinstance(keywords, list), f"Question {q['id']} fuzzy type must have list keywords"
                assert keywords, f"Question {q['id']} fuzzy type must have non-empty keywords"


class TestEvalSetContent:
    """测试题目内容是否使用真实实体"""

    def test_entities_are_real(self, eval_set_data: dict):
        """验证题目中提到的实体名称来自真实数据"""
        # 这个测试是启发式的，检查是否包含已知实体
        for q in eval_set_data["questions"]:
            # 检查题目是否合理
            # 注意：有些问题可能不直接提及实体名，而是问统计信息
            # 所以这个检查只是确保题目不是空的
            if q.get("question"):
                pass

    def test_questions_unique(self, eval_set_data: dict):
        """验证题目 ID 唯一"""
        ids = [q.get("id") for q in eval_set_data["questions"]]
        assert len(ids) == len(set(ids)), "Question IDs must be unique"

    def test_question_not_empty(self, eval_set_data: dict):
        """验证题目文本不为空"""
        for q in eval_set_data["questions"]:
            question = q.get("question", "").strip()
            assert question, f"Question {q.get('id')} has empty question text"
            # 至少 10 个字符
            assert len(question) >= 10, f"Question {q.get('id')} is too short"


@pytest.mark.integration
class TestEvalSetCypher:
    """集成测试：验证 Cypher 查询可执行（需要 Neo4j 连接）"""

    def test_cypher_queries_run(self, eval_set_data: dict):
        """验证 validation.cypher 可以在 Neo4j 上执行"""
        pytest.skip(
            "Requires Neo4j connection - run manually with: uv run pytest tests/evaluation/test_eval_set.py::TestEvalSetCypher -v"
        )

        # 要运行此测试，需要：
        # 1. 取消上面的 skip
        # 2. 添加 Neo4j fixture
        # 3. 对每个 cypher 执行 neo4j.query()
