"""Tests for TraceCollector."""
from __future__ import annotations

import asyncio

import pytest

from layerkg.agent.trace import TraceCollector, TraceStep


@pytest.mark.asyncio
async def test_start_and_get_trace(trace_collector: TraceCollector):
    """Test starting and retrieving a trace."""
    log = await trace_collector.start_trace("thread-1", "test query")
    assert log.thread_id == "thread-1"
    assert log.query == "test query"
    assert log.status == "running"
    assert log.steps == []

    retrieved = await trace_collector.get_trace("thread-1")
    assert retrieved is not None
    assert retrieved.thread_id == "thread-1"
    assert retrieved.query == "test query"


@pytest.mark.asyncio
async def test_add_step_generates_step_id(trace_collector: TraceCollector):
    """Test that add_step generates sequential step_ids."""
    await trace_collector.start_trace("thread-1", "test")

    step1 = await trace_collector.add_step("thread-1", "thinking", "thinking 1")
    assert step1.step_id == 0

    step2 = await trace_collector.add_step("thread-1", "tool_call", "calling tool")
    assert step2.step_id == 1

    step3 = await trace_collector.add_step("thread-1", "tool_result", "result")
    assert step3.step_id == 2

    trace = await trace_collector.get_trace("thread-1")
    assert len(trace.steps) == 3


@pytest.mark.asyncio
async def test_concurrent_add_steps(trace_collector: TraceCollector):
    """Test concurrent add_step calls don't mix up step_ids."""
    await trace_collector.start_trace("thread-1", "test")

    async def add_step_task(i: int) -> TraceStep:
        return await trace_collector.add_step("thread-1", "thinking", f"step {i}")

    results = await asyncio.gather(*[add_step_task(i) for i in range(10)])

    step_ids = [r.step_id for r in results]
    assert sorted(step_ids) == list(range(10))
    assert len(set(step_ids)) == 10  # All unique


@pytest.mark.asyncio
async def test_clean_old_traces():
    """Test that old traces are cleaned up."""
    # Small max_traces so cleanup triggers at 90% = ~9 traces
    collector = TraceCollector(max_traces=10, max_age_seconds=1)

    # Create traces to approach the 90% threshold (9 traces)
    for i in range(9):
        await collector.start_trace(f"old-{i}", "old query")
    # Wait for them to expire
    await asyncio.sleep(1.1)

    # Create a new trace to trigger cleanup (now 10 traces, > 9 threshold)
    await collector.start_trace("new-thread", "new query")

    # Old traces should be gone (deleted up to max_delete limit)
    old_trace = await collector.get_trace("old-0")
    assert old_trace is None

    # New trace should exist
    new_trace = await collector.get_trace("new-thread")
    assert new_trace is not None


@pytest.mark.asyncio
async def test_clean_excess_traces():
    """Test that exceeding max_traces cleans up oldest traces."""
    collector = TraceCollector(max_traces=3, max_age_seconds=60)

    await collector.start_trace("thread-1", "query 1")
    await asyncio.sleep(0.01)  # Ensure different timestamps
    await collector.start_trace("thread-2", "query 2")
    await asyncio.sleep(0.01)
    await collector.start_trace("thread-3", "query 3")
    await asyncio.sleep(0.01)
    await collector.start_trace("thread-4", "query 4")
    await asyncio.sleep(0.01)
    await collector.start_trace("thread-5", "query 5")

    # Should have at most max_traces
    traces = await collector.list_traces()
    assert len(traces) <= 3

    # Oldest traces should be deleted
    assert await collector.get_trace("thread-1") is None
    assert await collector.get_trace("thread-2") is None


@pytest.mark.asyncio
async def test_max_delete_limit():
    """Test that _clean_old_traces_unlocked respects max_delete limit."""
    collector = TraceCollector(max_traces=10, max_age_seconds=1)

    # Create 9 traces to reach 90% threshold
    for i in range(9):
        await collector.start_trace(f"old-{i}", f"query {i}")
    await asyncio.sleep(1.1)

    # Create 1 fresh trace (now 10 total, triggers cleanup)
    await collector.start_trace("fresh-1", "query")

    # max_delete=100, so all 9 old should be deleted
    # But let's verify cleanup happened by checking old-0 is gone
    old_trace = await collector.get_trace("old-0")
    assert old_trace is None
    # Fresh trace should exist
    fresh_trace = await collector.get_trace("fresh-1")
    assert fresh_trace is not None


@pytest.mark.asyncio
async def test_delete_trace(trace_collector: TraceCollector):
    """Test deleting a trace."""
    await trace_collector.start_trace("thread-1", "test")
    assert await trace_collector.get_trace("thread-1") is not None

    deleted = await trace_collector.delete_trace("thread-1")
    assert deleted is True

    assert await trace_collector.get_trace("thread-1") is None

    # Deleting non-existent trace returns False
    deleted = await trace_collector.delete_trace("thread-1")
    assert deleted is False


@pytest.mark.asyncio
async def test_tool_args_serialization(trace_collector: TraceCollector):
    """Test that tool_args dict is serialized to JSON string."""
    await trace_collector.start_trace("thread-1", "test")

    tool_args = {"query": "SELECT * FROM table", "limit": 10}
    step = await trace_collector.add_step(
        "thread-1",
        "tool_call",
        "calling sql",
        tool_name="sql_query",
        tool_args=tool_args,
    )

    assert step.tool_args is not None
    assert isinstance(step.tool_args, str)
    assert '"query":' in step.tool_args
    assert "SELECT * FROM table" in step.tool_args


@pytest.mark.asyncio
async def test_end_trace_status(trace_collector: TraceCollector):
    """Test ending a trace with different statuses."""
    await trace_collector.start_trace("thread-1", "test")

    # End with completed status
    log = await trace_collector.end_trace("thread-1", status="completed")
    assert log is not None
    assert log.status == "completed"
    assert log.total_duration_ms is not None
    assert log.total_duration_ms >= 0

    await trace_collector.start_trace("thread-2", "test")
    # End with failed status
    log = await trace_collector.end_trace("thread-2", status="failed")
    assert log is not None
    assert log.status == "failed"


@pytest.mark.asyncio
async def test_list_traces(trace_collector: TraceCollector):
    """Test listing all traces."""
    await trace_collector.start_trace("thread-1", "query 1")
    await trace_collector.start_trace("thread-2", "query 2")
    await trace_collector.start_trace("thread-3", "query 3")

    traces = await trace_collector.list_traces()
    assert len(traces) == 3

    thread_ids = {t.thread_id for t in traces}
    assert thread_ids == {"thread-1", "thread-2", "thread-3"}


@pytest.mark.asyncio
async def test_tool_args_truncation(trace_collector: TraceCollector):
    """Test that long tool_args are truncated to 1000 chars."""
    await trace_collector.start_trace("thread-1", "test")

    # Create a very long args dict
    long_args = {"data": "x" * 2000}
    step = await trace_collector.add_step(
        "thread-1",
        "tool_call",
        "calling with long args",
        tool_name="test_tool",
        tool_args=long_args,
    )

    assert step.tool_args is not None
    assert len(step.tool_args) <= 1000


@pytest.mark.asyncio
async def test_add_step_without_trace(trace_collector: TraceCollector):
    """Test adding a step when no trace exists returns step but doesn't add."""
    step = await trace_collector.add_step("non-existent", "thinking", "test")

    # Step is still created with step_id
    assert step is not None
    assert step.step_id == 0

    # But no trace exists
    trace = await trace_collector.get_trace("non-existent")
    assert trace is None


@pytest.mark.asyncio
async def test_multiple_threads_separate_counters(trace_collector: TraceCollector):
    """Test that different threads have separate step counters."""
    await trace_collector.start_trace("thread-1", "query 1")
    await trace_collector.start_trace("thread-2", "query 2")

    step1 = await trace_collector.add_step("thread-1", "thinking", "t1")
    step2 = await trace_collector.add_step("thread-2", "thinking", "t2")
    step3 = await trace_collector.add_step("thread-1", "thinking", "t1 again")

    assert step1.step_id == 0
    assert step2.step_id == 0
    assert step3.step_id == 1

    trace1 = await trace_collector.get_trace("thread-1")
    trace2 = await trace_collector.get_trace("thread-2")

    assert len(trace1.steps) == 2
    assert len(trace2.steps) == 1


@pytest.mark.asyncio
async def test_end_trace_calculates_duration(trace_collector: TraceCollector):
    """Test that end_trace calculates total duration."""
    await trace_collector.start_trace("thread-1", "test")

    # Wait a bit
    await asyncio.sleep(0.1)

    log = await trace_collector.end_trace("thread-1")

    assert log is not None
    assert log.total_duration_ms is not None
    assert log.total_duration_ms >= 100  # At least 100ms


@pytest.mark.asyncio
async def test_end_trace_nonexistent(trace_collector: TraceCollector):
    """Test ending a trace that doesn't exist."""
    log = await trace_collector.end_trace("non-existent", status="completed")
    assert log is None
