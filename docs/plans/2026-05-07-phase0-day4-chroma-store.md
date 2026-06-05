# Phase 0 Day 4: ChromaDB 向量存储

## 目标
实现 `ChromaStore` 类，为 LayerKG 提供代码嵌入（embedding）存储和语义搜索能力。基于 Ollama 本地嵌入模型（qwen2.5-coder:0.5b）和 ChromaDB 持久化存储。

## 设计决策

### 1. 向量存储抽象层
ChromaDB 不像 Neo4j 有 `GraphStore` ABC，因为向量存储的访问模式完全不同（put/search vs graph traversal）。`ChromaStore` 是一个独立的具体类，不继承 ABC。

### 2. 嵌入策略
- **嵌入函数**: 使用 Ollama REST API `/api/embed` 端点，不依赖 chromadb 内置的 embedding function
- **文本源**: CodeEntity 的 `source` 字段、DocEntity 的 `content` 字段、ConceptEntity 的 `description` 字段
- **元数据存储**: entity_type、name、file_path 等可过滤字段存为 ChromaDB metadata

### 3. 集合设计
- 单一集合 `layerkg_entities`，通过 metadata `entity_type` 区分实体类型
- 这样可以利用 ChromaDB 的 metadata filtering 做混合检索

### 4. ID 映射
- ChromaDB document ID = LayerKG entity ID（UUID），保持与 Neo4j 节点 ID 一致

## 文件清单

### 新增文件
| 文件 | 预估行数 | 说明 |
|------|---------|------|
| `src/layerkg/chroma_store.py` | ~220 | ChromaStore 实现 |
| `tests/unit/test_chroma_store.py` | ~450 | 单元测试（mock Ollama + 内存 ChromaDB） |

### 修改文件
| 文件 | 变更 |
|------|------|
| `src/layerkg/exceptions.py` | 添加 `EmbeddingError` 异常类 |
| `pyproject.toml` | 添加 `httpx` 依赖（用于 Ollama API 调用） |

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

### Task 2: OllamaEmbeddingFunction (chroma_store.py 上半部分)
**目标**: 实现嵌入函数，通过 Ollama REST API 生成向量

**类设计**:
```python
class OllamaEmbeddingFunction:
    """通过 Ollama REST API 生成嵌入向量。"""

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.Client(timeout=30.0)

    def __call__(self, input: list[str]) -> list[list[float]]:
        """批量生成嵌入向量。"""
        # POST {base_url}/api/embed
        # body: {"model": model, "input": input}
        # 返回 embeddings 列表

    def close(self) -> None:
        self._client.close()

    @property
    def dimension(self) -> int:
        """返回嵌入维度（首次调用时检测）。"""
        # 调用一次空嵌入获取维度
```

**关键点**:
- httpx.Client 同步调用（Phase 0 不用 async）
- 批量嵌入：一次 API 调用传入多个文本
- 错误处理：HTTP 错误、连接失败 → `EmbeddingError`
- dimension 属性：首次调用缓存，避免重复请求

---

### Task 3: ChromaStore 核心 (chroma_store.py 下半部分)
**目标**: 实现 ChromaStore 类

**类设计**:
```python
class ChromaStore:
    """ChromaDB 向量存储。"""

    def __init__(
        self,
        persist_dir: str = ".chroma",
        ollama_url: str = "http://localhost:11434",
        embedding_model: str = "qwen2.5-coder:0.5b",
        collection_name: str = "layerkg_entities",
    ) -> None:
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._embed_fn = OllamaEmbeddingFunction(ollama_url, embedding_model)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    # --- 写入操作 ---

    def put_entity(self, entity_id: str, text: str, metadata: dict) -> None:
        """存储实体嵌入。"""
        # 生成 embedding → collection.upsert(ids, embeddings, documents, metadatas)

    def put_entities_batch(self, items: list[tuple[str, str, dict]]) -> None:
        """批量存储实体嵌入。"""
        # 批量生成 embedding → collection.upsert

    # --- 查询操作 ---

    def search(
        self,
        query_text: str,
        n_results: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        """语义搜索。"""
        # 生成 query embedding → collection.query

    def search_by_embedding(
        self,
        embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        """按嵌入向量搜索。"""

    def get_entity(self, entity_id: str) -> dict | None:
        """按 ID 获取实体。"""

    # --- 删除操作 ---

    def delete_entity(self, entity_id: str) -> bool:
        """删除实体嵌入。"""

    def delete_entities_by_metadata(self, where: dict) -> int:
        """按 metadata 条件批量删除，返回删除数量。"""

    # --- 统计 ---

    def count(self, where: dict | None = None) -> int:
        """统计实体数量。"""

    # --- 生命周期 ---

    def close(self) -> None:
        """关闭资源。"""
        self._embed_fn.close()

    def __enter__(self) -> ChromaStore: ...
    def __exit__(self, *exc) -> None: self.close()
```

**关键点**:
- `put_entity`: 单个 upsert，文本为空时抛 `ValueError`
- `put_entities_batch`: 过滤空文本，一次 embed 调用
- `search`: 最常用方法，返回 `[{"id": ..., "text": ..., "metadata": ..., "distance": ...}]`
- `where`: 直接透传 ChromaDB 的 metadata filter 语法（`{"entity_type": "function"}`）
- `delete_entities_by_metadata`: 先 collection.get 找 ID，再 collection.delete
- 支持 context manager (`with` 语句)

---

### Task 4: 单元测试 (test_chroma_store.py)
**目标**: 全面覆盖，mock Ollama API，使用 ChromaDB 内存模式

**测试 fixture 策略**:
```python
@pytest.fixture
def mock_ollama():
    """Mock Ollama /api/embed 端点，返回固定维度向量。"""
    with unittest.mock.patch("httpx.Client") as mock_client:
        # 配置 mock 返回 8 维向量（测试用小维度）
        ...

@pytest.fixture
def chroma_store(tmp_path, mock_ollama):
    """创建使用临时目录的 ChromaStore 实例。"""
    return ChromaStore(persist_dir=str(tmp_path / "chroma"))
```

**测试用例清单** (~25 tests):

**OllamaEmbeddingFunction (6 tests)**:
1. `test_embed_single_text_returns_vector` — 单文本嵌入
2. `test_embed_batch_returns_vectors` — 批量嵌入
3. `test_embed_connection_error_raises_embedding_error` — 连接失败
4. `test_embed_http_error_raises_embedding_error` — HTTP 500
5. `test_embed_dimension_cached` — dimension 属性缓存
6. `test_embed_close_closes_client` — close 方法

**ChromaStore 写入 (5 tests)**:
7. `test_put_entity_stores_successfully` — 正常存储
8. `test_put_entity_empty_text_raises_value_error` — 空文本校验
9. `test_put_entity_upsert_overwrites` — 重复 ID 覆盖
10. `test_put_entities_batch_stores_all` — 批量存储
11. `test_put_entities_batch_skips_empty_text` — 批量跳过空文本

**ChromaStore 查询 (6 tests)**:
12. `test_search_returns_results` — 基本搜索
13. `test_search_with_metadata_filter` — metadata 过滤
14. `test_search_with_n_results` — 限制返回数量
15. `test_search_by_embedding` — 按向量搜索
16. `test_search_empty_collection_returns_empty` — 空集合搜索
17. `test_get_entity_returns_stored` — 按 ID 获取

**ChromaStore 删除 (3 tests)**:
18. `test_delete_entity_removes_from_store` — 单个删除
19. `test_delete_nonexistent_returns_false` — 删除不存在的 ID
20. `test_delete_by_metadata_removes_matching` — 批量按条件删除

**ChromaStore 统计与生命周期 (4 tests)**:
21. `test_count_returns_total` — 总数统计
22. `test_count_with_filter` — 带过滤条件的统计
23. `test_context_manager` — `with` 语句支持
24. `test_close_cleans_up` — close 方法清理

---

### Task 5: ruff 格式化 + 全量测试验证
**命令**:
```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest tests/ -v
```

---

## 依赖关系
```
Task 1 (异常+依赖)
  └── Task 2 (OllamaEmbeddingFunction)
       └── Task 3 (ChromaStore)
            └── Task 4 (单元测试)
                 └── Task 5 (验证)
```

## 预期结果
- `src/layerkg/chroma_store.py` (~220 行)
- `tests/unit/test_chroma_store.py` (~450 行, 24 tests)
- 全量测试 130 + 1(exception) + 24 = 155 tests
- ruff check/format clean
- 覆盖率 > 90%

## Ollama /api/embed API 参考
```json
// POST http://<YOUR_SERVER_IP>:11434/api/embed
// Request:
{"model": "qwen2.5-coder:0.5b", "input": ["text1", "text2"]}
// Response:
{"model": "qwen2.5-coder:0.5b", "embeddings": [[0.1, 0.2, ...], [0.3, 0.4, ...]]}
```

## ChromaDB 1.5 API 要点
- `chromadb.PersistentClient(path=...)` — 持久化客户端
- `chromadb.Client()` — 内存客户端（测试用）
- `collection.upsert(ids, embeddings, documents, metadatas)` — 插入/更新
- `collection.query(query_embeddings, n_results, where)` — 向量搜索
- `collection.get(ids, where)` — 按 ID 或条件获取
- `collection.delete(ids, where)` — 删除
- `collection.count()` — 总数
