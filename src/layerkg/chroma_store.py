from __future__ import annotations

import logging
from typing import Any

import chromadb
import httpx
from chromadb.api.types import EmbeddingFunction

from layerkg.exceptions import EmbeddingError

_VALID_METADATA_TYPES = (str, int, float, bool)

_logger = logging.getLogger(__name__)


class OllamaEmbeddingFunction(EmbeddingFunction):
    """通过 Ollama REST API 生成嵌入向量，实现 ChromaDB EmbeddingFunction 协议。"""

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=30.0)
        self._dimension: int | None = None
        self._logger = logging.getLogger(__name__)

    _EMBED_BATCH_SIZE = 10

    def _embed_one(self, text: str) -> list[float] | None:
        """单条文本 embedding，失败返回 None。"""
        try:
            response = self._client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": [text]},
                timeout=60.0,
            )
            response.raise_for_status()
            return response.json()["embeddings"][0]
        except httpx.HTTPError as e:
            self._logger.warning("Embed single text failed (len=%d): %s", len(text), e)
            return None

    def _embed_batch(self, texts: list[str], *, fallback: bool = True) -> list[list[float]]:
        """单批次调用 Ollama embed API，失败时降级为逐条 embedding。

        Args:
            texts: 待嵌入文本列表。
            fallback: 批次失败时是否降级逐条重试。为 False 时直接抛出原始异常。
        """
        try:
            response = self._client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": texts},
                timeout=60.0,
            )
            response.raise_for_status()
            return response.json()["embeddings"]
        except httpx.HTTPError as original_err:
            if not fallback:
                raise
            self._logger.warning("Batch embed (%d items) failed, falling back to single-item", len(texts))
            results: list[list[float]] = []
            any_success = False
            for text in texts:
                emb = self._embed_one(text)
                if emb is not None:
                    any_success = True
                    results.append(emb)
                else:
                    results.append([0.0] * (self._dimension or 384))
            if not any_success:
                raise original_err
            return results

    def __call__(self, input: list[str]) -> list[list[float]]:
        """批量生成嵌入向量（ChromaDB 自动调用）。

        自动将大批次拆分为小批次（每批 _EMBED_BATCH_SIZE 条），
        逐批调用 Ollama API 后合并结果。批次失败时降级为逐条处理。

        Args:
            input: 待嵌入的文本列表。

        Returns:
            嵌入向量列表，每个向量是 float 列表。

        Raises:
            EmbeddingError: 当 Ollama API 调用失败时。
        """
        if not input:
            return []
        all_embeddings: list[list[float]] = []
        try:
            for i in range(0, len(input), self._EMBED_BATCH_SIZE):
                batch = input[i : i + self._EMBED_BATCH_SIZE]
                embeddings = self._embed_batch(batch)
                if self._dimension is None and embeddings:
                    self._dimension = len(embeddings[0])
                all_embeddings.extend(embeddings)
            return all_embeddings
        except httpx.HTTPError as e:
            raise EmbeddingError(f"Ollama embedding failed: {e}") from e

    @property
    def dimension(self) -> int:
        """返回嵌入维度。

        首次调用时检测并缓存维度。

        Returns:
            嵌入向量的维度。

        Raises:
            EmbeddingError: 当无法确定嵌入维度时。
        """
        if self._dimension is None:
            result = self(["test"])
            if not result:
                raise EmbeddingError("Cannot determine embedding dimension")
        return self._dimension  # type: ignore[return-value]

    def close(self) -> None:
        """关闭 HTTP 客户端。"""
        self._client.close()


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """过滤 metadata，只保留 ChromaDB 兼容类型。

    ChromaDB metadata 只接受 str | int | float | bool 类型，
    非兼容类型会被转换为字符串。

    Args:
        metadata: 原始 metadata 字典。

    Returns:
        过滤后的 metadata 字典。
    """
    return {k: v if isinstance(v, _VALID_METADATA_TYPES) else str(v) for k, v in metadata.items()}


def _format_query_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    """格式化 ChromaDB query 返回值。

    Args:
        result: ChromaDB query 返回的原始结果。

    Returns:
        格式化后的结果列表，每个元素包含 id, text, metadata, distance。
    """
    if not result["ids"] or not result["ids"][0]:
        return []
    return [
        {
            "id": rid,
            "text": doc,
            "metadata": meta,
            "distance": dist,
        }
        for rid, doc, meta, dist in zip(
            result["ids"][0],
            result["documents"][0] if result["documents"] else [],
            result["metadatas"][0] if result["metadatas"] else [],
            result["distances"][0] if result["distances"] else [],
            strict=False,
        )
    ]


class ChromaStore:
    """ChromaDB 向量存储。"""

    def __init__(
        self,
        persist_dir: str | None = None,
        ollama_url: str = "http://localhost:11434",
        embedding_model: str = "qwen2.5-coder:0.5b",
        collection_name: str = "layerkg_entities",
    ) -> None:
        """初始化 ChromaStore。

        Args:
            persist_dir: 持久化目录路径，None 时使用内存模式（测试用）。
            ollama_url: Ollama API 地址。
            embedding_model: 嵌入模型名称。
            collection_name: ChromaDB 集合名称。
        """
        if persist_dir:
            self._client = chromadb.PersistentClient(path=persist_dir)
        else:
            self._client = chromadb.Client()
        self._embed_fn = OllamaEmbeddingFunction(ollama_url, embedding_model)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self._logger = logging.getLogger(__name__)

    # --- 写入操作 ---

    def put_entity(self, entity_id: str, text: str, metadata: dict[str, Any]) -> None:
        """存储实体嵌入。

        ChromaDB 会自动调用 embedding function 生成向量。

        Args:
            entity_id: 实体 ID。
            text: 待嵌入的文本内容。
            metadata: 实体元数据。

        Raises:
            ValueError: 当 text 为空或纯空白时。
        """
        if not text or not text.strip():
            raise ValueError("text cannot be empty or whitespace-only")
        clean_meta = _sanitize_metadata(metadata)
        self._collection.upsert(
            ids=[entity_id],
            documents=[text],
            metadatas=[clean_meta],
        )
        self._logger.debug("Put entity %s", entity_id)

    def put_entities_batch(
        self,
        items: list[tuple[str, str, dict[str, Any]]],
        batch_size: int = 20,
    ) -> None:
        """批量存储实体嵌入。

        空文本项会被跳过。数据按 batch_size 分批写入。
        单批次失败时跳过并记录警告，不中断整体流程。

        Args:
            items: (entity_id, text, metadata) 元组列表。
            batch_size: 每批次写入的实体数量，默认 20。
        """
        ids, docs, metas = [], [], []
        for entity_id, text, metadata in items:
            if text and text.strip():
                ids.append(entity_id)
                docs.append(text)
                metas.append(_sanitize_metadata(metadata))
        if not ids:
            return
        total_batches = -(-len(ids) // batch_size)  # ceil division
        failed = 0
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i : i + batch_size]
            batch_docs = docs[i : i + batch_size]
            batch_metas = metas[i : i + batch_size]
            try:
                self._collection.upsert(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            except Exception as e:
                failed += len(batch_ids)
                self._logger.warning("Batch %d/%d failed (%d items): %s", i // batch_size + 1, total_batches, len(batch_ids), e)
            else:
                self._logger.debug(
                    "Put batch (%d/%d): %d entities",
                    i // batch_size + 1,
                    total_batches,
                    len(batch_ids),
                )
        if failed:
            self._logger.warning("Vector write: %d/%d items failed", failed, len(ids))

    # --- 查询操作 ---

    def search(
        self,
        query_text: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """语义搜索。

        ChromaDB 会自动生成 query embedding。

        Args:
            query_text: 查询文本。
            n_results: 返回结果数量。
            where: metadata 过滤条件。

        Returns:
            搜索结果列表，每个元素包含 id, text, metadata, distance。
        """
        result = self._collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where,
        )
        return _format_query_results(result)

    def search_by_embedding(
        self,
        embedding: list[float],
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """按嵌入向量搜索。

        跳过 embedding function，直接使用提供的向量搜索。

        Args:
            embedding: 预生成的嵌入向量。
            n_results: 返回结果数量。
            where: metadata 过滤条件。

        Returns:
            搜索结果列表，每个元素包含 id, text, metadata, distance。
        """
        result = self._collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=where,
        )
        return _format_query_results(result)

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """按 ID 获取实体。

        Args:
            entity_id: 实体 ID。

        Returns:
            包含 id, text, metadata 的字典，不存在时返回 None。
        """
        result = self._collection.get(ids=[entity_id])
        if not result["ids"]:
            return None
        return {
            "id": result["ids"][0],
            "text": result["documents"][0] if result["documents"] else None,
            "metadata": result["metadatas"][0] if result["metadatas"] else {},
        }

    # --- 清空操作 ---

    def clear_all(self) -> int:
        """删除所有向量数据，返回删除的数量。分批删除以避免 SQL 变量上限。"""
        count = self._collection.count()
        if count > 0:
            batch_size = 5000
            deleted = 0
            while deleted < count:
                ids = self._collection.get(limit=batch_size)["ids"]
                if not ids:
                    break
                self._collection.delete(ids=ids)
                deleted += len(ids)
        return count

    # --- 删除操作 ---

    def delete_entity(self, entity_id: str) -> bool:
        """删除实体嵌入。

        Args:
            entity_id: 实体 ID。

        Returns:
            删除成功返回 True，实体不存在返回 False。
        """
        existing = self.get_entity(entity_id)
        if existing is None:
            return False
        self._collection.delete(ids=[entity_id])
        return True

    def delete_entities_by_metadata(self, where: dict[str, Any]) -> int:
        """按 metadata 条件批量删除。

        Args:
            where: metadata 过滤条件。

        Returns:
            删除的实体数量。
        """
        result = self._collection.get(where=where)
        ids = result["ids"]
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    # --- 统计 ---

    def count(self, where: dict[str, Any] | None = None) -> int:
        """统计实体数量。

        Args:
            where: metadata 过滤条件，None 时返回总数。

        Returns:
            符合条件的实体数量。
        """
        if where:
            result = self._collection.get(where=where)
            return len(result["ids"])
        return self._collection.count()

    # --- 生命周期 ---

    def close(self) -> None:
        """关闭资源。"""
        self._embed_fn.close()

    def __enter__(self) -> ChromaStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
