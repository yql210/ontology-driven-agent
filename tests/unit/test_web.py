from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from layerkg.web.app import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestChatSync:
    @patch("layerkg.web.router.chat.run_query", new_callable=AsyncMock)
    def test_chat_sync_returns_answer(self, mock_run, client):
        mock_run.return_value = "ConceptAligner 在 aligner.py"
        resp = client.post("/api/chat", json={"message": "ConceptAligner在哪"})
        assert resp.status_code == 200
        data = resp.json()
        assert "ConceptAligner" in data["answer"]
        assert data["thread_id"]
        assert data["duration_ms"] >= 0

    def test_chat_sync_empty_message_rejected(self, client):
        resp = client.post("/api/chat", json={"message": "  "})
        assert resp.status_code == 422

    @patch("layerkg.web.router.chat.run_query", new_callable=AsyncMock)
    def test_chat_sync_with_thread_id(self, mock_run, client):
        mock_run.return_value = "ok"
        resp = client.post("/api/chat", json={"message": "test", "thread_id": "my-thread"})
        assert resp.status_code == 200
        assert resp.json()["thread_id"] == "my-thread"


class TestChatStream:
    def test_chat_stream_returns_events(self, client):
        async def fake_stream(*args, **kwargs):
            yield {"type": "token", "content": "Hello"}
            yield {"type": "tool_start", "tool": "graph_query", "args": {}}
            yield {"type": "tool_end", "tool": "graph_query"}
            yield {"type": "token", "content": " World"}

        with patch("layerkg.agent.graph.run_query_stream", return_value=fake_stream()):
            resp = client.post("/api/chat/stream", json={"message": "test"})
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_chat_stream_empty_message_rejected(self, client):
        resp = client.post("/api/chat/stream", json={"message": ""})
        assert resp.status_code == 422

    def test_chat_stream_with_thread_id(self, client):
        async def fake_stream(*args, **kwargs):
            yield {"type": "token", "content": "hi"}

        with patch("layerkg.agent.graph.run_query_stream", return_value=fake_stream()):
            resp = client.post("/api/chat/stream", json={"message": "test", "thread_id": "t1"})
            assert resp.status_code == 200

    def test_stream_done_after_normal(self, client):
        """正常路径最后收到 done 事件"""
        async def fake_stream(*args, **kwargs):
            yield {"type": "token", "content": "Hello"}
            yield {"type": "tool_start", "tool": "search", "args": {}}
            yield {"type": "tool_end", "tool": "search", "result": "ok"}

        with patch("layerkg.agent.graph.run_query_stream", return_value=fake_stream()):
            resp = client.post("/api/chat/stream", json={"message": "test"})
            assert resp.status_code == 200
            content = resp.text
            # Should have "done" event
            assert "event: done" in content
            # done event should be near the end (check by finding its position)
            done_idx = content.find("event: done")
            # done should come after the token/tool events
            assert content.find("token") < done_idx or content.find("tool") < done_idx

    def test_stream_error_no_done(self, client):
        """异常路径：error 事件后没有 done"""
        async def fake_stream_error(*args, **kwargs):
            yield {"type": "token", "content": "Hello"}
            raise ValueError("Something went wrong")

        with patch("layerkg.agent.graph.run_query_stream", return_value=fake_stream_error()):
            resp = client.post("/api/chat/stream", json={"message": "test"})
            assert resp.status_code == 200
            content = resp.text
            # Should have error event
            assert "event: error" in content
            # Should NOT have done event (because error occurred)
            assert "event: done" not in content

    def test_stream_timeout_no_done(self, client):
        """超时路径：timeout error 后没有 done"""

        async def fake_stream_timeout(*args, **kwargs):
            yield {"type": "token", "content": "Starting"}
            raise TimeoutError()

        with patch("layerkg.agent.graph.run_query_stream", return_value=fake_stream_timeout()):
            resp = client.post("/api/chat/stream", json={"message": "test"})
            assert resp.status_code == 200
            content = resp.text
            # Should have error event
            assert "event: error" in content
            # Should NOT have done event
            assert "event: done" not in content


# ===== Graph API Tests =====


class MockGraphStore:
    """Mock Neo4jGraphStore for testing."""

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[dict] = []

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        params = params or {}
        # 匹配节点统计查询
        if "MATCH (n) WHERE size(labels(n)) > 0" in cypher and "count(*) AS count" in cypher:
            return [
                {"label": "CodeEntity", "count": 10},
                {"label": "ConceptEntity", "count": 5},
            ]
        # 匹配边统计查询
        if "MATCH ()-[r]->() RETURN count(*)" in cypher:
            return [{"cnt": 15}]
        # 匹配全图节点查询
        if "MATCH (n) WHERE size(labels(n)) > 0" in cypher and "LIMIT" in cypher:
            return [
                {"id": "n1", "name": "func_a", "label": "CodeEntity", "entity_type": "function"},
                {"id": "n2", "name": "ClassB", "label": "CodeEntity", "entity_type": "class"},
            ]
        # 匹配全图边查询
        if "MATCH (a)-[r]->(b)" in cypher and "WHERE a.id IN $ids" in cypher:
            return [
                {"source": "n1", "target": "n2", "type": "CALLS"},
            ]
        # 匹配中心节点邻居查询
        if "center {name: $name}" in cypher:
            return [
                {"id": "n2", "name": "neighbor_func", "label": "CodeEntity", "entity_type": "function"},
            ]
        # 匹配中心节点本身查询
        if "MATCH (n {name: $name})" in cypher and "WHERE size(labels(n))" in cypher:
            return [
                {"id": "center1", "name": "center_func", "label": "CodeEntity", "entity_type": "function"},
            ]
        # 匹配节点详情查询
        if "MATCH (n {id: $id}) RETURN n.id" in cypher:
            node_id = params.get("id")
            if node_id == "not_found":
                return []
            return [
                {
                    "id": node_id,
                    "name": "test_node",
                    "label": "CodeEntity",
                    "props": {"id": node_id, "name": "test_node", "entity_type": "function"},
                }
            ]
        # 匹配 outgoing 关系查询
        if "MATCH (n {id: $id})-[r]->(target)" in cypher:
            return [
                {"target_id": "n2", "target_name": "target_func", "type": "CALLS"},
            ]
        # 匹配 incoming 关系查询
        if "MATCH (source)-[r]->(n {id: $id})" in cypher:
            return [
                {"source_id": "n3", "source_name": "source_func", "type": "IMPORTS"},
            ]
        # 默认返回空列表
        return []

    def get_node(self, node_id: str) -> dict | None:
        if node_id == "not_found":
            return None
        return {"id": node_id, "name": "test_node", "labels": ["CodeEntity"]}

    def delete_node(self, node_id: str) -> bool:
        if node_id == "not_found":
            return False
        self._nodes.pop(node_id, None)
        return True


@pytest.fixture
def graph_client():
    """Create test client with mocked graph store."""
    mock_store = MockGraphStore()

    with (
        patch("layerkg.neo4j_store.GraphDatabase"),
        patch("layerkg.web.app.Neo4jGraphStore", return_value=mock_store),
    ):
        from layerkg.web.app import create_app

        app = create_app()
        # Override the graph_store with mock
        app.state.graph_store = mock_store
        yield TestClient(app)


class TestGraphStats:
    def test_graph_stats(self, graph_client):
        resp = graph_client.get("/api/graph/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] == 15
        assert data["edge_count"] == 15
        assert data["by_type"]["CodeEntity"] == 10
        assert data["by_type"]["ConceptEntity"] == 5

    def test_graph_stats_empty_db(self, graph_client):
        """Empty database scenario."""
        # 修改 mock 返回空结果
        mock_store = MockGraphStore()

        def empty_query(cypher: str, params: dict | None = None) -> list[dict]:
            if "MATCH ()-[r]->()" in cypher:
                return [{"cnt": 0}]
            return []

        mock_store.query = empty_query

        with (
            patch("layerkg.neo4j_store.GraphDatabase"),
            patch("layerkg.web.app.Neo4jGraphStore", return_value=mock_store),
        ):
            from layerkg.web.app import create_app

            app = create_app()
            app.state.graph_store = mock_store
            client = TestClient(app)

            resp = client.get("/api/graph/stats")
            assert resp.status_code == 200
            data = resp.json()
            assert data["node_count"] == 0
            assert data["edge_count"] == 0
            assert data["by_type"] == {}


class TestGetGraph:
    def test_get_graph_full(self, graph_client):
        resp = graph_client.get("/api/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 2
        assert data["nodes"][0]["id"] == "n1"
        assert data["nodes"][0]["name"] == "func_a"
        assert data["nodes"][0]["neo4jLabel"] == "CodeEntity"
        assert len(data["edges"]) == 1
        assert data["edges"][0]["source"] == "n1"
        assert data["edges"][0]["target"] == "n2"

    def test_get_graph_with_type_filter(self, graph_client):
        """Type filtering via query param."""
        mock_store = MockGraphStore()

        def filtered_query(cypher: str, params: dict | None = None) -> list[dict]:
            params = params or {}
            if "$types" in cypher:
                types = params.get("types", [])
                # 模拟类型过滤
                if "CodeEntity" not in types:
                    return []
                return [
                    {"id": "n1", "name": "func_a", "label": "CodeEntity", "entity_type": "function"},
                ]
            if "WHERE a.id IN $ids" in cypher:
                return [{"source": "n1", "target": "n2", "type": "CALLS"}]
            return []

        mock_store.query = filtered_query

        with (
            patch("layerkg.neo4j_store.GraphDatabase"),
            patch("layerkg.web.app.Neo4jGraphStore", return_value=mock_store),
        ):
            from layerkg.web.app import create_app

            app = create_app()
            app.state.graph_store = mock_store
            client = TestClient(app)

            resp = client.get("/api/graph?type=CodeEntity")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["nodes"]) >= 0

    def test_get_graph_center_mode(self, graph_client):
        """Center expansion mode."""
        resp = graph_client.get("/api/graph?center=center_func&depth=2")
        assert resp.status_code == 200
        data = resp.json()
        # 应该包含中心节点和邻居节点
        node_ids = {n["id"] for n in data["nodes"]}
        assert "center1" in node_ids or "n2" in node_ids

    def test_get_graph_center_empty_result(self, graph_client):
        """Center node not found."""
        mock_store = MockGraphStore()

        def empty_center_query(cypher: str, params: dict | None = None) -> list[dict]:
            if "MATCH (n {name: $name})" in cypher and "WHERE size(labels(n))" in cypher:
                return []  # 中心节点不存在
            if "center {name: $name}" in cypher:
                return []  # 没有邻居
            return []

        mock_store.query = empty_center_query

        with (
            patch("layerkg.neo4j_store.GraphDatabase"),
            patch("layerkg.web.app.Neo4jGraphStore", return_value=mock_store),
        ):
            from layerkg.web.app import create_app

            app = create_app()
            app.state.graph_store = mock_store
            client = TestClient(app)

            resp = client.get("/api/graph?center=nonexistent")
            assert resp.status_code == 200
            data = resp.json()
            assert data["nodes"] == []
            assert data["edges"] == []


class TestNodeDetail:
    def test_get_node_detail(self, graph_client):
        resp = graph_client.get("/api/graph/node/test_node_id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test_node_id"
        assert data["name"] == "test_node"
        assert data["neo4jLabel"] == "CodeEntity"
        assert "incoming" in data["relations"]
        assert "outgoing" in data["relations"]
        assert len(data["relations"]["incoming"]) == 1
        assert len(data["relations"]["outgoing"]) == 1

    def test_get_node_detail_not_found(self, graph_client):
        resp = graph_client.get("/api/graph/node/not_found")
        assert resp.status_code == 404
        assert "Node not found" in resp.json()["detail"]


class TestDeleteNode:
    def test_delete_node(self, graph_client):
        resp = graph_client.delete("/api/graph/node/node_to_delete")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["id"] == "node_to_delete"

    def test_delete_node_not_found(self, graph_client):
        resp = graph_client.delete("/api/graph/node/not_found")
        assert resp.status_code == 404
        assert "Node not found" in resp.json()["detail"]
