from __future__ import annotations

from abc import ABC

import pytest

from ontoagent.store.graph_store import GraphStore


class _DummyStore(GraphStore):
    """用于测试的最小子类实现。"""

    def merge_node(self, label: str, properties: dict) -> dict:
        return properties  # pragma: no cover

    def get_node(self, node_id: str) -> dict | None:
        return None  # pragma: no cover

    def delete_node(self, node_id: str) -> bool:
        return False  # pragma: no cover

    def merge_relation(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict | None = None,
        *,
        source_label: str = "",
        target_label: str = "",
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

    def cleanup_orphan_nodes(self) -> int:
        return 0  # pragma: no cover

    def update_node_property(self, node_id: str, prop: str, value: object) -> bool:
        return True  # pragma: no cover


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
        "merge_node",
        "get_node",
        "delete_node",
        "merge_relation",
        "delete_relation",
        "get_relations",
        "query",
        "cleanup_orphan_nodes",
        "update_node_property",
    }
    actual = {name for name in dir(GraphStore) if not name.startswith("_")}
    assert expected.issubset(actual)


@pytest.mark.unit
class TestGraphStoreGetNodesByLabel:
    """get_nodes_by_label 基类默认实现 — 统一返回业务 id（n.id）而非内部节点 id（id(n)）。"""

    def _store_capturing_cypher(self, captured: list[str]) -> _DummyStore:
        store = _DummyStore()

        def _fake_query(cypher: str, params: dict | None = None) -> list[dict]:
            captured.append(cypher)
            return []

        store.query = _fake_query  # type: ignore[method-assign]
        return store

    def test_properties_with_id_returns_business_id_no_duplicate(self) -> None:
        captured: list[str] = []
        store = self._store_capturing_cypher(captured)

        store.get_nodes_by_label("RepositoryEntity", ["id", "name", "url", "status"])

        assert captured == [
            "MATCH (n:RepositoryEntity) RETURN n.id AS id, n.name AS name, n.url AS url, n.status AS status"
        ]
        assert "id(n)" not in captured[0]

    def test_properties_without_id_still_returns_business_id(self) -> None:
        captured: list[str] = []
        store = self._store_capturing_cypher(captured)

        store.get_nodes_by_label("CodeEntity", ["name"])

        assert captured == ["MATCH (n:CodeEntity) RETURN n.id AS id, n.name AS name"]

    def test_default_properties_read_id_and_name(self) -> None:
        captured: list[str] = []
        store = self._store_capturing_cypher(captured)

        store.get_nodes_by_label("X")

        assert captured == ["MATCH (n:X) RETURN n.id AS id, n.name AS name"]

    def test_duplicate_props_deduped(self) -> None:
        captured: list[str] = []
        store = self._store_capturing_cypher(captured)

        store.get_nodes_by_label("X", ["id", "id", "name"])

        assert captured == ["MATCH (n:X) RETURN n.id AS id, n.name AS name"]
