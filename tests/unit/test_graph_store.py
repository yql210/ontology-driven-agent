from __future__ import annotations

from abc import ABC

import pytest

from layerkg.graph_store import GraphStore


class _DummyStore(GraphStore):
    """用于测试的最小子类实现。"""

    def merge_node(self, label: str, properties: dict) -> dict:
        return properties  # pragma: no cover

    def get_node(self, node_id: str) -> dict | None:
        return None  # pragma: no cover

    def delete_node(self, node_id: str) -> bool:
        return False  # pragma: no cover

    def merge_relation(
        self, source_id: str, target_id: str, rel_type: str, properties: dict | None = None
    ) -> dict:
        return {"source": source_id, "target": target_id}  # pragma: no cover

    def delete_relation(self, source_id: str, target_id: str, rel_type: str) -> bool:
        return False  # pragma: no cover

    def get_relations(
        self, source_id: str | None = None, target_id: str | None = None, rel_type: str | None = None
    ) -> list[dict]:
        return []  # pragma: no cover

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        return []  # pragma: no cover


@pytest.mark.unit
def test_graph_store_is_abstract():
    """GraphStore 不能直接实例化。"""
    assert issubclass(GraphStore, ABC)
    with pytest.raises(TypeError):
        GraphStore()  # type: ignore[abstract]


@pytest.mark.unit
def test_graph_store_subclass_instantiable():
    """子类实现所有抽象方法后可以实例化。"""
    store = _DummyStore()
    assert isinstance(store, GraphStore)


@pytest.mark.unit
def test_graph_store_abstract_methods():
    """GraphStore 必须定义全部抽象方法。"""
    expected = {
        "merge_node", "get_node", "delete_node",
        "merge_relation", "delete_relation", "get_relations",
        "query",
    }
    actual = {name for name in dir(GraphStore) if not name.startswith("_")}
    assert expected.issubset(actual)
