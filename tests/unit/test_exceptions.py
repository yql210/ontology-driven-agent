from __future__ import annotations

from layerkg.exceptions import (
    EmbeddingError,
    LayerKGError,
    SchemaValidationError,
    StoreError,
)


def test_base_error_is_exception():
    assert issubclass(LayerKGError, Exception)


def test_schema_validation_error_inherits():
    assert issubclass(SchemaValidationError, LayerKGError)


def test_store_error_inherits():
    assert issubclass(StoreError, LayerKGError)


def test_error_message():
    err = LayerKGError('test message')
    assert str(err) == 'test message'


def test_embedding_error_is_layerkg_error():
    assert issubclass(EmbeddingError, LayerKGError)
