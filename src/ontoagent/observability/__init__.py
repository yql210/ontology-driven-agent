"""Observability — 结构化日志 + Prometheus metrics + 请求追踪。

三大能力：
1. **结构化日志**：JSON 格式（生产）或 console 格式（开发），由 ONTOAGENT_LOG_FORMAT 控制。
2. **文件持久化**：设置 ONTOAGENT_LOG_FILE 后同时写入文件（RotatingFileHandler 轮转）。
3. **request_id 追踪**：每个 HTTP 请求自动注入 request_id，所有日志自动携带，串联完整调用链。

环境变量：
    ONTOAGENT_LOG_FORMAT=json|console          （默认 console，生产用 json）
    ONTOAGENT_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR （默认 INFO）
    ONTOAGENT_LOG_FILE=/data/logs/app.log      （设置后启用文件双写）
    ONTOAGENT_LOG_FILE_MAX_SIZE=52428800        （文件大小上限，默认 50MB）
    ONTOAGENT_LOG_FILE_BACKUP_COUNT=5           （保留备份数，默认 5）
"""

from __future__ import annotations

import contextvars
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ===== request_id 上下文传播 =====
# 使用 contextvars 实现异步安全的请求 ID 传播
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def set_request_id(request_id: str) -> contextvars.Token[str]:
    """设置当前请求 ID，返回 token 用于重置。"""
    return _request_id_ctx.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    """恢复之前的请求 ID。"""
    _request_id_ctx.reset(token)


def get_request_id() -> str:
    """获取当前请求 ID。"""
    return _request_id_ctx.get()


class _RequestIdFilter(logging.Filter):
    """自动给每条 LogRecord 注入 request_id。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


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

# LogRecord 的内置属性集合（这些不作为 extra 字段输出）
_RECORD_BUILTINS = frozenset({
    "name", "msg", "args", "created", "relativeCreated",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "filename", "module", "threadName", "thread", "msecs",
    "processName", "process", "levelname", "levelno", "pathname",
    "message", "taskName", "request_id",
})


class JsonFormatter(logging.Formatter):
    """JSON 格式日志输出器，适配 ELK/Loki 等日志聚合系统。

    自动注入 request_id 字段（由 _RequestIdFilter 提供）。
    """

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        # 合并 extra 字段
        for key, value in record.__dict__.items():
            if key not in _RECORD_BUILTINS:
                try:
                    json.dumps(value)  # 可序列化检查
                    log_entry[key] = value
                except (TypeError, ValueError):
                    log_entry[key] = str(value)
        return json.dumps(log_entry, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Console 格式日志，带 request_id 前缀。"""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        rid = getattr(record, "request_id", "-")
        if rid and rid != "-":
            return f"[{rid[:8]}] {base}"
        return base


def _make_formatter(log_format: str) -> logging.Formatter:
    """根据格式选择创建 formatter。"""
    if log_format == "json":
        return JsonFormatter()
    return ConsoleFormatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging() -> None:
    """初始化全局日志配置。

    - stdout handler 始终启用（Docker 采集用）
    - 设置 ONTOAGENT_LOG_FILE 后同时写入文件（RotatingFileHandler 轮转）
    - 第三方库日志静默到 WARNING
    - 每条日志自动注入 request_id
    """
    log_format = os.getenv("ONTOAGENT_LOG_FORMAT", "console")
    log_level = os.getenv("ONTOAGENT_LOG_LEVEL", "INFO")
    log_file = os.getenv("ONTOAGENT_LOG_FILE", "")
    log_file_max = int(os.getenv("ONTOAGENT_LOG_FILE_MAX_SIZE", str(50 * 1024 * 1024)))
    log_file_count = int(os.getenv("ONTOAGENT_LOG_FILE_BACKUP_COUNT", "5"))

    formatter = _make_formatter(log_format)
    rid_filter = _RequestIdFilter()

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # Handler 1: stdout（始终启用）
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(rid_filter)
    root_logger.addHandler(stdout_handler)

    # Handler 2: 文件（可选，设置 ONTOAGENT_LOG_FILE 后启用）
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=log_file_max,
            backupCount=log_file_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(rid_filter)
        root_logger.addHandler(file_handler)

    # 第三方库日志静默（Web 层与 CLI 统一）
    for noisy in ("httpx", "httpcore", "neo4j", "urllib3", "chromadb"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # uvicorn access log 也用相同格式
    for uv_logger in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv_logger_obj = logging.getLogger(uv_logger)
        uv_logger_obj.handlers.clear()
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        handler.addFilter(rid_filter)
        uv_logger_obj.addHandler(handler)


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
