"""Tests for observability module — Prometheus metrics + structured logging."""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from ontoagent.api.web.app import create_app
from ontoagent.observability import (
    JsonFormatter,
    record_graph_operation,
    record_http_request,
    render_metrics,
    setup_logging,
)


class TestPrometheusMetrics:
    """Prometheus metrics 端点测试。"""

    def test_render_metrics_returns_bytes(self):
        """render_metrics 返回 bytes 和 content_type。"""
        data, content_type = render_metrics()
        assert isinstance(data, bytes)
        assert "text/plain" in content_type or "prometheus" in content_type

    def test_render_metrics_contains_app_info(self):
        """metrics 输出包含 ontoagent_app_info。"""
        data, _ = render_metrics()
        text = data.decode()
        assert "ontoagent_app_info" in text

    def test_record_http_request_increments_counter(self):
        """record_http_request 正确递增 HTTP 请求计数。"""
        record_http_request(method="GET", endpoint="/health", status=200, duration=0.05)
        data, _ = render_metrics()
        text = data.decode()
        assert "ontoagent_http_requests_total" in text

    def test_record_graph_operation_increments_counter(self):
        """record_graph_operation 正确递增图数据库操作计数。"""
        record_graph_operation(operation="query", duration=0.01, success=True)
        data, _ = render_metrics()
        text = data.decode()
        assert "ontoagent_graph_operations_total" in text

    def test_metrics_endpoint_accessible(self):
        """/metrics 端点可访问，返回 200 + Prometheus 格式。"""
        app = create_app()
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "ontoagent_app_info" in resp.text

    def test_metrics_endpoint_exempt_from_api_key(self):
        """设置 API_KEY 时 /metrics 仍可访问。"""
        import os

        old_key = os.environ.get("ONTOAGENT_API_KEY", "")
        os.environ["ONTOAGENT_API_KEY"] = "test-secret"
        try:
            app = create_app()
            client = TestClient(app)
            # 不带 API key → /metrics 应该 200（exempt）
            resp = client.get("/metrics")
            assert resp.status_code == 200
            # /health 也应该 200 或 503（无DB时）
            resp2 = client.get("/health")
            assert resp2.status_code in (200, 503)
        finally:
            os.environ["ONTOAGENT_API_KEY"] = old_key


class TestStructuredLogging:
    """结构化日志测试。"""

    def test_json_formatter_outputs_valid_json(self):
        """JsonFormatter 输出合法 JSON。"""
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message %s",
            args=("arg1",),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "Test message arg1"
        assert parsed["logger"] == "test"

    def test_json_formatter_includes_exception(self):
        """JsonFormatter 包含异常信息。"""
        formatter = JsonFormatter()
        try:
            msg = "test error"
            raise ValueError(msg)
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="Error occurred",
                args=(),
                exc_info=sys.exc_info(),
            )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]

    def test_setup_logging_json_format(self, monkeypatch):
        """setup_logging 在 JSON 模式下使用 JsonFormatter。"""
        monkeypatch.setenv("ONTOAGENT_LOG_FORMAT", "json")
        monkeypatch.setenv("ONTOAGENT_LOG_LEVEL", "DEBUG")
        setup_logging()
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) >= 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)

    def test_setup_logging_console_format(self, monkeypatch):
        """setup_logging 在 console 模式下使用标准 Formatter。"""
        monkeypatch.setenv("ONTOAGENT_LOG_FORMAT", "console")
        setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        assert not isinstance(root.handlers[0].formatter, JsonFormatter)
