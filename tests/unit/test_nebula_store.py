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
    with patch("ontoagent.store.nebula_store.ConnectionPool", return_value=mock_pool):
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
        assert "UPSERT VERTEX ON CodeEntity" in stmt
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
        # 模拟 column_values(key) 返回 ValueWrapper，as_string() 取真实值
        vw = MagicMock()
        vw.as_string = MagicMock(return_value="foo")
        vw_list = MagicMock()
        vw_list.as_string = MagicMock(return_value="uuid-1")
        success.column_values = MagicMock(side_effect=lambda key: vw if key == "name" else vw_list)
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
        success.column_values = MagicMock(return_value=vw)
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


@pytest.mark.unit
class TestNebulaStoreSessionScope:
    """session scope context manager 测试。"""

    def test_session_scope_releases_on_exception(
        self, store_with_mock_pool: NebulaGraphStore, mock_session: MagicMock
    ) -> None:
        # 故意让 execute 抛异常
        mock_session.execute = MagicMock(side_effect=RuntimeError("boom"))

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
        success.column_values = MagicMock(return_value=vw)
        success.keys = MagicMock(return_value=["deleted"])
        success.row_size = MagicMock(return_value=1)
        mock_session.execute = MagicMock(return_value=success)

        count = store_with_mock_pool.cleanup_orphan_nodes()
        assert count == 3
        stmt = mock_session.execute.call_args.args[0]
        assert "MATCH" in stmt
        assert "DELETE" in stmt
