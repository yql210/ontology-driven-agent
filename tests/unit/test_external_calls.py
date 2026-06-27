from __future__ import annotations

from ontoagent.parsing.extractor.external_calls import (
    extract_external_calls_java,
    extract_external_calls_python,
)
from ontoagent.parsing.parser.java_parser import JavaParser
from ontoagent.parsing.parser.python_parser import PythonParser

# ---------------------------------------------------------------------------
# Python snippets
# ---------------------------------------------------------------------------

PYTHON_REQUESTS_CALL = b"""\
def charge_payment():
    requests.post("http://payment-api/charge")
"""

PYTHON_HTTPX_CALL = b"""\
def lookup_user():
    httpx.get("http://user-service/")
"""

PYTHON_KAFKA_PRODUCE = b"""\
def produce():
    kafka_producer.send("payment-events")
"""


# ---------------------------------------------------------------------------
# Java snippets
# ---------------------------------------------------------------------------

JAVA_RESTTEMPLATE_CALL = b"""\
class OrderService {
    void createOrder() {
        restTemplate.postForObject("http://order-service/api", null);
    }
}
"""

JAVA_WEBCLIENT_CALL = b"""\
class HealthCheck {
    void ping() {
        webClient.get().uri("http://payment-api/health");
    }
}
"""

JAVA_KAFKA_SEND = b"""\
class PaymentProducer {
    void sendEvent(String msg) {
        kafkaTemplate.send("payment-events", msg);
    }
}
"""


PYTHON_FSTRING_CALL = b"""\
def check_risk():
    requests.get(f"http://risk-service.internal/check?user={user_id}")
"""


# ---------------------------------------------------------------------------
# Tests: Python
# ---------------------------------------------------------------------------


def test_python_requests_calls_service() -> None:
    """requests.post("http://payment-api/charge") → calls_service payment-api."""
    parser = PythonParser()
    tree = parser._parser.parse(PYTHON_REQUESTS_CALL)
    root_node = tree.root_node

    relations = extract_external_calls_python(root_node, PYTHON_REQUESTS_CALL, "test_module", "test.py")
    assert len(relations) == 1
    rel = relations[0]
    assert rel.source_name == "test_module"
    assert rel.source_type == "module"
    assert rel.target_name == "payment-api"
    assert rel.target_type == "ServiceEntity"
    assert rel.relation_type == "calls_service"
    assert rel.file_path == "test.py"


def test_python_httpx_calls_service() -> None:
    """httpx.get("http://user-service/") → calls_service user-service."""
    parser = PythonParser()
    tree = parser._parser.parse(PYTHON_HTTPX_CALL)
    root_node = tree.root_node

    relations = extract_external_calls_python(root_node, PYTHON_HTTPX_CALL, "test_module", "test.py")
    assert len(relations) == 1
    rel = relations[0]
    assert rel.source_name == "test_module"
    assert rel.source_type == "module"
    assert rel.target_name == "user-service"
    assert rel.target_type == "ServiceEntity"
    assert rel.relation_type == "calls_service"
    assert rel.file_path == "test.py"


def test_python_kafka_publishes_to() -> None:
    """kafka_producer.send("payment-events") → publishes_to payment-events."""
    parser = PythonParser()
    tree = parser._parser.parse(PYTHON_KAFKA_PRODUCE)
    root_node = tree.root_node

    relations = extract_external_calls_python(root_node, PYTHON_KAFKA_PRODUCE, "test_module", "test.py")
    assert len(relations) == 1
    rel = relations[0]
    assert rel.source_name == "test_module"
    assert rel.source_type == "module"
    assert rel.target_name == "payment-events"
    assert rel.target_type == "ConceptEntity"
    assert rel.relation_type == "publishes_to"
    assert rel.file_path == "test.py"


def test_python_fstring_requests_calls_service() -> None:
    """requests.get(f\"http://risk-service.internal/check?user={user_id}\") → calls_service risk-service.internal."""
    parser = PythonParser()
    tree = parser._parser.parse(PYTHON_FSTRING_CALL)
    root_node = tree.root_node

    relations = extract_external_calls_python(root_node, PYTHON_FSTRING_CALL, "test_module", "test.py")
    assert len(relations) == 1
    rel = relations[0]
    assert rel.source_name == "test_module"
    assert rel.source_type == "module"
    assert rel.target_name == "risk-service.internal"
    assert rel.target_type == "ServiceEntity"
    assert rel.relation_type == "calls_service"
    assert rel.file_path == "test.py"


# ---------------------------------------------------------------------------
# Tests: Java
# ---------------------------------------------------------------------------


def test_java_resttemplate_calls_service() -> None:
    """restTemplate.postForObject("http://order-service/api",...) → calls_service order-service."""
    parser = JavaParser()
    tree = parser._parser.parse(JAVA_RESTTEMPLATE_CALL)
    root_node = tree.root_node

    relations = extract_external_calls_java(root_node, JAVA_RESTTEMPLATE_CALL, "OrderService.java")
    assert len(relations) == 1
    rel = relations[0]
    assert rel.source_name == "OrderService.java"
    assert rel.source_type == "file"
    assert rel.target_name == "order-service"
    assert rel.target_type == "ServiceEntity"
    assert rel.relation_type == "calls_service"
    assert rel.file_path == "OrderService.java"


def test_java_webclient_calls_service() -> None:
    """webClient.get().uri("http://payment-api/health") → calls_service payment-api."""
    parser = JavaParser()
    tree = parser._parser.parse(JAVA_WEBCLIENT_CALL)
    root_node = tree.root_node

    relations = extract_external_calls_java(root_node, JAVA_WEBCLIENT_CALL, "HealthCheck.java")
    assert len(relations) == 1
    rel = relations[0]
    assert rel.source_name == "HealthCheck.java"
    assert rel.source_type == "file"
    assert rel.target_name == "payment-api"
    assert rel.target_type == "ServiceEntity"
    assert rel.relation_type == "calls_service"
    assert rel.file_path == "HealthCheck.java"


def test_java_kafkatemplate_publishes_to() -> None:
    """kafkaTemplate.send("payment-events", msg) → publishes_to payment-events."""
    parser = JavaParser()
    tree = parser._parser.parse(JAVA_KAFKA_SEND)
    root_node = tree.root_node

    relations = extract_external_calls_java(root_node, JAVA_KAFKA_SEND, "PaymentProducer.java")
    assert len(relations) == 1
    rel = relations[0]
    assert rel.source_name == "PaymentProducer.java"
    assert rel.source_type == "file"
    assert rel.target_name == "payment-events"
    assert rel.target_type == "ConceptEntity"
    assert rel.relation_type == "publishes_to"
    assert rel.file_path == "PaymentProducer.java"
