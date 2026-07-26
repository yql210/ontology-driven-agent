"""CypherToNgqlAdapter 单元测试。

纯字符串输入/输出测试，无需数据库。
"""

from __future__ import annotations

import pytest

from ontoagent.store.cypher_adapter import CypherToNgqlAdapter, _split_top_level


@pytest.fixture
def adapter() -> CypherToNgqlAdapter:
    """每个测试独立 adapter 实例（无状态，便于隔离）。"""
    return CypherToNgqlAdapter()


# ============================================================================
# 1. labels(n) → tags(n)
# ============================================================================


@pytest.mark.unit
class TestLabelsToTags:
    """``labels(...)`` → ``tags(...)`` 替换。"""

    def test_simple_labels_call(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n) RETURN labels(n) AS labels")
        assert "tags(n)" in result
        assert "labels(" not in result

    def test_size_labels_to_size_tags(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n) WHERE size(labels(n)) > 0 RETURN n")
        assert "size(tags(n))" in result

    def test_labels_index_access(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n) RETURN labels(n)[0] AS label")
        assert "tags(n)[0]" in result

    def test_labels_in_complex_expression(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n) WHERE size(labels(n)) > 0 AND labels(n)[0] IN $types RETURN n")
        assert "size(tags(n))" in result
        assert "tags(n)[0]" in result


# ============================================================================
# 2. n.field → n.Tag.field
# ============================================================================


@pytest.mark.unit
class TestPropertyAccess:
    """``n.field`` → ``n.Tag.field`` 补全。"""

    def test_property_access_gets_tag_prefix(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n:CodeEntity) RETURN n.name AS name")
        assert "n.CodeEntity.name" in result

    def test_property_access_id_becomes_id_function(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n:CodeEntity) RETURN n.id AS id")
        assert "id(n)" in result
        assert "n.id" not in result

    def test_property_access_already_prefixed_unchanged(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n:CodeEntity) RETURN n.CodeEntity.name AS name")
        assert "n.CodeEntity.name" in result
        # 不应该出现 n.CodeEntity.CodeEntity.name 这种重复
        assert "CodeEntity.CodeEntity" not in result

    def test_property_access_multiple_vars(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt(
            "MATCH (a:CodeEntity)-[:CALLS]->(b:CodeEntity) RETURN a.name AS caller, b.name AS callee"
        )
        assert "a.CodeEntity.name" in result
        assert "b.CodeEntity.name" in result

    def test_property_access_no_tag_map_passthrough(self, adapter: CypherToNgqlAdapter) -> None:
        """无 Tag 信息时（如 MATCH (n)），属性访问原样返回。"""
        result = adapter.adapt("MATCH (n) RETURN n.name")
        # 没有 Tag 信息，无法补全前缀；保持原样
        assert result == "MATCH (n) RETURN n.name"


# ============================================================================
# 3. inline property matching → WHERE
# ============================================================================


@pytest.mark.unit
class TestInlineMatching:
    """``(n:Tag {prop: $val})`` → ``(n:Tag) WHERE n.Tag.prop == $val``。"""

    def test_inline_matching_converted_to_where(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n:CodeEntity {name: $val}) RETURN n")
        assert "(n:CodeEntity {name: $val})" not in result
        assert "(n:CodeEntity)" in result
        assert "WHERE n.CodeEntity.name == $val" in result

    def test_inline_matching_multiple_props(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n:CodeEntity {name: $a, age: $b}) RETURN n")
        assert "n.CodeEntity.name == $a" in result
        assert "n.CodeEntity.age == $b" in result
        assert "AND" in result

    def test_inline_matching_appends_to_existing_where(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n:CodeEntity {name: $val}) WHERE n.age > 18 RETURN n")
        # 原 WHERE 应该被保留并 AND 新条件
        assert "WHERE n.CodeEntity.name == $val AND" in result
        # n.age 已被属性访问补全为 n.CodeEntity.age
        assert "n.CodeEntity.age > 18" in result

    def test_inline_matching_inserts_where_before_return(self, adapter: CypherToNgqlAdapter) -> None:
        """无 WHERE 时，WHERE 子句应插在 RETURN 之前。"""
        result = adapter.adapt("MATCH (n:CodeEntity {name: $val}) RETURN n")
        where_pos = result.index("WHERE")
        return_pos = result.index("RETURN")
        assert where_pos < return_pos


# ============================================================================
# 4. = → == (WHERE 中)
# ============================================================================


@pytest.mark.unit
class TestEqualityFix:
    """WHERE 中的单 ``=`` → ``==``。"""

    def test_single_equal_to_double(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n:CodeEntity) WHERE n.name = $val RETURN n")
        assert "n.CodeEntity.name == $val" in result
        # 不应残留单 =（在 WHERE body 内）
        assert "= $val" not in result.replace("== $val", "")

    def test_preserves_greater_equal(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n) WHERE n.age >= 18 RETURN n")
        assert ">=" in result
        # 不应变成 >== 这种诡异形式
        assert ">==" not in result

    def test_preserves_less_equal(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n) WHERE n.age <= 18 RETURN n")
        assert "<=" in result
        assert "<==" not in result

    def test_preserves_not_equal(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (n) WHERE n.x != 1 RETURN n")
        assert "!=" in result
        assert "!==" not in result

    def test_set_clause_not_affected(self, adapter: CypherToNgqlAdapter) -> None:
        """SET 子句的 = 不应被改成 ==。"""
        # 这条查询 inline matching 转换后会产生 WHERE id(n) == 'x'（合法的 ==）
        # 但 SET 的赋值 = 必须保留为单 =
        result = adapter.adapt("MERGE (n:CodeEntity {id: 'x'}) SET n.flag = true RETURN n")
        # SET 子句的 = 必须保留（不能变成 ==）
        assert "SET n.CodeEntity.flag = true" in result or "SET n.flag = true" in result
        assert "flag == true" not in result

    def test_where_before_set_only_where_body_changed(self, adapter: CypherToNgqlAdapter) -> None:
        """WHERE x = 1 SET y = 2 → WHERE x == 1 SET y = 2。"""
        result = adapter.adapt("MATCH (n) WHERE n.x = 1 SET n.y = 2 RETURN n")
        assert "WHERE n.x == 1" in result
        assert "SET n.y = 2" in result


# ============================================================================
# 5. startNode(r)/endNode(r) → id(a)/id(b) 或 src(r)/dst(r)
# ============================================================================


@pytest.mark.unit
class TestStartEndNode:
    """``startNode(r).id`` / ``endNode(r).id`` 重写。"""

    def test_start_node_replaced_with_pattern_var(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("MATCH (a)-[r]->(b) RETURN startNode(r).id AS source, endNode(r).id AS target")
        assert "id(a) AS source" in result
        assert "id(b) AS target" in result
        assert "startNode" not in result
        assert "endNode" not in result

    def test_start_end_node_with_typed_edge(self, adapter: CypherToNgqlAdapter) -> None:
        """带关系类型的 pattern 也要正确提取变量。"""
        result = adapter.adapt("MATCH (a)-[r:CALLS]->(b) RETURN startNode(r).id AS source")
        assert "id(a) AS source" in result

    def test_start_end_node_anonymous_pattern_uses_src_dst(self, adapter: CypherToNgqlAdapter) -> None:
        """匿名节点 ``()-[r]->()`` 时使用 src(r)/dst(r)。"""
        result = adapter.adapt("MATCH ()-[r]->() RETURN startNode(r).id AS source, endNode(r).id AS target")
        assert "src(r) AS source" in result
        assert "dst(r) AS target" in result


# ============================================================================
# 6. 综合 / 边界场景
# ============================================================================


@pytest.mark.unit
class TestCombinedAndEdgeCases:
    """覆盖真实场景的组合查询。"""

    def test_real_world_call_tree_query(self, adapter: CypherToNgqlAdapter) -> None:
        """builtin.py trace_call_chain 的真实查询。"""
        cypher = (
            "MATCH (caller:CodeEntity)-[:CALLS*1..3]->(callee:CodeEntity) "
            "WHERE caller.id = $entity_id "
            "RETURN callee.id AS id, callee.name AS name, callee.entityType AS entity_type"
        )
        result = adapter.adapt(cypher)
        # caller.id → id(caller)
        assert "id(caller) == $entity_id" in result
        # callee.id → id(callee)
        assert "id(callee) AS id" in result
        # callee.name → callee.CodeEntity.name
        assert "callee.CodeEntity.name" in result
        # callee.entityType → callee.CodeEntity.entityType
        assert "callee.CodeEntity.entityType" in result

    def test_real_world_file_path_query(self, adapter: CypherToNgqlAdapter) -> None:
        """incremental_updater.py 的 filePath 查询。"""
        cypher = "MATCH (n {filePath: $fp}) RETURN n.id AS id"
        result = adapter.adapt(cypher)
        # 无 Tag 信息，无法做太多转换 — 不抛异常即可
        assert isinstance(result, str)
        assert len(result) > 0

    def test_real_world_graph_stats(self, adapter: CypherToNgqlAdapter) -> None:
        """graph.py graph_stats 的真实查询。"""
        cypher = "MATCH (n) WHERE size(labels(n)) > 0 RETURN labels(n)[0] AS label, count(*) AS count"
        result = adapter.adapt(cypher)
        assert "size(tags(n))" in result
        assert "tags(n)[0] AS label" in result

    def test_no_match_clause_passes_through(self, adapter: CypherToNgqlAdapter) -> None:
        result = adapter.adapt("RETURN 42")
        assert "42" in result

    def test_empty_string_passthrough(self, adapter: CypherToNgqlAdapter) -> None:
        assert adapter.adapt("") == ""

    def test_multi_label_match_takes_first_tag(self, adapter: CypherToNgqlAdapter) -> None:
        """多标签 ``(n:A:B)`` 取第一个 Tag。"""
        result = adapter.adapt("MATCH (n:CodeEntity:ConceptEntity) RETURN n.name")
        # 取第一个 Tag CodeEntity
        assert "n.CodeEntity.name" in result

    def test_id_in_inline_matching_handled_correctly(self, adapter: CypherToNgqlAdapter) -> None:
        """``(n:Tag {id: $val})`` 的 id 也要正确处理。"""
        cypher = "MATCH (n:CodeEntity {id: $id}) RETURN n.name AS name"
        result = adapter.adapt(cypher)
        # inline 的 id 应该作为 WHERE 条件，且用 id() 函数？不一定 — id() 是函数，不能写在 WHERE 左侧作为属性
        # 这里要求：至少不抛异常，且 inline pattern 被移除
        assert "(n:CodeEntity {id: $id})" not in result
        assert "(n:CodeEntity)" in result


# ============================================================================
# 7. _split_top_level helper
# ============================================================================


@pytest.mark.unit
class TestSplitTopLevel:
    """``_split_top_level`` 工具函数。"""

    def test_simple_split(self) -> None:
        assert _split_top_level("a, b, c", ",") == ["a", " b", " c"]

    def test_split_with_nested_braces(self) -> None:
        # 嵌套 map 中的逗号不应被切分
        result = _split_top_level("a: 1, b: {x: 1, y: 2}, c: 3", ",")
        assert len(result) == 3
        assert "x: 1, y: 2" in result[1]

    def test_split_with_nested_parens(self) -> None:
        result = _split_top_level("foo(a, b), bar(c, d)", ",")
        assert len(result) == 2


# ============================================================================
# 8. Reserved words
# ============================================================================


@pytest.mark.unit
class TestReservedWords:
    """保留字属性名加反引号。"""

    def test_reserved_word_property_gets_backticks(self, adapter: CypherToNgqlAdapter) -> None:
        """属性名是 nGQL 保留字（如 type, tag）时加反引号。"""
        # n:Tag 中 Tag 名是 CodeEntity，属性是 type（保留字）
        cypher = "MATCH (n:CodeEntity) RETURN n.type AS type"
        result = adapter.adapt(cypher)
        # n.type → n.CodeEntity.type → n.CodeEntity.`type`
        assert "`type`" in result

    def test_non_reserved_property_unchanged(self, adapter: CypherToNgqlAdapter) -> None:
        cypher = "MATCH (n:CodeEntity) RETURN n.name AS name"
        result = adapter.adapt(cypher)
        assert "`name`" not in result
