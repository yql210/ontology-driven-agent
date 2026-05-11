"""pytest 共享 fixtures."""

import pytest


@pytest.fixture
def trace_collector():
    from layerkg.agent.trace import TraceCollector
    return TraceCollector(max_traces=10, max_age_seconds=60)
