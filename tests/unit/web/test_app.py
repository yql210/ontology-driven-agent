"""metrics_middleware 单元测试（P1-1：call_next 异常不再被 UnboundLocalError 掩盖）。

覆盖：
- 正常请求：记录指标、回传 X-Request-ID、reset_request_id 被调用
- endpoint 抛异常：按 500 记录、响应体不含 UnboundLocalError
- record_http_request 自身抛错：reset_request_id 仍执行，请求不挂死
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ontoagent.api.web import app as app_module


@pytest.fixture
def app(tmp_path):
    """创建 Web app（不触发 lifespan，避免依赖 Neo4j 等外部服务）。"""
    from ontoagent.agent.trace import TraceCollector
    from ontoagent.api.web.router import trace as trace_router

    original_collector = app_module._trace_collector
    app_module._trace_collector = TraceCollector(max_traces=10, persist_path=str(tmp_path / "app_traces.db"))
    try:
        yield app_module.create_app()
    finally:
        app_module._trace_collector = original_collector
        trace_router.collector = original_collector


@pytest.fixture
def client(app):
    """TestClient：中间件抛出的异常转成 500 响应，便于断言响应体。"""
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_normal_request_records_metric_and_returns_request_id(client):
    """正常请求：记录指标、回传 X-Request-ID、reset_request_id 被调用。"""
    with (
        patch("ontoagent.api.web.app.record_http_request") as mock_record,
        patch("ontoagent.api.web.app.reset_request_id", wraps=app_module.reset_request_id) as mock_reset,
    ):
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")
    mock_record.assert_called_once()
    assert mock_record.call_args.kwargs["status"] == 200
    mock_reset.assert_called_once()


@pytest.mark.unit
def test_endpoint_exception_records_500_without_unbound_local_error(app, client):
    """call_next 抛异常：按 500 记录、响应体不含 UnboundLocalError、不回传 X-Request-ID。"""

    @app.get("/boom")
    async def boom():
        raise RuntimeError("real error")

    with (
        patch("ontoagent.api.web.app.record_http_request") as mock_record,
        patch("ontoagent.api.web.app.reset_request_id", wraps=app_module.reset_request_id) as mock_reset,
    ):
        response = client.get("/boom")

    assert response.status_code == 500
    assert "UnboundLocalError" not in response.text
    assert "real error" not in response.text
    mock_record.assert_called_once()
    assert mock_record.call_args.kwargs["status"] == 500
    assert "X-Request-ID" not in response.headers
    mock_reset.assert_called_once()


@pytest.mark.unit
def test_record_http_request_raising_still_resets_request_id(client):
    """record_http_request 抛错：reset_request_id 仍执行，请求不挂死。"""

    def _metrics_failure(*args, **kwargs):
        raise RuntimeError("metrics failure")

    with (
        patch("ontoagent.api.web.app.record_http_request", side_effect=_metrics_failure),
        patch("ontoagent.api.web.app.reset_request_id", wraps=app_module.reset_request_id) as mock_reset,
    ):
        response = client.get("/metrics")

    assert response.status_code == 500
    mock_reset.assert_called_once()
