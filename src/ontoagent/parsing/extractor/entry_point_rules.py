from __future__ import annotations

# ── Python classification tables ────────────────────────────────────────

# Framework HTTP patterns: (attr_path_prefix, method_name) tuples.
# e.g. ('app', 'post') matches @app.post(...) or @app.post("/path")
PY_FRAMEWORK_HTTP_PATTERNS: set[tuple[str, str]] = {
    ("app", "post"),
    ("app", "get"),
    ("app", "put"),
    ("app", "delete"),
    ("app", "patch"),
    ("app", "route"),
    ("router", "get"),
    ("router", "post"),
    ("router", "put"),
    ("router", "delete"),
    ("router", "patch"),
}

# Standalone HTTP decorator names (Phase 1 — unused).
PY_STANDALONE_HTTP: set[str] = set()

# Scheduled task decorator / callable names.
PY_SCHEDULE_NAMES: set[str] = {
    "scheduled_task",
    "task",
    "celery.task",
    "scheduled_job",
    "app.scheduled",
}

# Message-queue consumer names.
PY_MQ_NAMES: set[str] = {
    "kafka_handler",
    "consumer",
    "rabbitmq.consumer",
    "redis.pubsub",
    "subscriber",
}

# Event handler names (note: "subscriber" moved to PY_MQ_NAMES).
PY_EVENT_NAMES: set[str] = {
    "event_handler",
}

# ── Java classification tables ─────────────────────────────────────────

JAVA_HTTP_ANNOTATIONS: set[str] = {
    "GetMapping",
    "PostMapping",
    "PutMapping",
    "DeleteMapping",
    "PatchMapping",
    "RequestMapping",
    "Path",
}

JAVA_SCHEDULE_ANNOTATIONS: set[str] = {
    "Scheduled",
}

JAVA_MQ_ANNOTATIONS: set[str] = {
    "KafkaListener",
    "RabbitListener",
    "JmsListener",
    "StreamListener",
}

JAVA_EVENT_ANNOTATIONS: set[str] = {
    "EventListener",
    "TransactionalEventListener",
}

JAVA_RPC_ANNOTATIONS: set[str] = {
    "WebService",
    "RpcService",
}

# ── Classification functions ───────────────────────────────────────────


def classify_python_decorator(attr_path: tuple[str, ...], standalone_name: str) -> str | None:
    """Classify a Python decorator into a known entry-point category.

    Matching order:
      1. PY_FRAMEWORK_HTTP_PATTERNS  (any attr_path[:len(pat)] == pat)
      2. PY_STANDALONE_HTTP
      3. PY_SCHEDULE_NAMES
      4. PY_MQ_NAMES
      5. PY_EVENT_NAMES

    Args:
        attr_path: Dotted attribute chain, e.g. ("app", "post") for ``@app.post``.
        standalone_name: Bare decorator name, e.g. ``"scheduled_task"``.

    Returns:
        One of ``"http_api"``, ``"scheduled"``, ``"mq_consumer"``,
        ``"event_handler"``, or ``None`` if no category matches.
    """
    # 1) Framework HTTP patterns
    for pat in PY_FRAMEWORK_HTTP_PATTERNS:
        if attr_path[: len(pat)] == pat:
            return "http_api"

    # 2) Standalone HTTP (Phase 1 empty)
    if standalone_name in PY_STANDALONE_HTTP:
        return "http_api"

    # 3) Scheduled
    if standalone_name in PY_SCHEDULE_NAMES:
        return "scheduled"

    # 4) MQ consumer
    if standalone_name in PY_MQ_NAMES:
        return "mq_consumer"

    # 5) Event handler
    if standalone_name in PY_EVENT_NAMES:
        return "event_handler"

    return None


def classify_java_annotation(name: str) -> str | None:
    """Classify a Java annotation into a known entry-point category.

    Matching order:
      1. JAVA_HTTP_ANNOTATIONS
      2. JAVA_SCHEDULE_ANNOTATIONS
      3. JAVA_MQ_ANNOTATIONS
      4. JAVA_EVENT_ANNOTATIONS
      5. JAVA_RPC_ANNOTATIONS

    Args:
        name: The simple annotation name, e.g. ``"GetMapping"``.

    Returns:
        One of ``"http_api"``, ``"scheduled"``, ``"mq_consumer"``,
        ``"event_handler"``, ``"rpc_service"``, or ``None`` if no category
        matches.
    """
    if name in JAVA_HTTP_ANNOTATIONS:
        return "http_api"
    if name in JAVA_SCHEDULE_ANNOTATIONS:
        return "scheduled"
    if name in JAVA_MQ_ANNOTATIONS:
        return "mq_consumer"
    if name in JAVA_EVENT_ANNOTATIONS:
        return "event_handler"
    if name in JAVA_RPC_ANNOTATIONS:
        return "rpc_service"
    return None
