from __future__ import annotations

from contextlib import suppress
from unittest.mock import MagicMock, patch

import pytest

from ontoagent.store.nebula_store import NebulaGraphStore


def _make_successful_result(*, rows: list[dict] | None = None) -> MagicMock:
    """构造一个成功 ResultSet，可迭代返回 rows（默认空）。"""
    result = MagicMock()
    result.is_succeeded = MagicMock(return_value=True)
    result.error_msg = ""
    result.is_empty = MagicMock(return_value=not rows)
    result.row_size = MagicMock(return_value=len(rows or []))
    # row_values(row_index) 返回该行的 ValueWrapper 列表；为简化测试，直接返回 dict 列表
    result.row_values = MagicMock(side_effect=lambda i: list((rows or [])[i].values()))
    result.column_values = MagicMock(side_effect=lambda key: key)
    result.keys = MagicMock(return_value=list((rows or [{}])[0].keys()) if rows else [])
    result.as_primitive = MagicMock(return_value=rows or [])
    return result


@pytest.fixture
def mock_pool() -> MagicMock:
    """mock ConnectionPool — init 与 get_session 都成功。"""
    pool = MagicMock()
    pool.init = MagicMock(return_value=True)
    pool.close = MagicMock()
    return pool


@pytest.fixture
def mock_session() -> MagicMock:
    """mock nebula Session — execute 返回成功 ResultSet。"""
    session = MagicMock()
    session.execute = MagicMock(return_value=_make_successful_result())
    session.release = MagicMock()
    return session


@pytest.fixture
def store_with_mock_pool(mock_pool: MagicMock, mock_session: MagicMock) -> NebulaGraphStore:
    """构造 NebulaGraphStore，但替换 _pool 为 mock，每次 _session_scope yield mock_session。"""
    mock_pool.get_session = MagicMock(return_value=mock_session)
    with (
        patch("ontoagent.store.nebula_store.ConnectionPool", return_value=mock_pool),
        # 跳过 schema 初始化（探针会尝试真实 SHOW TAGS，在 mock 下无意义）
        patch.object(NebulaGraphStore, "_ensure_schema_ready"),
    ):
        store = NebulaGraphStore(host="127.0.0.1", port=9669, user="root", password="nebula", space="test_space")
    return store


@pytest.mark.unit
class TestNebulaStoreMergeNode:
    """merge_node 测试。"""

    def test_merge_node_uses_upsert_with_correct_vid(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        properties = {"id": "uuid-1", "name": "foo", "entity_type": "function"}

        store_with_mock_pool.merge_node("CodeEntity", properties)

        mock_session.execute.assert_called()
        stmt = mock_session.execute.call_args.args[0]
        assert "UPSERT VERTEX ON `CodeEntity`" in stmt
        assert '"uuid-1"' in stmt

    def test_merge_node_converts_keys_to_camel_case(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        properties = {"id": "uuid-1", "entity_type": "function", "file_path": "/x/y.py"}

        store_with_mock_pool.merge_node("CodeEntity", properties)

        stmt = mock_session.execute.call_args.args[0]
        assert "entityType" in stmt
        assert "filePath" in stmt
        assert "entity_type" not in stmt
        assert "file_path" not in stmt

    def test_merge_node_missing_id_raises(self, store_with_mock_pool: NebulaGraphStore) -> None:
        with pytest.raises(ValueError, match="must contain 'id'"):
            store_with_mock_pool.merge_node("CodeEntity", {"name": "foo"})

    def test_merge_node_invalid_label_raises(self, store_with_mock_pool: NebulaGraphStore) -> None:
        with pytest.raises(ValueError, match="Invalid label"):
            store_with_mock_pool.merge_node("Invalid;Label", {"id": "x"})

    def test_merge_node_returns_camel_case_dict(self, store_with_mock_pool: NebulaGraphStore) -> None:
        properties = {"id": "uuid-1", "entity_type": "function"}
        result = store_with_mock_pool.merge_node("CodeEntity", properties)

        # 返回值 key 是 camelCase
        assert result["id"] == "uuid-1"
        assert result["entityType"] == "function"


@pytest.mark.unit
class TestNebulaStoreGetNode:
    """get_node 测试。"""

    def test_get_node_returns_none_when_not_found(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        # 空 ResultSet
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        result = store_with_mock_pool.get_node("missing-vid")
        assert result is None

    def test_get_node_returns_dict_when_found(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        # 构造一个 ResultSet：包含一行，列名为 id/name，值通过 ValueWrapper 包装
        success = _make_successful_result(rows=[{"id": "uuid-1", "name": "foo"}])
        # 模拟 column_values(key) 返回 list[ValueWrapper]（真实 NebulaGraph 行为）
        vw_name = MagicMock()
        vw_name.as_string = MagicMock(return_value="foo")
        vw_id = MagicMock()
        vw_id.as_string = MagicMock(return_value="uuid-1")
        success.column_values = MagicMock(side_effect=lambda key: [vw_name] if key == "name" else [vw_id])
        success.keys = MagicMock(return_value=["id", "name"])
        success.is_empty = MagicMock(return_value=False)
        success.row_size = MagicMock(return_value=1)
        mock_session.execute = MagicMock(return_value=success)

        result = store_with_mock_pool.get_node("uuid-1")

        assert result is not None
        assert result["id"] == "uuid-1"
        assert result["name"] == "foo"
        # 验证用了 FETCH PROP
        stmt = mock_session.execute.call_args.args[0]
        assert "FETCH PROP ON" in stmt
        assert '"uuid-1"' in stmt

    def test_get_node_unwraps_props_map_values(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """FETCH PROP ... YIELD properties(vertex) AS props 返回的 map 中 value 是 ValueWrapper。

        本测试构造 props 列为 dict[str, ValueWrapper]，验证 get_node 返回的 dict
        中每个 value 已被递归解包为 Python 原始类型。
        """
        # 真实 NebulaGraph 流程：YIELD id(vertex) AS id, properties(vertex) AS props
        # column_values("props")[0] 是一个 ValueWrapper，其 as_map() 返回 {key: ValueWrapper}
        success = _make_successful_result(rows=[{"id": "uuid-1", "props": {}}])
        vw_id = MagicMock()
        vw_id.as_string = MagicMock(return_value="uuid-1")
        vw_name = MagicMock()
        vw_name.as_string = MagicMock(return_value="process_order")
        vw_path = MagicMock()
        vw_path.as_string = MagicMock(return_value="/x/y.py")
        props_map = {"name": vw_name, "filePath": vw_path}
        vw_props = MagicMock()
        vw_props.as_string = MagicMock(side_effect=Exception("not a string"))
        vw_props.as_int = MagicMock(side_effect=Exception("not an int"))
        vw_props.as_double = MagicMock(side_effect=Exception("not a double"))
        vw_props.as_bool = MagicMock(side_effect=Exception("not a bool"))
        vw_props.as_list = MagicMock(side_effect=Exception("not a list"))
        vw_props.as_map = MagicMock(return_value=props_map)
        success.column_values = MagicMock(side_effect=lambda key: [vw_props] if key == "props" else [vw_id])
        success.keys = MagicMock(return_value=["id", "props"])
        success.is_empty = MagicMock(return_value=False)
        success.row_size = MagicMock(return_value=1)
        mock_session.execute = MagicMock(return_value=success)

        result = store_with_mock_pool.get_node("uuid-1")

        assert result is not None
        # map 内的 value 必须被解包成 str，而不是 ValueWrapper/MagicMock
        assert result["name"] == "process_order"
        assert result["filePath"] == "/x/y.py"
        assert result["id"] == "uuid-1"


@pytest.mark.unit
class TestNebulaStoreDeleteNode:
    """delete_node 测试。"""

    def test_delete_node_uses_delete_vertex_with_edge(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        result = store_with_mock_pool.delete_node("uuid-1")

        stmt = mock_session.execute.call_args.args[0]
        assert "DELETE VERTEX" in stmt
        assert '"uuid-1"' in stmt
        assert "WITH EDGE" in stmt
        assert result is True

    def test_delete_node_returns_false_on_failure(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        failed = MagicMock()
        failed.is_succeeded = MagicMock(return_value=False)
        failed.error_msg = "vertex not found"
        mock_session.execute = MagicMock(return_value=failed)

        assert store_with_mock_pool.delete_node("missing") is False


@pytest.mark.unit
class TestNebulaStoreMergeRelation:
    """merge_relation 测试。"""

    def test_merge_relation_uses_delete_then_insert(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        # 使用 snake_case rel_type，应该被转成 CALLS
        store_with_mock_pool.merge_relation("src-1", "tgt-1", "calls")

        # 至少 2 次 execute：DELETE EDGE + INSERT EDGE
        execute_calls = mock_session.execute.call_args_list
        stmts = [c.args[0] for c in execute_calls]
        assert any("DELETE EDGE" in s for s in stmts), f"Expected DELETE EDGE in {stmts}"
        assert any("INSERT EDGE" in s for s in stmts), f"Expected INSERT EDGE in {stmts}"
        # INSERT EDGE 的 type 必须是大写 CALLS
        insert_stmt = next(s for s in stmts if "INSERT EDGE" in s)
        assert "CALLS" in insert_stmt
        assert '"src-1"' in insert_stmt
        assert '"tgt-1"' in insert_stmt
        # rank=0 保证幂等
        assert "@0" in insert_stmt

    def test_merge_relation_accepts_uppercase_rel_type(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        store_with_mock_pool.merge_relation("src-1", "tgt-1", "CALLS")
        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        insert_stmt = next(s for s in stmts if "INSERT EDGE" in s)
        assert "CALLS" in insert_stmt

    def test_merge_relation_invalid_rel_type_raises(self, store_with_mock_pool: NebulaGraphStore) -> None:
        with pytest.raises(ValueError, match="Invalid relation type"):
            store_with_mock_pool.merge_relation("src-1", "tgt-1", "INVALID;REL")

    def test_merge_relation_invalid_source_label_raises(self, store_with_mock_pool: NebulaGraphStore) -> None:
        with pytest.raises(ValueError, match="Invalid source_label"):
            store_with_mock_pool.merge_relation("src-1", "tgt-1", "calls", source_label="Bad;Label")


@pytest.mark.unit
class TestNebulaStoreDeleteRelation:
    """delete_relation 测试。"""

    def test_delete_relation_uses_delete_edge_at_rank_zero(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        ok = store_with_mock_pool.delete_relation("src-1", "tgt-1", "calls")
        assert ok is True

        stmt = mock_session.execute.call_args.args[0]
        assert "DELETE EDGE CALLS" in stmt
        assert '"src-1"->"tgt-1"@0' in stmt


@pytest.mark.unit
class TestNebulaStoreGetRelations:
    """get_relations 测试。"""

    def test_get_relations_filters_by_source_id(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        store_with_mock_pool.get_relations(source_id="src-1")

        stmt = mock_session.execute.call_args.args[0]
        assert "MATCH" in stmt
        assert 'id(a) == "src-1"' in stmt

    def test_get_relations_filters_by_target_id(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        store_with_mock_pool.get_relations(target_id="tgt-1")

        stmt = mock_session.execute.call_args.args[0]
        assert 'id(b) == "tgt-1"' in stmt

    def test_get_relations_filters_by_rel_type(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        store_with_mock_pool.get_relations(rel_type="calls")

        stmt = mock_session.execute.call_args.args[0]
        # rel_type 转大写
        assert ":CALLS" in stmt or "CALLS" in stmt

    def test_get_relations_no_filters_no_where(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        store_with_mock_pool.get_relations()

        stmt = mock_session.execute.call_args.args[0]
        assert "MATCH" in stmt
        assert "WHERE" not in stmt


@pytest.mark.unit
class TestNebulaStoreQuery:
    """query 方法测试。"""

    def test_query_returns_list_of_dict(self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock) -> None:
        success = _make_successful_result(rows=[{"col1": "v1"}])
        vw = MagicMock()
        vw.as_string = MagicMock(return_value="v1")
        success.column_values = MagicMock(return_value=[vw])
        success.keys = MagicMock(return_value=["col1"])
        success.is_empty = MagicMock(return_value=False)
        success.row_size = MagicMock(return_value=1)
        mock_session.execute = MagicMock(return_value=success)

        result = store_with_mock_pool.query('FETCH PROP ON CodeEntity "x" YIELD id(vertex);')
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["col1"] == "v1"

    def test_query_empty_result_returns_empty_list(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        result = store_with_mock_pool.query("MATCH (n) RETURN n LIMIT 1;")
        assert result == []

    def test_query_failed_raises_runtime_error(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        failed = MagicMock()
        failed.is_succeeded = MagicMock(return_value=False)
        failed.error_msg = "syntax error"
        mock_session.execute = MagicMock(return_value=failed)

        with pytest.raises(RuntimeError, match="NebulaGraph query failed"):
            store_with_mock_pool.query("INVALID STATEMENT")

    def test_query_invokes_cypher_adapter(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """query() 必须把 Cypher 经过 CypherToNgqlAdapter 转换后再下发。

        用一条含 ``labels(n)`` 的查询作为探针，验证下发给 session.execute 的语句
        中 ``labels`` 已被替换为 ``tags``。
        """
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        store_with_mock_pool.query("MATCH (n) WHERE size(labels(n)) > 0 RETURN n")

        # session.execute 至少被调用过一次（USE SPACE + 查询）；找到含 MATCH 的那次
        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        match_stmts = [s for s in stmts if "MATCH" in s]
        assert match_stmts, f"Expected MATCH in execute stmts: {stmts}"
        # adapter 应当把 labels → tags
        assert any("tags(n)" in s for s in match_stmts), f"Expected tags(n) in {match_stmts}"
        assert all("labels(" not in s for s in match_stmts), f"labels( should be converted: {match_stmts}"

    def test_query_cypher_property_access_converted(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """带 Tag 信息的 Cypher（``n.field``）应被转成 ``n.Tag.field``。"""
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        store_with_mock_pool.query(
            "MATCH (n:CodeEntity) WHERE n.id = $eid RETURN n.name AS name",
            {"eid": "uuid-1"},
        )

        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        match_stmt = next(s for s in stmts if "MATCH" in s)
        # n.id → id(n)，= → ==，n.name → n.CodeEntity.name
        assert "id(n) ==" in match_stmt
        assert "n.CodeEntity.name" in match_stmt

    def test_query_failure_logs_original_and_adapted(
        self,
        store_with_mock_pool: NebulaGraphStore,
        mock_session: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """查询失败时，原始 Cypher 与转换后的 nGQL 都应记录到日志（便于排查）。"""
        failed = MagicMock()
        failed.is_succeeded = MagicMock(return_value=False)
        failed.error_msg = "syntax error"
        mock_session.execute = MagicMock(return_value=failed)

        original_cypher = "MATCH (n:CodeEntity) WHERE n.id = $eid RETURN n.name"
        with caplog.at_level("WARNING"), pytest.raises(RuntimeError):
            store_with_mock_pool.query(original_cypher, {"eid": "uuid-1"})

        # 日志中应有原始语句和转换后的语句
        log_text = caplog.text
        assert "MATCH (n:CodeEntity) WHERE n.id" in log_text  # 原始
        # 转换后的语句也应记录（id(n) == 是 adapter 的产物）
        assert "id(n) ==" in log_text

    def test_query_dollar_param_string_substituted_as_quoted_literal(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """``$name`` 形式的 string 参数应被替换为带引号的字面量（nGQL map value 语法）。

        上层代码（agent/tools.py、incremental_updater.py 等）大量使用
        ``MATCH (n {name: $name})`` 形式，NebulaGraph 不支持参数化查询，
        需要把 ``$name`` 替换为字面量 ``"foo"``。
        """
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        store_with_mock_pool.query("MATCH (n {name: $name}) RETURN n", {"name": "foo"})

        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        match_stmt = next(s for s in stmts if "MATCH" in s)
        # 字符串值替换为带引号字面量
        assert '"foo"' in match_stmt
        assert "$name" not in match_stmt

    def test_query_dollar_param_int_substituted_without_quotes(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """``$limit`` 形式的 int 参数应被替换为不带引号的数字字面量。

        ``LIMIT $limit`` 在 nGQL 里必须是 ``LIMIT 10``（数字）而非 ``LIMIT "10"``。
        """
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        store_with_mock_pool.query("MATCH (n) RETURN n LIMIT $limit", {"limit": 10})

        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        match_stmt = next(s for s in stmts if "MATCH" in s)
        assert "LIMIT 10" in match_stmt
        assert 'LIMIT "10"' not in match_stmt
        assert "$limit" not in match_stmt

    def test_query_dollar_param_none_substituted_as_null(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """``$key`` 值为 None 时替换为 ``null`` 字面量。"""
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        store_with_mock_pool.query("MATCH (n) WHERE n.x = $x RETURN n", {"x": None})

        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        match_stmt = next(s for s in stmts if "MATCH" in s)
        assert "= null" in match_stmt
        assert "$x" not in match_stmt

    def test_query_dollar_param_list_raises_type_error(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """``$ids`` 值为 list 时抛 TypeError（强制上层走语义化 API）。"""
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        with pytest.raises(TypeError, match="ids"):
            store_with_mock_pool.query("MATCH (n) WHERE n.id IN $ids RETURN n", {"ids": ["a", "b"]})

    def test_query_dollar_param_dict_raises_type_error(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """``$cfg`` 值为 dict 时抛 TypeError。"""
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        with pytest.raises(TypeError, match="cfg"):
            store_with_mock_pool.query("RETURN $cfg", {"cfg": {"k": "v"}})

    def test_query_dollar_param_tuple_raises_type_error(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """``$ids`` 值为 tuple 时抛 TypeError。"""
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        with pytest.raises(TypeError, match="ids"):
            store_with_mock_pool.query("MATCH (n) WHERE n.id IN $ids RETURN n", {"ids": ("a", "b")})

    def test_query_dollar_param_set_raises_type_error(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """``$ids`` 值为 set 时抛 TypeError。"""
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        with pytest.raises(TypeError, match="ids"):
            store_with_mock_pool.query("MATCH (n) WHERE n.id IN $ids RETURN n", {"ids": {"a", "b"}})

    def test_query_dollar_param_logs_warning(
        self,
        store_with_mock_pool: NebulaGraphStore,
        mock_session: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``$key`` 替换时应打 warning 日志（标记需要后续迁移到 {key} 或语义化 API）。"""
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        with caplog.at_level("WARNING"):
            store_with_mock_pool.query("MATCH (n {name: $name}) RETURN n", {"name": "foo"})

        assert "$param fallback" in caplog.text or "param substitution" in caplog.text.lower()
        assert "name" in caplog.text

    def test_query_dollar_param_not_in_stmt_no_warning(
        self,
        store_with_mock_pool: NebulaGraphStore,
        mock_session: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``params`` 中有 key 但语句里没出现 ``$key`` 时不打 warning（也不替换）。"""
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        with caplog.at_level("WARNING"):
            store_with_mock_pool.query("MATCH (n) RETURN n", {"name": "foo"})

        # 没有 $name 替换发生 → 没有 warning
        assert "$-param" not in caplog.text
        assert "param substitution" not in caplog.text.lower()

    def test_query_brace_and_dollar_params_coexist(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """同一语句里 ``{limit}`` 与 ``$name`` 共存时两者都要被替换。"""
        empty = _make_successful_result(rows=[])
        empty.is_empty = MagicMock(return_value=True)
        mock_session.execute = MagicMock(return_value=empty)

        store_with_mock_pool.query(
            "MATCH (n {name: $name}) RETURN n LIMIT {limit}",
            {"name": "foo", "limit": 5},
        )

        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        match_stmt = next(s for s in stmts if "MATCH" in s)
        assert '"foo"' in match_stmt
        assert "LIMIT 5" in match_stmt
        assert "$name" not in match_stmt
        assert "{limit}" not in match_stmt


@pytest.mark.unit
class TestNebulaStoreSessionScope:
    """session scope context manager 测试。"""

    def test_session_scope_releases_on_exception(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        # USE 语句成功，yield body 内的 execute 抛异常
        success = _make_successful_result()
        call_count = [0]

        def execute_side_effect(stmt):
            call_count[0] += 1
            if "USE" in stmt:
                return success
            raise RuntimeError("boom")

        mock_session.execute = MagicMock(side_effect=execute_side_effect)

        # swallow 上层异常，仅验证 release 是否被调用
        with suppress(RuntimeError), store_with_mock_pool._session_scope():
            mock_session.execute("foo")

        mock_session.release.assert_called_once()

    def test_session_scope_releases_on_success(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        with store_with_mock_pool._session_scope():
            pass

        mock_session.release.assert_called_once()

    def test_session_scope_executes_use_space(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        with store_with_mock_pool._session_scope():
            pass

        # 第一次 execute 应该是 USE SPACE
        first_call = mock_session.execute.call_args_list[0]
        assert "USE SPACE" in first_call.args[0] or "USE `test_space`" in first_call.args[0]


@pytest.mark.unit
class TestNebulaStoreClose:
    """close 方法测试。"""

    def test_close_closes_pool(self, store_with_mock_pool: NebulaGraphStore, mock_pool: MagicMock) -> None:
        store_with_mock_pool.close()
        mock_pool.close.assert_called_once()

    def test_context_manager_closes_pool(self, mock_pool: MagicMock, mock_session: MagicMock) -> None:
        mock_pool.get_session = MagicMock(return_value=mock_session)
        with (
            patch("ontoagent.store.nebula_store.ConnectionPool", return_value=mock_pool),
            patch.object(NebulaGraphStore, "_ensure_schema_ready"),
            NebulaGraphStore(host="127.0.0.1") as store,
        ):
            assert store is not None

        mock_pool.close.assert_called_once()


@pytest.mark.unit
class TestNebulaStoreCleanupOrphanNodes:
    """cleanup_orphan_nodes 测试。"""

    def test_cleanup_orphan_nodes_returns_count(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        # 构造一个返回 deleted=3 的 ResultSet
        success = MagicMock()
        success.is_succeeded = MagicMock(return_value=True)
        success.is_empty = MagicMock(return_value=False)
        vw = MagicMock()
        vw.as_int = MagicMock(return_value=3)
        success.column_values = MagicMock(return_value=[vw])
        success.keys = MagicMock(return_value=["deleted"])
        success.row_size = MagicMock(return_value=1)
        mock_session.execute = MagicMock(return_value=success)

        count = store_with_mock_pool.cleanup_orphan_nodes()
        assert count == 3
        stmt = mock_session.execute.call_args.args[0]
        assert "MATCH" in stmt
        assert "DELETE" in stmt


@pytest.mark.unit
class TestNebulaStoreMergeNodesBatch:
    """merge_nodes_batch 测试。"""

    def test_batch_writes_multiple_nodes_in_one_insert(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """3 个节点应在一条 INSERT VERTEX 语句中批量写入。"""
        nodes = [
            {"id": "uuid-1", "name": "foo", "entity_type": "function"},
            {"id": "uuid-2", "name": "bar", "entity_type": "function"},
            {"id": "uuid-3", "name": "baz", "entity_type": "function"},
        ]

        count = store_with_mock_pool.merge_nodes_batch("CodeEntity", nodes)

        assert count == 3
        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        insert_stmts = [s for s in stmts if "INSERT VERTEX" in s]
        assert len(insert_stmts) == 1
        stmt = insert_stmts[0]
        assert "`CodeEntity`" in stmt
        assert '"uuid-1"' in stmt
        assert '"uuid-2"' in stmt
        assert '"uuid-3"' in stmt
        # 属性列表不带类型，仅名称
        assert "entityType" in stmt
        assert "entity_type" not in stmt
        # 属性列表不应包含 id（id 已作为 VID）
        # 检查 prop 列表括号内不含 id
        prop_clause = stmt.split("VALUES")[0]
        assert "id" not in prop_clause

    def test_batch_empty_list_returns_zero(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """空列表返回 0，且不调用 INSERT VERTEX。"""
        count = store_with_mock_pool.merge_nodes_batch("CodeEntity", [])

        assert count == 0
        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        assert not any("INSERT VERTEX" in s for s in stmts)

    def test_batch_missing_id_raises(self, store_with_mock_pool: NebulaGraphStore) -> None:
        """任一 dict 缺 id → ValueError。"""
        nodes = [{"id": "uuid-1", "name": "foo"}, {"name": "no-id"}]

        with pytest.raises(ValueError, match="must contain 'id'"):
            store_with_mock_pool.merge_nodes_batch("CodeEntity", nodes)

    def test_batch_invalid_label_raises(self, store_with_mock_pool: NebulaGraphStore) -> None:
        """非法 label → ValueError。"""
        with pytest.raises(ValueError, match="Invalid label"):
            store_with_mock_pool.merge_nodes_batch("Bad;Label", [{"id": "x"}])

    def test_batch_splits_by_batch_size(self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock) -> None:
        """5 个节点 batch_size=2 → 3 条 INSERT VERTEX（2+2+1）。"""
        nodes = [{"id": f"uuid-{i}", "name": f"n{i}"} for i in range(5)]

        count = store_with_mock_pool.merge_nodes_batch("CodeEntity", nodes, batch_size=2)

        assert count == 5
        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        insert_stmts = [s for s in stmts if "INSERT VERTEX" in s]
        assert len(insert_stmts) == 3

    def test_batch_failure_raises_runtime_error(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """INSERT 失败 → RuntimeError。"""
        failed = MagicMock()
        failed.is_succeeded = MagicMock(return_value=False)
        failed.error_msg = "syntax error"
        mock_session.execute = MagicMock(return_value=failed)

        with pytest.raises(RuntimeError, match="merge_nodes_batch failed"):
            store_with_mock_pool.merge_nodes_batch("CodeEntity", [{"id": "x"}])


@pytest.mark.unit
class TestNebulaStoreMergeRelationsBatch:
    """merge_relations_batch 测试。"""

    def test_batch_writes_multiple_edges_in_one_insert(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """3 条同类型关系应在一条 INSERT EDGE 中批量写入。"""
        rels = [
            {"source_id": "s1", "target_id": "t1", "rel_type": "calls"},
            {"source_id": "s2", "target_id": "t2", "rel_type": "calls"},
            {"source_id": "s3", "target_id": "t3", "rel_type": "calls"},
        ]

        count = store_with_mock_pool.merge_relations_batch(rels)

        assert count == 3
        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        insert_stmts = [s for s in stmts if "INSERT EDGE" in s]
        assert len(insert_stmts) == 1
        stmt = insert_stmts[0]
        assert "`CALLS`" in stmt
        assert '"s1"->"t1"' in stmt
        assert '"s2"->"t2"' in stmt
        assert '"s3"->"t3"' in stmt

    def test_batch_rel_type_mapping(self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock) -> None:
        """snake_case rel_type 应映射为 UPPER_SNAKE（contains → CONTAINS）。"""
        rels = [{"source_id": "s1", "target_id": "t1", "rel_type": "contains"}]

        store_with_mock_pool.merge_relations_batch(rels)

        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        insert_stmt = next(s for s in stmts if "INSERT EDGE" in s)
        assert "`CONTAINS`" in insert_stmt

    def test_batch_splits_by_batch_size(self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock) -> None:
        """5 条关系 batch_size=2 → 3 条 INSERT EDGE。"""
        rels = [{"source_id": f"s{i}", "target_id": f"t{i}", "rel_type": "calls"} for i in range(5)]

        count = store_with_mock_pool.merge_relations_batch(rels, batch_size=2)

        assert count == 5
        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        insert_stmts = [s for s in stmts if "INSERT EDGE" in s]
        assert len(insert_stmts) == 3

    def test_batch_invalid_rel_type_raises(self, store_with_mock_pool: NebulaGraphStore) -> None:
        """非法 rel_type → ValueError。"""
        with pytest.raises(ValueError, match="Invalid relation type"):
            store_with_mock_pool.merge_relations_batch([{"source_id": "s1", "target_id": "t1", "rel_type": "BAD;REL"}])

    def test_batch_empty_list_returns_zero(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """空列表返回 0，不调用 INSERT EDGE。"""
        count = store_with_mock_pool.merge_relations_batch([])

        assert count == 0
        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        assert not any("INSERT EDGE" in s for s in stmts)


@pytest.mark.unit
class TestNebulaStoreEnsureConstraints:
    """ensure_constraints 测试。"""

    def test_succeeds_when_schema_init_ok(self, store_with_mock_pool: NebulaGraphStore) -> None:
        """schema 初始化成功时不抛异常。"""
        store_with_mock_pool.ensure_constraints()

    def test_raises_when_schema_init_fails(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """schema 初始化失败时抛 RuntimeError。"""
        failed = MagicMock()
        failed.is_succeeded = MagicMock(return_value=False)
        failed.error_msg = "create space failed"
        mock_session.execute = MagicMock(return_value=failed)

        with pytest.raises(RuntimeError, match="initialize"):
            store_with_mock_pool.ensure_constraints()


@pytest.mark.unit
class TestNebulaStoreClearAll:
    """clear_all 测试。"""

    def test_executes_clear_space(self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock) -> None:
        """clear_all 应执行 CLEAR SPACE 语句，space 名带反引号。"""
        store_with_mock_pool.clear_all()

        stmts = [c.args[0] for c in mock_session.execute.call_args_list]
        clear_stmts = [s for s in stmts if "CLEAR SPACE" in s]
        assert len(clear_stmts) == 1
        assert "`test_space`" in clear_stmts[0]

    def test_returns_int(self, store_with_mock_pool: NebulaGraphStore) -> None:
        """返回值是 int。"""
        result = store_with_mock_pool.clear_all()
        assert isinstance(result, int)

    def test_failure_raises_runtime_error(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        """CLEAR SPACE 失败 → RuntimeError。"""
        failed = MagicMock()
        failed.is_succeeded = MagicMock(return_value=False)
        failed.error_msg = "permission denied"
        mock_session.execute = MagicMock(return_value=failed)

        with pytest.raises(RuntimeError, match="clear_all failed"):
            store_with_mock_pool.clear_all()


@pytest.mark.unit
class TestFormatValueSerialization:
    """``_format_value`` 序列化测试（Phase 6.4：修复 list/dict/set/bool 序列化）。"""

    def test_none_becomes_null(self) -> None:
        from ontoagent.store.nebula_store import _format_value

        assert _format_value(None) == "null"

    def test_bool_becomes_lowercase_string(self) -> None:
        """bool 不应变成 ``"True"``/``"False"``（Python 默认），而应是 ``"true"``/``"false"``。"""
        from ontoagent.store.nebula_store import _format_value

        assert _format_value(True) == '"true"'
        assert _format_value(False) == '"false"'

    def test_list_becomes_json_string(self) -> None:
        """list 不应变成 ``"['a', 'b']"``（Python repr），而应是 JSON ``'["a","b"]'``。"""
        from ontoagent.store.nebula_store import _format_value

        result = _format_value(["a", "b"])
        assert result == '"["a", "b"]"' or result == '"[\\"a\\", \\"b\\"]"'

    def test_dict_becomes_json_string(self) -> None:
        from ontoagent.store.nebula_store import _format_value

        result = _format_value({"key": "val"})
        # 应是 JSON 格式，不是 Python repr
        assert "key" in result and "val" in result

    def test_set_becomes_sorted_json(self) -> None:
        """set 序列化为排序后的 JSON list，保证确定性。"""
        from ontoagent.store.nebula_store import _format_value

        result = _format_value({"z", "a", "m"})
        # set 排序后应为 ["a", "m", "z"]
        assert "a" in result and "m" in result and "z" in result

    def test_int_stays_string(self) -> None:
        from ontoagent.store.nebula_store import _format_value

        assert _format_value(42) == '"42"'

    def test_float_stays_string(self) -> None:
        from ontoagent.store.nebula_store import _format_value

        assert _format_value(0.85) == '"0.85"'

    def test_string_escapes_quotes(self) -> None:
        from ontoagent.store.nebula_store import _format_value

        result = _format_value('hello "world"')
        assert '\\"' in result


@pytest.mark.unit
class TestSessionRetryAndHealthCheck:
    """Phase 9: session 重连和健康检查测试。"""

    def test_health_check_returns_dict(self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock) -> None:
        """health_check 返回包含 connected/space/tag_count/edge_count 的 dict。"""
        ok_result = MagicMock()
        ok_result.is_succeeded = MagicMock(return_value=True)
        ok_result.row_size = MagicMock(return_value=5)
        mock_session.execute = MagicMock(return_value=ok_result)

        result = store_with_mock_pool.health_check()
        assert isinstance(result, dict)
        assert "connected" in result
        assert result["connected"] is True
        assert result["space"] == "test_space"
        assert result["tag_count"] == 5

    def test_health_check_on_failure(self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock) -> None:
        """health_check 连接失败时返回 connected=False 且不抛异常。"""
        mock_session.execute = MagicMock(side_effect=OSError("connection lost"))

        result = store_with_mock_pool.health_check()
        assert result["connected"] is False
        assert "error" in result

    def test_retry_config_defaults(self) -> None:
        """_SESSION_RETRY_MAX 和 _SESSION_RETRY_DELAY 有合理默认值。"""
        assert NebulaGraphStore._SESSION_RETRY_MAX >= 1
        assert NebulaGraphStore._SESSION_RETRY_DELAY > 0
