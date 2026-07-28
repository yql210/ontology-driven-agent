"""Observability — 结构化日志 + Prometheus metrics。

日志：JSON 格式（生产）或 console 格式（开发），由 ONTOAGENT_LOG_FORMAT 控制。
Metrics：/metrics 端点暴露 Prometheus 格式指标，含 HTTP 请求计数/耗时 + LLM 调用计数。

环境变量：
    ONTOAGENT_LOG_FORMAT=json|console（默认 console，生产用 json）
    ONTOAGENT_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR（默认 INFO）
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ===== Metrics Registry =====
registry = CollectorRegistry()

# HTTP 请求计数
http_requests_total = Counter(
    "ontoagent_http_requests_total",
    "Total HTTP requests",
    labelnames=["method", "endpoint", "status"],
    registry=registry,
)

# HTTP 请求耗时
http_request_duration_seconds = Histogram(
    "ontoagent_http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=["method", "endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=registry,
)

# Graph DB 操作计数
graph_operations_total = Counter(
    "ontoagent_graph_operations_total",
    "Total graph database operations",
    labelnames=["operation", "status"],
    registry=registry,
)

# Graph DB 操作耗时
graph_operation_duration_seconds = Histogram(
    "ontoagent_graph_operation_duration_seconds",
    "Graph database operation duration in seconds",
    labelnames=["operation"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
    registry=registry,
)

# 活跃 Agent 会话数
active_sessions = Gauge(
    "ontoagent_active_sessions",
    "Number of active agent sessions",
    registry=registry,
)

# LLM 调用计数
llm_calls_total = Counter(
    "ontoagent_llm_calls_total",
    "Total LLM API calls",
    labelnames=["provider", "status"],
    registry=registry,
)

# 应用信息（常量 Gauge）
app_info = Gauge(
    "ontoagent_app_info",
    "Application metadata",
    labelnames=["version"],
    registry=registry,
)
app_info.labels(version="0.2.0").set(1)


def render_metrics() -> tuple[bytes, str]:
    """返回 Prometheus 格式的 metrics 数据和 content-type。"""
    return generate_latest(registry), CONTENT_TYPE_LATEST


# ===== Structured Logging =====
class JsonFormatter(logging.Formatter):
    """JSON 格式日志输出器，适配 ELK/Loki 等日志聚合系统。"""

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        # 合并 extra 字段
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "created", "relativeCreated",
                "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "filename", "module", "threadName", "thread", "msecs",
                "processName", "process", "levelname", "levelno", "pathname",
                "message", "taskName",
            ):
                try:
                    json.dumps(value)  # 可序列化检查
                    log_entry[key] = value
                except (TypeError, ValueError):
                    log_entry[key] = str(value)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging() -> None:
    """初始化全局日志配置。

    由 Web API 启动时调用，CLI 和测试不调用（使用默认 logging）。
    """
    log_format = os.getenv("ONTOAGENT_LOG_FORMAT", "console")
    log_level = os.getenv("ONTOAGENT_LOG_LEVEL", "INFO")

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有 handler（防止重复）
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    root_logger.addHandler(handler)


# ===== Middleware Helpers =====
def record_http_request(method: str, endpoint: str, status: int, duration: float) -> None:
    """记录 HTTP 请求指标。"""
    http_requests_total.labels(method=method, endpoint=endpoint, status=str(status)).inc()
    http_request_duration_seconds.labels(method=method, endpoint=endpoint).observe(duration)


def record_graph_operation(operation: str, duration: float, success: bool = True) -> None:
    """记录图数据库操作指标。"""
    status = "success" if success else "error"
    graph_operations_total.labels(operation=operation, status=status).inc()
    graph_operation_duration_seconds.labels(operation=operation).observe(duration)


# 模块导入时初始化日志
setup_logging()
