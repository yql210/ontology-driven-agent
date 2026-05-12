"""pytest 共享 fixtures."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每个测试后重置全局单例"""
    yield
    from layerkg.agent.graph import _reset_llm
    _reset_llm()


@pytest.fixture
def trace_collector(tmp_path: Path):
    """Create TraceCollector with isolated temporary database."""
    from layerkg.agent.trace import TraceCollector
    db_path = tmp_path / "test_traces.db"
    return TraceCollector(max_traces=10, max_age_seconds=60, persist_path=str(db_path))
