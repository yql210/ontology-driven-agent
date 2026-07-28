"""Tests for observability module — Prometheus metrics + structured logging + request_id."""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from ontoagent.api.web.app import create_app
from ontoagent.observability import (
    JsonFormatter,
    get_request_id,
    record_graph_operation,
    record_http_request,
    render_metrics,
    reset_request_id,
    set_request_id,
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


class TestRequestId:
    """request_id 上下文传播测试。"""

    def test_set_and_get_request_id(self):
        """set_request_id 后 get_request_id 返回相同的 ID。"""
        token = set_request_id("test-req-123")
        assert get_request_id() == "test-req-123"
        reset_request_id(token)
        assert get_request_id() == "-"

    def test_request_id_default_is_dash(self):
        """未设置时 request_id 默认为 '-'。"""
        assert get_request_id() == "-"

    def test_json_log_includes_request_id(self):
        """JSON 日志输出包含 request_id 字段。"""
        token = set_request_id("abc123def456")
        try:
            formatter = JsonFormatter()
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None,
            )
            # 手动应用 filter 模拟中间件行为
            from ontoagent.observability import _RequestIdFilter

            _RequestIdFilter().filter(record)
            output = formatter.format(record)
            parsed = json.loads(output)
            assert parsed["request_id"] == "abc123def456"
        finally:
            reset_request_id(token)

    def test_http_response_has_request_id_header(self):
        """HTTP 响应头包含 X-Request-ID。"""
        app = create_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) > 0

    def test_http_request_with_custom_request_id(self):
        """客户端传入 X-Request-ID 时服务器回传相同 ID。"""
        app = create_app()
        client = TestClient(app)
        resp = client.get("/health", headers={"X-Request-ID": "my-trace-id"})
        assert resp.headers.get("x-request-id") == "my-trace-id"


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
        """setup_logging 在 console 模式下使用 ConsoleFormatter（非 JsonFormatter）。"""
        monkeypatch.setenv("ONTOAGENT_LOG_FORMAT", "console")
        setup_logging()
        root = logging.getLogger()
        assert len(root.handlers) >= 1
        assert not isinstance(root.handlers[0].formatter, JsonFormatter)

    def test_setup_logging_file_handler(self, monkeypatch, tmp_path):
        """设置 ONTOAGENT_LOG_FILE 后启用文件日志。"""
        from logging.handlers import RotatingFileHandler

        log_file = tmp_path / "app.log"
        monkeypatch.setenv("ONTOAGENT_LOG_FILE", str(log_file))
        setup_logging()
        root = logging.getLogger()
        # 应该有 2 个 handler: stdout + file
        file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert len(file_handlers) == 1
        assert file_handlers[0].baseFilename == str(log_file)

    def test_setup_logging_third_party_silenced(self, monkeypatch):
        """第三方库日志被静默到 WARNING。"""
        monkeypatch.setenv("ONTOAGENT_LOG_FORMAT", "console")
        setup_logging()
        for name in ("httpx", "httpcore", "neo4j", "urllib3", "chromadb"):
            assert logging.getLogger(name).level == logging.WARNING

    def test_setup_logging_uvicorn_handlers_replaced(self, monkeypatch):
        """uvicorn logger 的 handler 被替换为统一格式。"""
        monkeypatch.setenv("ONTOAGENT_LOG_FORMAT", "json")
        setup_logging()
        uvicorn_access = logging.getLogger("uvicorn.access")
        assert len(uvicorn_access.handlers) >= 1
        assert isinstance(uvicorn_access.handlers[0].formatter, JsonFormatter)
