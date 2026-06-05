# Phase 2 Day 7 实施计划

基于方案 v2（9.2/10 通过）+ 实施计划审核反馈。

## 审核反馈（预估 9.5/10）

| # | 反馈 | 修复 |
|---|------|------|
| 1 | `_scan_files` 是 `@staticmethod`，不能访问 `self._config`，需改为实例方法 | ✅ Task 2b |
| 2 | `build_doc_max_length` 需先在 dataclass 定义 | ✅ Task 2a |
| 3 | batch_size=0 边界测试 | ✅ Task 1 |
| 4 | content 为 None/空测试 | ✅ Task 2c |

## Task 1：向量写入分批

### 文件：`src/layerkg/chroma_store.py`

1. 修改 `put_entities_batch()` 方法签名，新增 `batch_size: int = 50` 参数
2. 原有过滤逻辑不变（跳过空文本）
3. 新增分批循环：按 `batch_size` 切片 ids/docs/metas，逐批 upsert
4. 添加 debug 日志：`(batch_idx/total_batches, count)`

### 文件：`tests/unit/test_chroma_store.py`

5. 新增 `test_put_entities_batch_splits`：
   - 构造 120 条 items，batch_size=50
   - mock collection.upsert
   - 验证调用 3 次，每次分别 50/50/20 条
6. 新增 `test_put_entities_batch_empty`：空 items 不调用 upsert
7. 新增 `test_put_entities_batch_size_1`：batch_size=1，120 条 → 120 次调用

## Task 2a：Config.from_env() 补全

### 文件：`src/layerkg/config.py`

1. 在 `from_env()` 中新增 `build_doc_extensions` 解析：
   - 环境变量 `LAYERKG_BUILD_DOC_EXTENSIONS`，逗号分隔
   - 未设置时用硬编码默认值 `[".md", ".rst"]`（不用 `__dataclass_fields__`）
2. 新增 `build_skip_dirs` 解析：
   - 环境变量 `LAYERKG_BUILD_SKIP_DIRS`，逗号分隔
   - 未设置时用硬编码默认值（与 dataclass 默认值一致）
3. 新增 `build_doc_max_length` 字段（`int = 2000`）及 from_env 解析

### 文件：`tests/unit/test_config.py`

4. 新增 `test_from_env_doc_extensions`：设置 `LAYERKG_BUILD_DOC_EXTENSIONS=".md,.rst,.txt"` 验证解析
5. 新增 `test_from_env_skip_dirs`：设置 `LAYERKG_BUILD_SKIP_DIRS=".git,node_modules"` 验证解析
6. 新增 `test_from_env_doc_max_length`：设置 `LAYERKG_BUILD_DOC_MAX_LENGTH=5000` 验证解析
7. 新增 `test_from_env_defaults`：不设置环境变量，验证默认值正确

## Task 2b：skip_dirs 统一

### 文件：`src/layerkg/config.py`

1. 默认 skip_dirs 加入 `.pytest_cache`、`venv`（已有 `.ruff_cache`）

### 文件：`src/layerkg/builder.py`

2. 删除 `_DOC_EXTENSIONS` 模块级常量（第 26 行）
3. **关键**：`_scan_files` 从 `@staticmethod` 改为实例方法（去掉 `@staticmethod` 装饰器，新增 `self` 参数）
4. 删除硬编码 skip_dirs 字典（第 466-479 行），改为 `skip_dirs = self._config.build_skip_dirs`
5. 文档扫描：`for ext in _DOC_EXTENSIONS` → `for ext in self._config.build_doc_extensions`
6. 文档扫描检查 `self._config.build_include_docs`：
   ```python
   doc_files: list[Path] = []
   if self._config.build_include_docs:
       for ext in self._config.build_doc_extensions:
           for p in repo_path.rglob(f"*{ext}"):
               if any(skip in p.parts or skip in p.name for skip in skip_dirs):
                   continue
               doc_files.append(p)
   ```
7. **连带修改**：所有调用 `self._scan_files(...)` / `Builder._scan_files(...)` 的地方需确认签名兼容

### 文件：`tests/unit/test_builder.py`

6. 修改已有 `_scan_files` 测试，确保 mock config 使用新的 skip_dirs
7. 新增 `test_scan_files_skip_dirs_from_config`：自定义 skip_dirs 验证生效
8. 新增 `test_scan_files_include_docs_false`：`build_include_docs=False` 时 doc_files 为空

## Task 2c：文档截断配置化

### 文件：`src/layerkg/builder.py`

1. 找到 `text = (doc.content or "")[:2000]`（约第 740 行），改为 `[:self._config.build_doc_max_length]`

### 文件：`tests/unit/test_builder.py` 或 `test_chroma_store.py`

2. 新增 `test_doc_entity_truncation_respects_config`：config 设 `build_doc_max_length=100`，验证写入文本 <= 100 字符

## Task 3：真实全量构建验证（手动）

不写代码，仅运行验证：

1. 清空 Neo4j：`MATCH (n) DETACH DELETE n`
2. 删除 ChromaDB 目录：`rm -rf .chroma`
3. 运行构建：
   ```bash
   LAYERKG_NEO4J_URI="bolt://<YOUR_SERVER_IP>:7687" \
   LAYERKG_NEO4J_PASSWORD="<YOUR_NEO4J_PASSWORD>" \
   LAYERKG_OLLAMA_URL="http://<YOUR_SERVER_IP>:11434" \
   uv run layerkg build . --verbose-build --skip-semantic --skip-clustering
   ```
4. 记录耗时和产出数据

## Task 4：数据质量验证（手动）

1. Neo4j Cypher 查询：
   ```cypher
   MATCH (n) RETURN labels(n)[0] AS type, count(*) AS cnt ORDER BY cnt DESC
   ```
2. ChromaDB search 验证：
   ```python
   from layerkg.chroma_store import ChromaStore
   store = ChromaStore(...)
   results = store.search("知识图谱", n_results=5)
   ```
3. 输出验证报告

## 执行顺序

Task 1 → Task 2a → Task 2b → Task 2c → pytest → ruff check → Task 3 → Task 4

## 验收标准

- `uv run pytest tests/ -q` 全部通过（预期 ~670+ tests）
- `uv run ruff check src/` + `uv run ruff format --check src/` clean
- 真实构建 Stage 1-2-5 完成不超时
- Neo4j + ChromaDB 有正确数据
