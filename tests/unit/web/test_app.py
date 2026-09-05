"""metrics_middleware 单元测试（P1-1：call_next 异常不再被 UnboundLocalError 掩盖）。

覆盖：
- 正常请求：记录指标、回传 X-Request-ID、reset_request_id 被调用
- endpoint 抛异常：按 500 记录、响应体不含 UnboundLocalError
- record_http_request 自身抛错：reset_request_id 仍执行，请求不挂死
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


# ===== lifespan shutdown 优雅清理（U4）=====


def _lifespan_app():
    """构造最小 app 对象：仅需可挂属性（graph_store / acl 等）的 state。"""
    return SimpleNamespace(state=SimpleNamespace())


@pytest.mark.unit
async def test_lifespan_shutdown_closes_store_and_acl():
    """退出 lifespan 上下文后，store.close 与 acl.close 均被调用。"""
    app = _lifespan_app()
    mock_store = MagicMock()
    mock_acl = MagicMock()

    with (
        patch("ontoagent.api.web.app.create_graph_store", return_value=mock_store),
        patch("ontoagent.api.web.app.RepoAccessControl", return_value=mock_acl),
    ):
        async with app_module.lifespan(app):
            app.state.graph_store.query("RETURN 1")

    mock_store.close.assert_called_once()
    mock_acl.close.assert_called_once()


@pytest.mark.unit
async def test_lifespan_shutdown_store_close_error_still_closes_acl(caplog):
    """store.close 抛异常：被捕获不向上抛，acl.close 仍被调用，并记录 warning 日志。"""
    app = _lifespan_app()
    mock_store = MagicMock()
    mock_store.close.side_effect = RuntimeError("store close failed")
    mock_acl = MagicMock()

    with (
        patch("ontoagent.api.web.app.create_graph_store", return_value=mock_store),
        patch("ontoagent.api.web.app.RepoAccessControl", return_value=mock_acl),
    ):
        async with app_module.lifespan(app):  # 不应抛异常
            app.state.graph_store.query("RETURN 1")

    mock_acl.close.assert_called_once()
    assert any("graph store close failed" in r.message for r in caplog.records)


@pytest.mark.unit
async def test_lifespan_shutdown_acl_close_error_does_not_raise(caplog):
    """acl.close 抛异常：被捕获不向上抛，退出上下文不中断，并记录 warning 日志。"""
    app = _lifespan_app()
    mock_store = MagicMock()
    mock_acl = MagicMock()
    mock_acl.close.side_effect = RuntimeError("acl close failed")

    with (
        patch("ontoagent.api.web.app.create_graph_store", return_value=mock_store),
        patch("ontoagent.api.web.app.RepoAccessControl", return_value=mock_acl),
    ):
        async with app_module.lifespan(app):  # 不应抛异常
            app.state.graph_store.query("RETURN 1")

    mock_store.close.assert_called_once()
    assert any("acl close failed" in r.message for r in caplog.records)
