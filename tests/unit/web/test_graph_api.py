"""Tests for graph API router backend compatibility (Neo4j / NebulaGraph).

覆盖 P3-1：全图模式的类型筛选改为白名单校验 + 字面量拼接，get_node_detail 使用后端兼容 label 表达式。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from ontoagent.api.web import app as app_module


def _query_spy_store(
    class_name: str, *, reject_list_params: bool = False
) -> tuple[object, list[tuple[str, dict | None]]]:
    """Create a store instance with a query spy.

    - ``class_name`` 控制 ``type(store).__name__``（决定 labels()/tags() 后端）。
    - ``reject_list_params`` 模拟 NebulaGraph：query 对 list/dict 参数抛 TypeError。
    """
    calls: list[tuple[str, dict | None]] = []

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        if reject_list_params:
            for value in (params or {}).values():
                if isinstance(value, (list, dict)):
                    raise TypeError("NebulaGraph does not support list/dict params")
        calls.append((cypher, params))
        return []

    return type(class_name, (), {"query": query})(), calls


@pytest.fixture
def make_client():
    """Create a TestClient with the given store injected as app.state.graph_store.

    不走 lifespan（避免触发真实 Neo4j 连接），手动注入 mock graph_store。
    """

    def _make(store) -> TestClient:
        app = app_module.create_app()
        app.state.graph_store = store
        app.state.build_tasks = {}
        app.state.build_asyncio_tasks = {}
        return TestClient(app)

    return _make


@pytest.mark.unit
def test_neo4j_full_graph_type_filter(make_client):
    """neo4j 全图模式 + 类型筛选：label 用 labels(n)[0]、类型筛选为字面量、不再传 $types。"""
    store = MagicMock()
    store.query.return_value = []
    client = make_client(store)

    resp = client.get("/api/graph?type=CodeEntity,ConceptEntity")

    assert resp.status_code == 200
    cypher, params = store.query.call_args.args
    assert 'labels(n)[0] IN ["CodeEntity", "ConceptEntity"]' in cypher
    assert "$types" not in cypher
    assert "types" not in params


@pytest.mark.unit
def test_nebula_full_graph_type_filter(make_client):
    """nebula 全图模式：tags(n)[0] + 字面量类型筛选，且不传 list 参数（不抛 TypeError）。"""
    store, calls = _query_spy_store("NebulaGraphStore", reject_list_params=True)
    client = make_client(store)

    resp = client.get("/api/graph?type=CodeEntity")

    assert resp.status_code == 200
    assert calls, "query should have been called"
    cypher, params = calls[0]
    assert "tags(n)[0]" in cypher
    assert 'tags(n)[0] IN ["CodeEntity"]' in cypher
    assert "$types" not in cypher
    assert "types" not in params


@pytest.mark.unit
def test_injection_attempt_type_filtered(make_client):
    """注入尝试：非法 type 值被白名单过滤，不进入查询串。"""
    store = MagicMock()
    store.query.return_value = []
    client = make_client(store)

    resp = client.get("/api/graph?type=CodeEntity%22%20OR%201%3D1")

    assert resp.status_code == 200
    cypher, params = store.query.call_args.args
    assert "OR 1=1" not in cypher
    assert '"CodeEntity" OR 1=1' not in cypher
    assert "IN [" not in cypher
    assert "types" not in params


@pytest.mark.unit
@pytest.mark.parametrize(
    ("class_name", "label_expr"),
    [("NebulaGraphStore", "tags(n)[0]"), ("Neo4jGraphStore", "labels(n)[0]")],
)
def test_get_node_detail_label_expr(make_client, class_name, label_expr):
    """get_node_detail 使用后端兼容 label 表达式。"""
    store, calls = _query_spy_store(class_name)
    client = make_client(store)

    resp = client.get("/api/graph/node/abc")

    assert resp.status_code == 404  # node 查询返回空 → 404
    assert calls, "query should have been called"
    cypher, _ = calls[0]
    assert label_expr in cypher


@pytest.mark.unit
def test_full_graph_no_type_filter(make_client):
    """全图模式不带 type：无类型筛选条件（不拼接 IN [...]）。"""
    store = MagicMock()
    store.query.return_value = []
    client = make_client(store)

    resp = client.get("/api/graph")

    assert resp.status_code == 200
    cypher, params = store.query.call_args.args
    assert "IN [" not in cypher
    assert "types" not in params


@pytest.mark.unit
@pytest.mark.parametrize(
    ("class_name", "label_fn"),
    [("Neo4jGraphStore", "labels"), ("NebulaGraphStore", "tags")],
)
def test_center_mode_uses_label_expr(make_client, class_name, label_fn):
    """中心模式回归：邻居与中心节点查询均用后端兼容 label 表达式。"""
    store, calls = _query_spy_store(class_name)
    client = make_client(store)

    resp = client.get("/api/graph?center=foo")

    assert resp.status_code == 200
    assert len(calls) >= 2  # neighbor 查询 + center 查询
    for cypher, _ in calls[:2]:
        assert f"{label_fn}(" in cypher
