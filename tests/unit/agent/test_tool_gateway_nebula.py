"""测试 tool_gateway 拦截 NebulaGraph 写操作关键字。

V4 Phase 2: graph_query 工具按 graph_backend 接收 nGQL/Cypher，
但无论哪种语言，写操作都必须被拦截。
"""

from __future__ import annotations

from ontoagent.agent.tool_gateway import is_write_cypher, validate_graph_query


def test_blocked_nebula_insert_vertex() -> None:
    """INSERT VERTEX 必须被拦截。"""
    assert is_write_cypher("INSERT VERTEX CodeEntity(name) VALUES '1':('foo')") is True
    allowed, _ = validate_graph_query("INSERT VERTEX CodeEntity(name) VALUES '1':('foo')")
    assert allowed is False


def test_blocked_nebula_upsert_vertex() -> None:
    """UPSERT VERTEX 必须被拦截。"""
    assert is_write_cypher("UPSERT VERTEX ON CodeEntity SET name = 'foo'") is True
    allowed, _ = validate_graph_query("UPSERT VERTEX ON CodeEntity SET name = 'foo'")
    assert allowed is False


def test_blocked_nebula_delete_vertex() -> None:
    """DELETE VERTEX 必须被拦截。"""
    assert is_write_cypher("DELETE VERTEX '1'") is True
    allowed, _ = validate_graph_query("DELETE VERTEX '1'")
    assert allowed is False


def test_blocked_nebula_create_tag() -> None:
    """CREATE TAG 必须被拦截（schema 变更）。"""
    assert is_write_cypher("CREATE TAG CodeEntity(name STRING)") is True
    allowed, _ = validate_graph_query("CREATE TAG CodeEntity(name STRING)")
    assert allowed is False


def test_blocked_nebula_create_edge() -> None:
    """CREATE EDGE 必须被拦截（schema 变更）。"""
    assert is_write_cypher("CREATE EDGE CALLS()") is True
    allowed, _ = validate_graph_query("CREATE EDGE CALLS()")
    assert allowed is False


def test_blocked_nebula_drop_space() -> None:
    """DROP SPACE 必须被拦截（最危险）。"""
    assert is_write_cypher("DROP SPACE ontoagent") is True
    allowed, _ = validate_graph_query("DROP SPACE ontoagent")
    assert allowed is False


def test_blocked_nebula_submit_job() -> None:
    """SUBMIT JOB 必须被拦截。"""
    assert is_write_cypher("SUBMIT JOB COMPACT") is True
    allowed, _ = validate_graph_query("SUBMIT JOB COMPACT")
    assert allowed is False


def test_allowed_match_query_not_blocked() -> None:
    """只读 MATCH 查询不应被拦截（nGQL 同样使用 MATCH 关键字）。"""
    assert is_write_cypher("MATCH (n:CodeEntity) WHERE n.CodeEntity.name == 'foo' RETURN n") is False
    allowed, reason = validate_graph_query("MATCH (n:CodeEntity) RETURN n LIMIT 10")
    assert allowed is True
    assert reason == "ok"


def test_validate_graph_query_returns_backend_agnostic_message() -> None:
    """拦截消息应后端无关（不写死 Cypher）。"""
    _, reason = validate_graph_query("DELETE VERTEX '1'")
    assert "Cypher" not in reason
    assert "写操作" in reason


def test_validate_graph_query_accepts_positional_arg() -> None:
    """validate_graph_query 改名为 query 后仍接受位置参数（向后兼容）。"""
    allowed, _ = validate_graph_query("MATCH (n) RETURN n LIMIT 1")
    assert allowed is True
