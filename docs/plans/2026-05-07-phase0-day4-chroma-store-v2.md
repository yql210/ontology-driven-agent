# Phase 0 Day 4: ChromaDB 向量存储 (V2)

## 目标
实现 `ChromaStore` 类，为 LayerKG 提供代码嵌入（embedding）存储和语义搜索能力。基于 Ollama 本地嵌入模型（qwen2.5-coder:0.5b）和 ChromaDB 持久化存储。

## 设计决策

### 1. 向量存储抽象层
ChromaDB 不像 Neo4j 有 `GraphStore` ABC，因为向量存储的访问模式完全不同（put/search vs graph traversal）。`ChromaStore` 是一个独立的具体类，不继承 ABC。

### 2. 嵌入策略（V2 修订）
- **嵌入函数**: 实现 ChromaDB 的 `EmbeddingFunction` 协议（`__call__(input) -> list[list[float]]`），让 ChromaDB 内部自动调用
- **API 端点**: Ollama REST API `/api/embed`
- **优势**: ChromaDB 在 `add`/`upsert`/`query` 时自动调用 embedding function，无需手动生成向量
- **文本源**: CodeEntity 的 `source` 字段、DocEntity 的 `content` 字段、ConceptEntity 的 `description` 字段

### 3. 集合设计
- 单一集合 `layerkg_entities`，通过 metadata `entity_type` 区分实体类型
- ChromaDB 的 metadata filtering 做混合检索

### 4. ID 映射
- ChromaDB document ID = LayerKG entity ID（UUID），保持与 Neo4j 节点 ID 一致

### 5. Metadata 类型安全（V2 新增）
- ChromaDB metadata 只接受 `str | int | float | bool` 值
- `put_entity` 入口统一过滤/转换 metadata，非兼容类型转为 str

## 文件清单

### 新增文件
| 文件 | 预估行数 | 说明 |
|------|---------|------|
| `src/layerkg/chroma_store.py` | ~260 | OllamaEmbeddingFunction + ChromaStore 实现 |
| `tests/unit/test_chroma_store.py` | ~520 | 单元测试（mock Ollama + 内存 ChromaDB） |

### 修改文件
| 文件 | 变更 |
|------|------|
| `src/layerkg/exceptions.py` | 添加 `EmbeddingError` 异常类 |
| `pyproject.toml` | 添加 `httpx` 依赖 |

不修改已有的 schema.py、config.py、graph_store.py、neo4j_store.py。

## 实现计划

### Task 1: 异常类 + 依赖准备 (5 min)
**目标**: 添加 `EmbeddingError` 异常，安装 `httpx` 依赖

**文件**: `src/layerkg/exceptions.py`
```python
class EmbeddingError(LayerKGError):
    """嵌入向量生成失败。"""
```

**命令**: `uv add httpx`

**测试**: `test_exceptions.py` 中添加 `test_embedding_error_is_layerkg_error`

---

### Task 2: OllamaEmbeddingFunction — 实现 ChromaDB EmbeddingFunction 协议 (chroma_store.py 上半部分)

**关键设计（V2 修订）**:
- 实现 `chromadb.EmbeddingFunction` 协议（只需 `__call__(input: list[str]) -> list[list[float]]`）
- 将此对象传给 `chromadb.Client.get_or_create_collection(embedding_function=...)`
- ChromaDB 在 `add`/`upsert`/`query` 时自动调用，无需手动生成向量

```python
import chromadb
from chromadb.api.types import EmbeddingFunction

class OllamaEmbeddingFunction(EmbeddingFunction):
    """通过 Ollama REST API 生成嵌入向量，实现 ChromaDB EmbeddingFunction 协议。"""

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=30.0)
        self._dimension: int | None = None
        self._logger = logging.getLogger(__name__)

    def __call__(self, input: list[str]) -> list[list[float]]:
        """批量生成嵌入向量（ChromaDB 自动调用）。"""
        if not input:
            return []
        try:
            response = self._client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": input},
            )
            response.raise_for_status()
            data = response.json()
            embeddings = data["embeddings"]
            if self._dimension is None and embeddings:
                self._dimension = len(embeddings[0])
            return embeddings
        except httpx.HTTPError as e:
            raise EmbeddingError(f"Ollama embedding failed: {e}") from e

    @property
    def dimension(self) -> int:
        """返回嵌入维度。首次调用时检测并缓存。"""
        if self._dimension is None:
            result = self(["test"])
            if not result:
                raise EmbeddingError("Cannot determine embedding dimension")
        return self._dimension  # type: ignore[return-value]

    def close(self) -> None:
        """关闭 HTTP 客户端。"""
        self._client.close()
```

**关键点**:
- 空输入直接返回空列表（边界处理）
- HTTP 错误 → `EmbeddingError`
- dimension 属性延迟检测 + 缓存
- logging 在关键路径记录

---

### Task 3: Metadata 工具函数 (chroma_store.py 中间部分)

```python
_VALID_METADATA_TYPES = (str, int, float, bool)

def _sanitize_metadata(metadata: dict) -> dict[str, str | int | float | bool]:
    """过滤 metadata，只保留 ChromaDB 兼容类型。"""
    return {
        k: v if isinstance(v, _VALID_METADATA_TYPES) else str(v)
        for k, v in metadata.items()
    }
```

---

### Task 4: ChromaStore 核心 (chroma_store.py 下半部分)

```python
class ChromaStore:
    """ChromaDB 向量存储。"""

    def __init__(
        self,
        persist_dir: str | None = None,
        ollama_url: str = "http://localhost:11434",
        embedding_model: str = "qwen2.5-coder:0.5b",
        collection_name: str = "layerkg_entities",
    ) -> None:
        # persist_dir=None 时使用内存模式（测试用）
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

    def put_entity(self, entity_id: str, text: str, metadata: dict) -> None:
        """存储实体嵌入。ChromaDB 自动调用 embedding function。"""
        if not text or not text.strip():
            raise ValueError("text cannot be empty or whitespace-only")
        clean_meta = _sanitize_metadata(metadata)
        self._collection.upsert(
            ids=[entity_id],
            documents=[text],
            metadatas=[clean_meta],
        )
        self._logger.debug("Put entity %s", entity_id)

    def put_entities_batch(self, items: list[tuple[str, str, dict]]) -> None:
        """批量存储实体嵌入。跳过空文本。"""
        ids, docs, metas = [], [], []
        for entity_id, text, metadata in items:
            if text and text.strip():
                ids.append(entity_id)
                docs.append(text)
                metas.append(_sanitize_metadata(metadata))
        if not ids:
            return
        self._collection.upsert(ids=ids, documents=docs, metadatas=metas)
        self._logger.debug("Put batch: %d entities", len(ids))

    # --- 查询操作 ---

    def search(
        self,
        query_text: str,
        n_results: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        """语义搜索。ChromaDB 自动生成 query embedding。"""
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
        where: dict | None = None,
    ) -> list[dict]:
        """按嵌入向量搜索（跳过 embedding function）。"""
        result = self._collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=where,
        )
        return _format_query_results(result)

    def get_entity(self, entity_id: str) -> dict | None:
        """按 ID 获取实体。"""
        result = self._collection.get(ids=[entity_id])
        if not result["ids"]:
            return None
        return {
            "id": result["ids"][0],
            "text": result["documents"][0] if result["documents"] else None,
            "metadata": result["metadatas"][0] if result["metadatas"] else {},
        }

    # --- 删除操作 ---

    def delete_entity(self, entity_id: str) -> bool:
        """删除实体嵌入。"""
        existing = self.get_entity(entity_id)
        if existing is None:
            return False
        self._collection.delete(ids=[entity_id])
        return True

    def delete_entities_by_metadata(self, where: dict) -> int:
        """按 metadata 条件批量删除，返回删除数量。"""
        result = self._collection.get(where=where)
        ids = result["ids"]
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    # --- 统计 ---

    def count(self, where: dict | None = None) -> int:
        """统计实体数量。"""
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
```

**辅助函数**:
```python
def _format_query_results(result: dict) -> list[dict]:
    """格式化 ChromaDB query 返回值。"""
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
        )
    ]
```

---

### Task 5: 单元测试 (test_chroma_store.py)

**测试 fixture 策略（V2 修订 — 资源清理）**:
```python
@pytest.fixture
def mock_ollama():
    """Mock httpx.Client.post，返回固定 8 维向量。"""
    # Mock httpx.Client 构造 + post 方法

@pytest.fixture
def chroma_store(tmp_path, mock_ollama):
    """创建内存模式 ChromaStore（persist_dir=None）。"""
    store = ChromaStore(persist_dir=None)
    yield store  # V2: 确保清理
    store.close()
```

**使用 `persist_dir=None` 内存模式**，避免临时文件残留。

**测试用例清单 (~27 tests)**:

**EmbeddingError (1 test)**:
1. `test_embedding_error_is_layerkg_error`

**OllamaEmbeddingFunction (7 tests)**:
2. `test_embed_single_text_returns_vector`
3. `test_embed_batch_returns_vectors`
4. `test_embed_empty_input_returns_empty_list` — V2 新增
5. `test_embed_connection_error_raises_embedding_error`
6. `test_embed_http_error_raises_embedding_error`
7. `test_embed_dimension_cached_after_first_call`
8. `test_embed_close_closes_http_client`

**Metadata 工具函数 (2 tests)**:
9. `test_sanitize_metadata_keeps_valid_types`
10. `test_sanitize_metadata_converts_invalid_types_to_str`

**ChromaStore 写入 (5 tests)**:
11. `test_put_entity_stores_successfully`
12. `test_put_entity_empty_text_raises_value_error`
13. `test_put_entity_whitespace_only_raises_value_error` — V2 新增
14. `test_put_entity_upsert_overwrites_existing`
15. `test_put_entities_batch_stores_all`
16. `test_put_entities_batch_skips_empty_text`
17. `test_put_entities_batch_empty_list_is_noop` — V2 新增

**ChromaStore 查询 (6 tests)**:
18. `test_search_returns_results`
19. `test_search_with_metadata_filter`
20. `test_search_with_n_results_limit`
21. `test_search_by_embedding`
22. `test_search_empty_collection_returns_empty`
23. `test_get_entity_returns_stored`

**ChromaStore 删除 (3 tests)**:
24. `test_delete_entity_removes_from_store`
25. `test_delete_nonexistent_returns_false`
26. `test_delete_by_metadata_removes_matching`

**ChromaStore 统计与生命周期 (4 tests)**:
27. `test_count_returns_total`
28. `test_count_with_filter`
29. `test_context_manager_closes_on_exit`
30. `test_close_cleans_up_embed_function`

---

### Task 6: ruff 格式化 + 全量测试验证
```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest tests/ -v
```

---

## 依赖关系
```
Task 1 (异常+依赖)
  └── Task 2 (OllamaEmbeddingFunction, 实现 EmbeddingFunction 协议)
       └── Task 3 (Metadata 工具函数)
            └── Task 4 (ChromaStore 核心)
                 └── Task 5 (单元测试, 27 tests)
                      └── Task 6 (验证)
```

## V1 → V2 修订摘要
| 修订项 | V1 | V2 |
|--------|----|----|
| 嵌入函数 | 独立类，手动生成向量再传 ChromaDB | 实现 `EmbeddingFunction` 协议，ChromaDB 自动调用 |
| 测试 fixture | tmp_path + PersistentClient，无清理 | `persist_dir=None` 内存模式，yield + close 清理 |
| Metadata 校验 | 无，直接透传 | `_sanitize_metadata` 过滤非兼容类型 |
| 测试数量 | 24 tests | 27 tests（+空输入、纯空白、空列表、metadata类型） |
| Logging | 未提及 | 添加 `logging.getLogger(__name__)` 关键路径 |
| 行数估计 | ~220 + ~450 | ~260 + ~520 |

## 预期结果
- `src/layerkg/chroma_store.py` (~260 行)
- `tests/unit/test_chroma_store.py` (~520 行, 27 tests)
- 全量测试 130 + 1(exception) + 27 = 158 tests
- ruff check/format clean
- 覆盖率 > 90%

## Ollama /api/embed API 参考
```json
// POST http://localhost:11434/api/embed
// Request:
{"model": "qwen2.5-coder:0.5b", "input": ["text1", "text2"]}
// Response:
{"model": "qwen2.5-coder:0.5b", "embeddings": [[0.1, 0.2, ...], [0.3, 0.4, ...]]}
```

## ChromaDB 1.5 API 要点
- `chromadb.PersistentClient(path=...)` — 持久化客户端
- `chromadb.Client()` — 内存客户端（测试用）
- `get_or_create_collection(name, embedding_function=..., metadata=...)` — 绑定嵌入函数
- `collection.upsert(ids, documents, metadatas)` — 自动调用 embedding function 生成向量
- `collection.query(query_texts, n_results, where)` — 自动生成 query embedding
- `collection.query(query_embeddings, n_results, where)` — 跳过 embedding，直接搜索
- `collection.get(ids, where)` — 按 ID 或条件获取
- `collection.delete(ids, where)` — 删除
- `collection.count()` — 总数
- EmbeddingFunction 协议: `__call__(input: list[str]) -> list[list[float]]`
