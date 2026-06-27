"""Tests for trace API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ontoagent.agent.trace import TraceCollector


@pytest.fixture
def trace_collector(tmp_path: Path) -> TraceCollector:
    """Create a TraceCollector with isolated temporary database."""
    db_path = tmp_path / "test_traces.db"
    return TraceCollector(max_traces=10, max_age_seconds=60, persist_path=str(db_path))


@pytest.fixture
def app_with_trace_collector(trace_collector: TraceCollector):
    """Create an app with the test trace collector injected."""
    from ontoagent.api.web import app as app_module
    from ontoagent.api.web.router import trace as trace_router

    # Patch the _trace_collector at the module level
    original_collector = app_module._trace_collector
    app_module._trace_collector = trace_collector
    trace_router.collector = trace_collector

    yield app_module.create_app()

    # Restore original
    app_module._trace_collector = original_collector
    trace_router.collector = original_collector


@pytest.fixture
def test_client(app_with_trace_collector) -> TestClient:
    """Create a test client with injected TraceCollector."""
    return TestClient(app_with_trace_collector)


@pytest.mark.unit
def test_list_traces_empty(test_client: TestClient):
    """Test list_traces returns empty list when no traces."""
    response = test_client.get("/api/trace/list")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.unit
def test_list_traces_with_data(test_client: TestClient, trace_collector: TraceCollector):
    """Test list_traces returns traces."""
    import asyncio

    async def add_trace():
        await trace_collector.start_trace("thread-1", "test query")
        await trace_collector.add_step("thread-1", type="thinking", content="thinking")
        await trace_collector.end_trace("thread-1", status="completed")

    asyncio.run(add_trace())

    response = test_client.get("/api/trace/list")
    assert response.status_code == 200
    traces = response.json()
    assert len(traces) == 1
    assert traces[0]["thread_id"] == "thread-1"
    assert traces[0]["query"] == "test query"
    assert traces[0]["status"] == "completed"
    assert traces[0]["step_count"] == 1


@pytest.mark.unit
def test_get_trace_404(test_client: TestClient):
    """Test get_trace returns 404 for non-existent trace."""
    response = test_client.get("/api/trace/thread/non-existent")
    assert response.status_code == 404
    # Error message includes the thread_id
    assert "non-existent" in response.json()["detail"]


@pytest.mark.unit
def test_get_trace_detail(test_client: TestClient, trace_collector: TraceCollector):
    """Test get_trace returns trace detail."""
    import asyncio

    async def add_trace():
        await trace_collector.start_trace("thread-1", "test query")
        await trace_collector.add_step(
            "thread-1",
            type="tool_call",
            content="calling tool",
            tool_name="search_knowledge",
            tool_args={"query": "test"},
        )
        await trace_collector.end_trace("thread-1", status="completed")

    asyncio.run(add_trace())

    response = test_client.get("/api/trace/thread/thread-1")
    assert response.status_code == 200
    data = response.json()
    assert data["thread_id"] == "thread-1"
    assert data["query"] == "test query"
    assert data["status"] == "completed"
    assert len(data["steps"]) == 1
    assert data["steps"][0]["type"] == "tool_call"
    assert data["steps"][0]["tool_name"] == "search_knowledge"
    assert data["steps"][0]["tool_args"] is not None


@pytest.mark.unit
def test_delete_trace(test_client: TestClient, trace_collector: TraceCollector):
    """Test delete_trace removes trace."""
    import asyncio

    async def add_trace():
        await trace_collector.start_trace("thread-1", "test query")
        await trace_collector.end_trace("thread-1", status="completed")

    asyncio.run(add_trace())

    # Verify trace exists
    response = test_client.get("/api/trace/thread/thread-1")
    assert response.status_code == 200

    # Delete trace
    response = test_client.delete("/api/trace/thread/thread-1")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Verify trace is gone
    response = test_client.get("/api/trace/thread/thread-1")
    assert response.status_code == 404


@pytest.mark.unit
def test_delete_trace_404(test_client: TestClient):
    """Test delete_trace returns 404 for non-existent trace."""
    response = test_client.delete("/api/trace/thread/non-existent")
    assert response.status_code == 404


@pytest.mark.unit
def test_get_mermaid(test_client: TestClient):
    """Test get_graph_mermaid returns Mermaid string."""
    response = test_client.get("/api/trace/graph/mermaid")
    assert response.status_code == 200
    data = response.json()
    assert "mermaid" in data
    assert isinstance(data["mermaid"], str)
    # Mermaid format should contain graph keywords
    assert "graph" in data["mermaid"].lower()


@pytest.mark.unit
def test_no_collector_returns_empty_list(tmp_path: Path):
    """Test API returns empty list when collector is None."""
    from ontoagent.api.web import app as app_module
    from ontoagent.api.web.router import trace as trace_router

    # Save original
    original_app_collector = app_module._trace_collector
    original_router_collector = trace_router.collector

    # Create app with fresh collector using empty database
    db_path = tmp_path / "empty_test.db"
    empty_collector = TraceCollector(max_traces=10, persist_path=str(db_path))
    app_module._trace_collector = empty_collector

    app = app_module.create_app()
    client = TestClient(app)
    response = client.get("/api/trace/list")
    assert response.status_code == 200
    assert response.json() == []

    # Restore
    app_module._trace_collector = original_app_collector
    trace_router.collector = original_router_collector


@pytest.mark.unit
def test_no_collector_returns_404_for_get(tmp_path: Path):
    """Test API returns 404 when collector is None for get trace."""
    from ontoagent.api.web import app as app_module
    from ontoagent.api.web.router import trace as trace_router

    # Save original
    original_app_collector = app_module._trace_collector
    original_router_collector = trace_router.collector

    # Set module-level collector to None to simulate uninitialized state
    app_module._trace_collector = None

    app = app_module.create_app()
    client = TestClient(app)
    response = client.get("/api/trace/thread/test-thread")
    assert response.status_code == 404
    assert "not initialized" in response.json()["detail"]

    # Restore
    app_module._trace_collector = original_app_collector
    trace_router.collector = original_router_collector
