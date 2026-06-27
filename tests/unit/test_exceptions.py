from __future__ import annotations

from ontoagent.domain.exceptions import (
    EmbeddingError,
    ExtractionError,
    OntoAgentError,
    SchemaValidationError,
    StoreError,
)


def test_base_error_is_exception():
    assert issubclass(OntoAgentError, Exception)


def test_schema_validation_error_inherits():
    assert issubclass(SchemaValidationError, OntoAgentError)


def test_store_error_inherits():
    assert issubclass(StoreError, OntoAgentError)


def test_error_message():
    err = OntoAgentError("test message")
    assert str(err) == "test message"


def test_embedding_error_is_ontoagent_error():
    assert issubclass(EmbeddingError, OntoAgentError)


def test_extraction_error_inherits():
    """ExtractionError 继承 OntoAgentError。"""
    assert issubclass(ExtractionError, OntoAgentError)
