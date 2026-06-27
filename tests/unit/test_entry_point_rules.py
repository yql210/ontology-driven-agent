from __future__ import annotations

from ontoagent.parsing.extractor.entry_point_rules import (
    JAVA_EVENT_ANNOTATIONS,
    JAVA_HTTP_ANNOTATIONS,
    JAVA_MQ_ANNOTATIONS,
    JAVA_RPC_ANNOTATIONS,
    JAVA_SCHEDULE_ANNOTATIONS,
    PY_EVENT_NAMES,
    PY_FRAMEWORK_HTTP_PATTERNS,
    PY_MQ_NAMES,
    PY_SCHEDULE_NAMES,
    PY_STANDALONE_HTTP,
    classify_java_annotation,
    classify_python_decorator,
)


# ── Python classification tests ────────────────────────────────────────


def test_fastapi_app_post_returns_http_api() -> None:
    """@app.post("/path") should be classified as http_api."""
    result = classify_python_decorator(("app", "post"), "post")
    assert result == "http_api"


def test_flask_app_route_returns_http_api() -> None:
    """@app.route("/path") should be classified as http_api."""
    result = classify_python_decorator(("app", "route"), "route")
    assert result == "http_api"


def test_router_post_returns_http_api() -> None:
    """@router.post("/path") should be classified as http_api."""
    result = classify_python_decorator(("router", "post"), "post")
    assert result == "http_api"


def test_router_put_returns_http_api() -> None:
    """@router.put("/path") should be classified as http_api."""
    result = classify_python_decorator(("router", "put"), "put")
    assert result == "http_api"


def test_router_delete_returns_http_api() -> None:
    """@router.delete("/path") should be classified as http_api."""
    result = classify_python_decorator(("router", "delete"), "delete")
    assert result == "http_api"


def test_router_patch_returns_http_api() -> None:
    """@router.patch("/path") should be classified as http_api."""
    result = classify_python_decorator(("router", "patch"), "patch")
    assert result == "http_api"


def test_app_put_returns_http_api() -> None:
    """@app.put("/path") should be classified as http_api."""
    result = classify_python_decorator(("app", "put"), "put")
    assert result == "http_api"


def test_app_delete_returns_http_api() -> None:
    """@app.delete("/path") should be classified as http_api."""
    result = classify_python_decorator(("app", "delete"), "delete")
    assert result == "http_api"


def test_app_patch_returns_http_api() -> None:
    """@app.patch("/path") should be classified as http_api."""
    result = classify_python_decorator(("app", "patch"), "patch")
    assert result == "http_api"


def test_scheduled_task_returns_scheduled() -> None:
    """A @scheduled_task decorator (standalone) should be classified as scheduled."""
    result = classify_python_decorator((), "scheduled_task")
    assert result == "scheduled"


def test_kafka_handler_returns_mq_consumer() -> None:
    """A @kafka_handler decorator should be classified as mq_consumer."""
    result = classify_python_decorator((), "kafka_handler")
    assert result == "mq_consumer"


def test_event_handler_returns_event_handler() -> None:
    """An @event_handler decorator should be classified as event_handler."""
    result = classify_python_decorator((), "event_handler")
    assert result == "event_handler"


def test_unknown_returns_none_python() -> None:
    """An unrecognised Python decorator should return None."""
    result = classify_python_decorator((), "some_random_decorator")
    assert result is None


# ── Java classification tests ──────────────────────────────────────────


def test_get_mapping_returns_http_api() -> None:
    """@GetMapping should be classified as http_api."""
    result = classify_java_annotation("GetMapping")
    assert result == "http_api"


def test_post_mapping_returns_http_api() -> None:
    """@PostMapping should be classified as http_api."""
    result = classify_java_annotation("PostMapping")
    assert result == "http_api"


def test_scheduled_returns_scheduled() -> None:
    """@Scheduled should be classified as scheduled."""
    result = classify_java_annotation("Scheduled")
    assert result == "scheduled"


def test_kafka_listener_returns_mq_consumer() -> None:
    """@KafkaListener should be classified as mq_consumer."""
    result = classify_java_annotation("KafkaListener")
    assert result == "mq_consumer"


def test_event_listener_returns_event_handler() -> None:
    """@EventListener should be classified as event_handler."""
    result = classify_java_annotation("EventListener")
    assert result == "event_handler"


def test_unknown_returns_none_java() -> None:
    """An unrecognised Java annotation should return None."""
    result = classify_java_annotation("SomeUnknownAnnotation")
    assert result is None
