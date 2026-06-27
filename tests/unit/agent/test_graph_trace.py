"""Tests for graph.py trace integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ontoagent.agent.graph import run_query_stream
from ontoagent.agent.trace import TraceCollector


@pytest.mark.unit
async def test_trace_thinking_recorded(trace_collector: TraceCollector):
    """Test that thinking steps are recorded in trace."""

    async def mock_astream_events(*args, **kwargs):  # type: ignore[misc]
        """Mock astream_events to produce a thinking event."""
        yield {
            "event": "on_chat_model_end",
            "data": {"output": MagicMock(content="This is a thinking output")},
        }

    with patch("ontoagent.agent.graph.create_agent") as mock_create_agent:
        mock_agent = MagicMock()
        mock_agent.astream_events = mock_astream_events
        mock_create_agent.return_value = mock_agent

        events = []
        async for event in run_query_stream(
            "test query",
            thread_id="test-thread",
            trace_collector=trace_collector,
        ):
            events.append(event)

        # Verify trace was recorded
        trace = await trace_collector.get_trace("test-thread")
        assert trace is not None
        assert trace.thread_id == "test-thread"
        assert trace.query == "test query"
        assert len(trace.steps) == 1
        assert trace.steps[0].type == "thinking"
        assert "thinking output" in trace.steps[0].content


@pytest.mark.unit
async def test_trace_tool_call_recorded(trace_collector: TraceCollector):
    """Test that tool calls are recorded in trace."""

    async def mock_astream_events(*args, **kwargs):  # type: ignore[misc]
        """Mock astream_events to produce tool start/end events."""
        yield {
            "event": "on_tool_start",
            "name": "search_knowledge",
            "data": {"input": {"query": "test"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "search_knowledge",
            "data": {"output": "result data"},
        }

    with patch("ontoagent.agent.graph.create_agent") as mock_create_agent:
        mock_agent = MagicMock()
        mock_agent.astream_events = mock_astream_events
        mock_create_agent.return_value = mock_agent

        events = []
        async for event in run_query_stream(
            "test query",
            thread_id="test-thread",
            trace_collector=trace_collector,
        ):
            events.append(event)

        # Verify trace has tool_call and tool_result
        trace = await trace_collector.get_trace("test-thread")
        assert trace is not None
        assert len(trace.steps) == 2
        assert trace.steps[0].type == "tool_call"
        assert trace.steps[0].tool_name == "search_knowledge"
        assert trace.steps[1].type == "tool_result"
        assert trace.steps[1].tool_name == "search_knowledge"
        assert trace.steps[1].duration_ms is not None


@pytest.mark.unit
async def test_trace_failed_status(trace_collector: TraceCollector):
    """Test that failed status is set on exception."""

    async def mock_astream_events(*args, **kwargs):  # type: ignore[misc]
        """Mock astream_events to raise an exception."""
        raise ValueError("Test error")
        yield  # makes this an async generator function

    with patch("ontoagent.agent.graph.create_agent") as mock_create_agent:
        mock_agent = MagicMock()
        mock_agent.astream_events = mock_astream_events
        mock_create_agent.return_value = mock_agent

        events = []
        async for event in run_query_stream(
            "test query",
            thread_id="test-thread",
            trace_collector=trace_collector,
        ):
            events.append(event)

        # Verify trace has failed status
        trace = await trace_collector.get_trace("test-thread")
        assert trace is not None
        assert trace.status == "failed"


@pytest.mark.unit
async def test_no_trace_without_collector():
    """Test that run_query_stream works without trace_collector."""

    async def mock_astream_events(*args, **kwargs):  # type: ignore[misc]
        """Mock astream_events to produce a simple event."""
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": MagicMock(content="token")},
        }

    with patch("ontoagent.agent.graph.create_agent") as mock_create_agent:
        mock_agent = MagicMock()
        mock_agent.astream_events = mock_astream_events
        mock_create_agent.return_value = mock_agent

        # Should not raise any exception without collector
        events = []
        async for event in run_query_stream(
            "test query",
            thread_id="test-thread",
            trace_collector=None,
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0]["type"] == "token"


@pytest.mark.unit
async def test_trace_completed_status_normal_end(trace_collector: TraceCollector):
    """Test that completed status is set on normal completion."""

    async def mock_astream_events(*args, **kwargs):  # type: ignore[misc]
        """Mock astream_events to produce a normal event."""
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": MagicMock(content="token")},
        }

    with patch("ontoagent.agent.graph.create_agent") as mock_create_agent:
        mock_agent = MagicMock()
        mock_agent.astream_events = mock_astream_events
        mock_create_agent.return_value = mock_agent

        events = []
        async for event in run_query_stream(
            "test query",
            thread_id="test-thread",
            trace_collector=trace_collector,
        ):
            events.append(event)

        # Verify trace has completed status
        trace = await trace_collector.get_trace("test-thread")
        assert trace is not None
        assert trace.status == "completed"
        assert trace.total_duration_ms is not None
