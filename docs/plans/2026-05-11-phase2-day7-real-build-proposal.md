# Phase 2 Day 7 方案：真实全量构建验证（修订版 v2）

## 审核反馈（8.2 → 目标 9.0+）

| # | 问题 | 优先级 | 本版修复 |
|---|------|--------|---------|
| 1 | Config.from_env() 缺 build_doc_extensions + build_skip_dirs 解析 | P0 | ✅ Task 2a |
| 2 | skip_dirs Builder/Config 不同步（Builder 多 venv/.pytest_cache，Config 多 .ruff_cache） | P0 | ✅ Task 2b |
| 3 | DocEntity 截断 2000 字符硬编码 | P1 | ✅ Task 2c |
| 4 | batch_size 写死 50，不同环境不灵活 | P2 | ✅ Task 1 |

## Task 1：向量写入分批（🔴 关键）

**文件**: `src/layerkg/chroma_store.py`

`put_entities_batch()` 增加分批 + batch_size 从 config 读取：

```python
def put_entities_batch(
    self,
    items: list[tuple[str, str, dict[str, Any]]],
    batch_size: int = 50,
) -> None:
    ids, docs, metas = [], [], []
    for entity_id, text, metadata in items:
        if text and text.strip():
            ids.append(entity_id)
            docs.append(text)
            metas.append(_sanitize_metadata(metadata))
    if not ids:
        return
    total_batches = -(-len(ids) // batch_size)  # ceil div
    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i : i + batch_size]
        batch_docs = docs[i : i + batch_size]
        batch_metas = metas[i : i + batch_size]
        self._collection.upsert(
            ids=batch_ids, documents=batch_docs, metadatas=batch_metas
        )
        self._logger.debug(
            "Put batch (%d/%d): %d entities",
            i // batch_size + 1,
            total_batches,
            len(batch_ids),
        )
```

**测试**：新增 `test_put_entities_batch_splits` — 传入 120 条，验证分 3 批调用 upsert（mock collection）。

## Task 2：Builder 接入 Config 字段

### Task 2a：Config.from_env() 补全（P0）

**文件**: `src/layerkg/config.py`

```python
@classmethod
def from_env(cls) -> LayerKGConfig:
    # ... 现有字段 ...
    # 新增解析
    doc_ext_str = os.getenv("LAYERKG_BUILD_DOC_EXTENSIONS")
    build_doc_extensions = (
        [e.strip() for e in doc_ext_str.split(",") if e.strip()]
        if doc_ext_str
        else list(cls.__dataclass_fields__["build_doc_extensions"].default_factory())
    )
    skip_dirs_str = os.getenv("LAYERKG_BUILD_SKIP_DIRS")
    build_skip_dirs = (
        {d.strip() for d in skip_dirs_str.split(",") if d.strip()}
        if skip_dirs_str
        else cls.__dataclass_fields__["build_skip_dirs"].default_factory()
    )
    return cls(
        # ... 现有 ...
        build_doc_extensions=build_doc_extensions,
        build_skip_dirs=build_skip_dirs,
    )
```

**测试**：新增 `test_from_env_doc_extensions` + `test_from_env_skip_dirs`。

### Task 2b：skip_dirs 同步（P0）

**统一后的 skip_dirs**（Config 默认值 + Builder 共用）：

```python
{
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".ruff_cache",     # 新增
    ".pytest_cache",   # 从 Builder 移入
    "node_modules",
    ".venv",
    "venv",            # 从 Builder 移入
    "site",
    ".tox",
    "dist",
    "build",
    "*.egg-info",
}
```

**文件改动**：
- `config.py`：默认值加入 `.ruff_cache`（已有）+ `.pytest_cache` + `venv`
- `builder.py:26`：删除 `_DOC_EXTENSIONS` 常量
- `builder.py:466-479`：删除硬编码 skip_dirs，改为 `skip_dirs = self._config.build_skip_dirs`
- `builder.py:490`：`for ext in _DOC_EXTENSIONS` → `for ext in self._config.build_doc_extensions`

### Task 2c：文档截断配置化（P1）

**文件**: `src/layerkg/config.py` + `src/layerkg/builder.py`

Config 新增字段：
```python
build_doc_max_length: int = 2000
```

Builder 中：
```python
# 之前
text = (doc.content or "")[:2000]
# 之后
text = (doc.content or "")[: self._config.build_doc_max_length]
```

from_env() 解析：
```python
build_doc_max_length=int(os.getenv("LAYERKG_BUILD_DOC_MAX_LENGTH", "2000")),
```

## Task 3：真实全量构建验证

不新增代码，验证流程：
1. 清空 Neo4j 所有节点 + ChromaDB collection
2. 运行 `layerkg build . --verbose-build --skip-semantic --skip-clustering`
3. 检查 Stage 1-2-5 是否全部通过
4. 记录产出数据

## Task 4：数据质量验证

构建完成后验证：
1. Neo4j Cypher 查各类型实体数量
2. ChromaDB search 验证向量可搜索
3. 输出验证报告

## 依赖关系

```
Task 1 (分批) ──┐
Task 2a (from_env) ──┤── Task 3 (真实构建) ── Task 4 (数据验证)
Task 2b (skip_dirs) ──┤
Task 2c (截断配置) ──┘
```

## 不改什么

- 不改语义提取逻辑（_stage_semantic）
- 不改 CLI 接口
- 不改 doc_parser
- Task 3/4 不新增代码

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Ollama 远程延迟 | 分批 50 条，单批 timeout 30s |
| Neo4j 大量写入 | 已有 merge_node 批量操作 |
| 构建超时 | 先 skip-semantic 跑 Stage 1-2-5 |
| from_env() 默认值取不到 | 用 __dataclass_fields__ 读取 default_factory |
