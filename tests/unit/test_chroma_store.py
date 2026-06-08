from __future__ import annotations

from contextlib import suppress
from unittest.mock import MagicMock, Mock, patch

import pytest

from layerkg.chroma_store import (
    ChromaStore,
    OllamaEmbeddingFunction,
    _format_query_results,
    _sanitize_metadata,
)
from layerkg.exceptions import EmbeddingError

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_ollama_response():
    """构造 mock Ollama API 响应。"""

    def _make_response(embeddings: list[list[float]]) -> Mock:
        response = Mock()
        response.json.return_value = {"embeddings": embeddings}
        response.raise_for_status = Mock()
        return response

    return _make_response


@pytest.fixture
def mock_ollama():
    """Mock httpx.Client.post，返回固定 8 维向量。"""

    def _embed_fn(input: list[str]) -> list[list[float]]:
        return [[0.1 * i, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8] for i in range(len(input))]

    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client.is_closed = False

        def mock_close():
            mock_client.is_closed = True

        mock_client.close = Mock(side_effect=mock_close)
        mock_client.post = Mock(
            side_effect=lambda url, json, **kwargs: Mock(
                json=lambda: {"embeddings": _embed_fn(json.get("input", []))}, raise_for_status=Mock()
            )
        )
        mock_client_class.return_value = mock_client
        yield mock_client


@pytest.fixture
def chroma_store(mock_ollama):
    """创建内存模式 ChromaStore（persist_dir=None）。每个测试使用独立集合名。"""
    import uuid

    store = ChromaStore(persist_dir=None, collection_name=f"test_{uuid.uuid4().hex}")
    yield store
    store.close()


@pytest.fixture
def sample_embedding():
    """返回 8 维样本嵌入向量。"""
    return [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


# =============================================================================
# OllamaEmbeddingFunction Tests (7 tests)
# =============================================================================


class TestOllamaEmbeddingFunction:
    """测试 OllamaEmbeddingFunction 类。"""

    def test_embed_single_text_returns_vector(self, mock_ollama_response):
        # Arrange
        embed_fn = OllamaEmbeddingFunction("http://localhost:11434", "qwen2.5-coder:0.5b")
        mock_response = mock_ollama_response([[0.1, 0.2, 0.3, 0.4]])
        embed_fn._client.post = Mock(return_value=mock_response)

        # Act
        result = embed_fn(["test text"])

        # Assert
        assert len(result) == 1
        assert list(result[0]) == [0.1, 0.2, 0.3, 0.4]
        embed_fn.close()

    def test_embed_batch_returns_vectors(self, mock_ollama_response):
        # Arrange
        embed_fn = OllamaEmbeddingFunction("http://localhost:11434", "qwen2.5-coder:0.5b")
        mock_response = mock_ollama_response(
            [
                [0.1, 0.2, 0.3, 0.4],
                [0.5, 0.6, 0.7, 0.8],
            ]
        )
        embed_fn._client.post = Mock(return_value=mock_response)

        # Act
        result = embed_fn(["text1", "text2"])

        # Assert
        assert len(result) == 2
        assert list(result[0]) == [0.1, 0.2, 0.3, 0.4]
        assert list(result[1]) == [0.5, 0.6, 0.7, 0.8]
        embed_fn.close()

    def test_embed_empty_input_skips_api_call(self):
        # Arrange - 使用 mock client 来验证不会被调用
        embed_fn = OllamaEmbeddingFunction("http://localhost:11434", "qwen2.5-coder:0.5b")
        mock_post = Mock()
        embed_fn._client.post = mock_post

        # Act - 空输入应该提前返回，不调用 API
        # 注意：ChromaDB EmbeddingFunction 协议不允许空输入，
        # 但我们的实现会提前返回，不调用 API
        with suppress(ValueError):
            embed_fn([])

        # Assert - API 没有被调用（提前返回）
        assert mock_post.call_count == 0
        embed_fn.close()

    def test_embed_connection_error_raises_embedding_error(self):
        # Arrange
        embed_fn = OllamaEmbeddingFunction("http://invalid:9999", "qwen2.5-coder:0.5b")

        # Act & Assert
        with pytest.raises(EmbeddingError, match="Ollama embedding failed"):
            embed_fn(["test"])
        embed_fn.close()

    def test_embed_http_error_raises_embedding_error(self):
        # Arrange
        from httpx import HTTPStatusError, Request, Response

        embed_fn = OllamaEmbeddingFunction("http://localhost:11434", "qwen2.5-coder:0.5b")
        request = Request("POST", "http://localhost:11434/api/embed")
        response = Response(500, request=request)
        embed_fn._client.post = Mock(side_effect=HTTPStatusError("Server error", request=request, response=response))

        # Act & Assert
        with pytest.raises(EmbeddingError, match="Ollama embedding failed"):
            embed_fn(["test"])
        embed_fn.close()

    def test_embed_dimension_cached_after_first_call(self, mock_ollama_response):
        # Arrange
        embed_fn = OllamaEmbeddingFunction("http://localhost:11434", "qwen2.5-coder:0.5b")
        mock_response = mock_ollama_response([[0.1] * 768])
        embed_fn._client.post = Mock(return_value=mock_response)

        # Act
        dim1 = embed_fn.dimension
        dim2 = embed_fn.dimension

        # Assert
        assert dim1 == 768
        assert dim2 == 768
        assert embed_fn._client.post.call_count == 1  # 只调用一次，后续使用缓存
        embed_fn.close()

    def test_embed_close_closes_http_client(self):
        # Arrange
        embed_fn = OllamaEmbeddingFunction("http://localhost:11434", "qwen2.5-coder:0.5b")
        assert not embed_fn._client.is_closed

        # Act
        embed_fn.close()

        # Assert
        assert embed_fn._client.is_closed


# =============================================================================
# Metadata 工具函数测试 (2 tests)
# =============================================================================


class TestSanitizeMetadata:
    """测试 _sanitize_metadata 函数。"""

    def test_sanitize_metadata_keeps_valid_types(self):
        # Arrange
        metadata = {
            "name": "test",
            "count": 42,
            "score": 0.95,
            "active": True,
        }

        # Act
        result = _sanitize_metadata(metadata)

        # Assert
        assert result == metadata
        assert result["name"] == "test"
        assert result["count"] == 42
        assert result["score"] == 0.95
        assert result["active"] is True

    def test_sanitize_metadata_converts_invalid_types_to_str(self):
        # Arrange
        metadata = {
            "name": "test",
            "tags": ["tag1", "tag2"],  # list 会被转换
            "nested": {"key": "value"},  # dict 会被转换
            "none_value": None,  # None 会被转换
            "count": 42,  # int 保持不变
        }

        # Act
        result = _sanitize_metadata(metadata)

        # Assert
        assert result["name"] == "test"
        assert result["count"] == 42
        assert result["tags"] == "['tag1', 'tag2']"
        assert result["nested"] == "{'key': 'value'}"
        assert result["none_value"] == "None"


# =============================================================================
# ChromaStore 写入测试 (7 tests)
# =============================================================================


class TestChromaStoreWrite:
    """测试 ChromaStore 写入操作。"""

    def test_put_entity_stores_successfully(self, chroma_store):
        # Arrange
        entity_id = "test-entity-1"
        text = "This is a test function"
        metadata = {"entity_type": "function", "file": "test.py"}

        # Act
        chroma_store.put_entity(entity_id, text, metadata)

        # Assert
        result = chroma_store.get_entity(entity_id)
        assert result is not None
        assert result["id"] == entity_id
        assert result["text"] == text
        assert result["metadata"]["entity_type"] == "function"

    def test_put_entity_empty_text_raises_value_error(self, chroma_store):
        # Arrange
        entity_id = "test-entity-2"

        # Act & Assert
        with pytest.raises(ValueError, match="text cannot be empty"):
            chroma_store.put_entity(entity_id, "", {"type": "test"})

    def test_put_entity_whitespace_only_raises_value_error(self, chroma_store):
        # Arrange
        entity_id = "test-entity-3"

        # Act & Assert
        with pytest.raises(ValueError, match="text cannot be empty"):
            chroma_store.put_entity(entity_id, "   \n\t  ", {"type": "test"})

    def test_put_entity_upsert_overwrites_existing(self, chroma_store):
        # Arrange
        entity_id = "test-entity-4"
        chroma_store.put_entity(entity_id, "original text", {"version": 1})

        # Act
        chroma_store.put_entity(entity_id, "updated text", {"version": 2})

        # Assert
        result = chroma_store.get_entity(entity_id)
        assert result["text"] == "updated text"
        assert result["metadata"]["version"] == 2

    def test_put_entities_batch_stores_all(self, chroma_store):
        # Arrange
        items = [
            ("entity-1", "text 1", {"type": "a"}),
            ("entity-2", "text 2", {"type": "b"}),
            ("entity-3", "text 3", {"type": "c"}),
        ]

        # Act
        chroma_store.put_entities_batch(items)

        # Assert
        assert chroma_store.count() == 3
        assert chroma_store.get_entity("entity-1") is not None
        assert chroma_store.get_entity("entity-2") is not None
        assert chroma_store.get_entity("entity-3") is not None

    def test_put_entities_batch_skips_empty_text(self, chroma_store):
        # Arrange
        items = [
            ("entity-1", "valid text", {"type": "a"}),
            ("entity-2", "", {"type": "b"}),  # 空文本，应被跳过
            ("entity-3", "   ", {"type": "c"}),  # 纯空白，应被跳过
            ("entity-4", "also valid", {"type": "d"}),
        ]

        # Act
        chroma_store.put_entities_batch(items)

        # Assert
        assert chroma_store.count() == 2
        assert chroma_store.get_entity("entity-1") is not None
        assert chroma_store.get_entity("entity-2") is None
        assert chroma_store.get_entity("entity-3") is None
        assert chroma_store.get_entity("entity-4") is not None

    def test_put_entities_batch_empty_list_is_noop(self, chroma_store):
        # Arrange
        initial_count = chroma_store.count()

        # Act
        chroma_store.put_entities_batch([])

        # Assert
        assert chroma_store.count() == initial_count

    def test_put_entities_batch_splits(self, chroma_store):
        # Arrange
        items = [(f"entity-{i}", f"text {i}", {"idx": i}) for i in range(120)]
        batch_size = 50

        # Act
        chroma_store.put_entities_batch(items, batch_size=batch_size)

        # Assert
        assert chroma_store.count() == 120

    def test_put_entities_batch_size_1(self, chroma_store):
        # Arrange
        items = [(f"entity-{i}", f"text {i}", {"idx": i}) for i in range(10)]

        # Act
        chroma_store.put_entities_batch(items, batch_size=1)

        # Assert
        assert chroma_store.count() == 10


# =============================================================================
# ChromaStore 查询测试 (7 tests)
# =============================================================================


class TestChromaStoreQuery:
    """测试 ChromaStore 查询操作。"""

    def test_search_returns_results(self, chroma_store):
        # Arrange
        chroma_store.put_entity("e1", "function that calculates sum", {"type": "function"})
        chroma_store.put_entity("e2", "class for user management", {"type": "class"})
        chroma_store.put_entity("e3", "utility function for logging", {"type": "function"})

        # Act
        results = chroma_store.search("calculation")

        # Assert
        assert len(results) > 0
        assert "id" in results[0]
        assert "text" in results[0]
        assert "metadata" in results[0]
        assert "distance" in results[0]

    def test_search_with_metadata_filter(self, chroma_store):
        # Arrange
        chroma_store.put_entity("e1", "function", {"type": "function"})
        chroma_store.put_entity("e2", "class", {"type": "class"})
        chroma_store.put_entity("e3", "another function", {"type": "function"})

        # Act
        results = chroma_store.search("test", where={"type": "function"})

        # Assert
        assert len(results) == 2
        for r in results:
            assert r["metadata"]["type"] == "function"

    def test_search_with_n_results_limit(self, chroma_store):
        # Arrange
        for i in range(5):
            chroma_store.put_entity(f"e{i}", f"text {i}", {"index": i})

        # Act
        results = chroma_store.search("text", n_results=3)

        # Assert
        assert len(results) <= 3

    def test_search_by_embedding(self, chroma_store, sample_embedding):
        # Arrange
        chroma_store.put_entity("e1", "sample text", {"type": "test"})

        # Act
        results = chroma_store.search_by_embedding(sample_embedding)

        # Assert
        assert len(results) > 0
        assert results[0]["id"] == "e1"

    def test_search_empty_collection_returns_empty(self, chroma_store):
        # Act
        results = chroma_store.search("anything")

        # Assert
        assert results == []

    def test_get_entity_returns_stored(self, chroma_store):
        # Arrange
        chroma_store.put_entity("e1", "test content", {"key": "value"})

        # Act
        result = chroma_store.get_entity("e1")

        # Assert
        assert result is not None
        assert result["id"] == "e1"
        assert result["text"] == "test content"
        assert result["metadata"]["key"] == "value"

    def test_get_entity_not_found_returns_none(self, chroma_store):
        # Act
        result = chroma_store.get_entity("nonexistent")

        # Assert
        assert result is None


# =============================================================================
# ChromaStore 删除测试 (3 tests)
# =============================================================================


class TestChromaStoreDelete:
    """测试 ChromaStore 删除操作。"""

    def test_delete_entity_removes_from_store(self, chroma_store):
        # Arrange
        chroma_store.put_entity("e1", "to be deleted", {"x": "1"})

        # Act
        result = chroma_store.delete_entity("e1")

        # Assert
        assert result is True
        assert chroma_store.get_entity("e1") is None

    def test_delete_nonexistent_returns_false(self, chroma_store):
        # Act
        result = chroma_store.delete_entity("nonexistent")

        # Assert
        assert result is False

    def test_delete_by_metadata_removes_matching(self, chroma_store):
        # Arrange
        chroma_store.put_entity("e1", "text 1", {"type": "function"})
        chroma_store.put_entity("e2", "text 2", {"type": "class"})
        chroma_store.put_entity("e3", "text 3", {"type": "function"})

        # Act
        count = chroma_store.delete_entities_by_metadata({"type": "function"})

        # Assert
        assert count == 2
        assert chroma_store.count() == 1
        assert chroma_store.get_entity("e2") is not None


# =============================================================================
# ChromaStore 统计与生命周期测试 (4 tests)
# =============================================================================


class TestChromaStoreLifecycle:
    """测试 ChromaStore 统计与生命周期。"""

    def test_count_returns_total(self, chroma_store):
        # Arrange
        chroma_store.put_entity("e1", "text 1", {"x": "1"})
        chroma_store.put_entity("e2", "text 2", {"x": "2"})
        chroma_store.put_entity("e3", "text 3", {"x": "3"})

        # Act
        count = chroma_store.count()

        # Assert
        assert count == 3

    def test_count_with_filter(self, chroma_store):
        # Arrange
        chroma_store.put_entity("e1", "text 1", {"type": "function"})
        chroma_store.put_entity("e2", "text 2", {"type": "class"})
        chroma_store.put_entity("e3", "text 3", {"type": "function"})

        # Act
        count = chroma_store.count(where={"type": "function"})

        # Assert
        assert count == 2

    def test_context_manager_closes_on_exit(self, mock_ollama):
        # Arrange & Act
        with ChromaStore(persist_dir=None, collection_name="test_ctx") as store:
            embed_fn = store._embed_fn
            assert not embed_fn._client.is_closed

        # Assert
        assert embed_fn._client.is_closed

    def test_close_cleans_up_embed_function(self, mock_ollama):
        # Arrange
        store = ChromaStore(persist_dir=None, collection_name="test_close")
        embed_fn = store._embed_fn
        assert not embed_fn._client.is_closed

        # Act
        store.close()

        # Assert
        assert embed_fn._client.is_closed


# =============================================================================
# 辅助函数测试
# =============================================================================


class TestFormatQueryResults:
    """测试 _format_query_results 函数。"""

    def test_format_empty_results(self):
        # Arrange
        result = {
            "ids": [],
            "documents": [],
            "metadatas": [],
            "distances": [],
        }

        # Act
        formatted = _format_query_results(result)

        # Assert
        assert formatted == []

    def test_format_results_with_data(self):
        # Arrange
        result = {
            "ids": [["id1", "id2"]],
            "documents": [["doc1", "doc2"]],
            "metadatas": [[{"k": "v1"}, {"k": "v2"}]],
            "distances": [[0.1, 0.2]],
        }

        # Act
        formatted = _format_query_results(result)

        # Assert
        assert len(formatted) == 2
        assert formatted[0] == {"id": "id1", "text": "doc1", "metadata": {"k": "v1"}, "distance": 0.1}
        assert formatted[1] == {"id": "id2", "text": "doc2", "metadata": {"k": "v2"}, "distance": 0.2}

    def test_format_results_with_missing_optional_fields(self):
        # Arrange - 当 documents/metadatas/distances 为空列表但 ids 有值时
        result = {
            "ids": [["id1"]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

        # Act
        formatted = _format_query_results(result)

        # Assert
        # ids 有值但其他列表为空，zip 会产生空结果
        assert formatted == []

    def test_format_results_with_empty_inner_list(self):
        # Arrange
        result = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

        # Act
        formatted = _format_query_results(result)

        # Assert
        assert formatted == []
