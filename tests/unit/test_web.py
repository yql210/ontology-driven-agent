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
