from __future__ import annotations

import json

from ontoagent.parsing.parser.java_parser import JavaParser
from ontoagent.parsing.parser.python_parser import PythonParser

# ── Python Integration Tests ────────────────────────────────────────────


def test_fastapi_post_detected_as_http_api() -> None:
    """FastAPI @app.post 应被识别为 http_api，提取路由到 entry_metadata。"""
    source = (
        b"from fastapi import FastAPI\n"
        b"app = FastAPI()\n"
        b"\n"
        b'@app.post("/api/payment/charge")\n'
        b"def charge(amount: float):\n"
        b"    pass\n"
    )
    parser = PythonParser()
    result = parser.parse_source(source, "api.py")
    assert result.error is None

    funcs = [e for e in result.entities if e.entity_type == "function"]
    assert len(funcs) >= 1
    charge = funcs[0]
    assert charge.entry_category == "http_api"
    meta = json.loads(charge.entry_metadata)
    assert meta["route"] == "/api/payment/charge"


def test_flask_route_detected_as_http_api() -> None:
    """Flask @app.route 应被识别为 http_api。"""
    source = (
        b"from flask import Flask\n"
        b"app = Flask(__name__)\n"
        b"\n"
        b'@app.route("/api/login", methods=["POST"])\n'
        b"def login():\n"
        b"    pass\n"
    )
    parser = PythonParser()
    result = parser.parse_source(source, "flask_app.py")
    assert result.error is None

    funcs = [e for e in result.entities if e.entity_type == "function"]
    assert len(funcs) >= 1
    login = funcs[0]
    assert login.entry_category == "http_api"


def test_scheduled_task_detected() -> None:
    """@scheduled_task 独立装饰器应被识别为 scheduled。"""
    source = b"@scheduled_task\ndef daily_report():\n    pass\n"
    parser = PythonParser()
    result = parser.parse_source(source, "tasks.py")
    assert result.error is None

    funcs = [e for e in result.entities if e.entity_type == "function"]
    assert len(funcs) >= 1
    daily = funcs[0]
    assert daily.entry_category == "scheduled"


def test_plain_function_not_classified() -> None:
    """无装饰器的普通函数不应设置 entry_category。"""
    source = b"def helper():\n    pass\n"
    parser = PythonParser()
    result = parser.parse_source(source, "util.py")
    assert result.error is None

    funcs = [e for e in result.entities if e.entity_type == "function"]
    assert len(funcs) >= 1
    helper = funcs[0]
    assert helper.entry_category is None


# ── Java Integration Tests ──────────────────────────────────────────────


def test_spring_get_mapping_detected_as_http_api() -> None:
    """@GetMapping 应被识别为 http_api，提取路由和 HTTP 方法。"""
    source = (
        b"import org.springframework.web.bind.annotation.*;\n"
        b"\n"
        b"@RestController\n"
        b"public class PaymentController {\n"
        b'    @GetMapping("/api/payment/status")\n'
        b"    public String status() {\n"
        b'        return "ok";\n'
        b"    }\n"
        b"}\n"
    )
    parser = JavaParser()
    result = parser.parse_source(source, "PaymentController.java")
    assert result.error is None

    funcs = [e for e in result.entities if e.entity_type == "function"]
    assert len(funcs) >= 1
    status = funcs[0]
    assert status.entry_category == "http_api"
    meta = json.loads(status.entry_metadata)
    assert meta["route"] == "/api/payment/status"
    assert meta["method"] == "GET"


def test_scheduled_detected_with_cron() -> None:
    """@Scheduled(cron=...) 应被识别为 scheduled，提取 cron 表达式。"""
    source = (
        b"import org.springframework.scheduling.annotation.Scheduled;\n"
        b"\n"
        b"public class Tasks {\n"
        b'    @Scheduled(cron = "0 0 * * *")\n'
        b"    public void dailyJob() {\n"
        b"    }\n"
        b"}\n"
    )
    parser = JavaParser()
    result = parser.parse_source(source, "Tasks.java")
    assert result.error is None

    funcs = [e for e in result.entities if e.entity_type == "function"]
    assert len(funcs) >= 1
    job = funcs[0]
    assert job.entry_category == "scheduled"
    meta = json.loads(job.entry_metadata)
    assert meta["cron"] == "0 0 * * *"


def test_kafka_listener_detected() -> None:
    """@KafkaListener 应被识别为 mq_consumer。"""
    source = (
        b"import org.springframework.kafka.annotation.KafkaListener;\n"
        b"\n"
        b"public class Consumers {\n"
        b'    @KafkaListener(topics = "payment-events")\n'
        b"    public void processPayment(String msg) {\n"
        b"    }\n"
        b"}\n"
    )
    parser = JavaParser()
    result = parser.parse_source(source, "Consumers.java")
    assert result.error is None

    funcs = [e for e in result.entities if e.entity_type == "function"]
    assert len(funcs) >= 1
    consumer = funcs[0]
    assert consumer.entry_category == "mq_consumer"


def test_plain_method_not_classified() -> None:
    """无注解的普通方法不应设置 entry_category。"""
    source = b"public class Util {\n    public void helper() {\n    }\n}\n"
    parser = JavaParser()
    result = parser.parse_source(source, "Util.java")
    assert result.error is None

    funcs = [e for e in result.entities if e.entity_type == "function"]
    assert len(funcs) >= 1
    helper = funcs[0]
    assert helper.entry_category is None
