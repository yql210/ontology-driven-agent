# Phase 2 Day 5 设计方案：文档摄入 (DocParser + describes 关系)

## 1. 问题陈述

当前 `build()` 流水线只处理 Python 源文件，无法摄入项目中的 Markdown/RST 文档。LayerKG 的 6 实体设计包含 `DocEntity`，schema 中 `describes` 关系已预定义，但 builder 未实现文档解析和关联。

## 2. 设计方案

### 2.1 新建 DocParser（`src/layerkg/parser/doc_parser.py`）

**不继承 BaseParser**。原因：
- `BaseParser.parse_file()` 返回 `ParseResult`，其 `entities` 类型为 `list[CodeEntity]`
- DocParser 产出 `DocEntity`，与 `CodeEntity` 类型不兼容
- 强行继承需要 `type: ignore` 或泛型化，增加复杂度

**独立接口设计：**
```python
class DocParser:
    def parse_file(self, file_path: Path) -> DocParseResult
    def parse_source(self, source: str, file_path: str = "<string>") -> DocParseResult

@dataclass
class DocParseResult:
    file_path: str
    entities: list[DocEntity]     # DocEntity，非 CodeEntity
    error: str | None = None
```

**Markdown 解析策略（纯正则，零依赖）：**
- 按 `# ` / `## ` / `### ` 标题拆分 section
- 每个 section → 一个 DocEntity（name=标题, content=正文+代码块）
- 无标题的文件 → 整个文件作为一个 DocEntity（name=文件名）
- 代码块提取：`` ```language ... ``` `` 正则匹配，代码块内容已包含在 content 中
- **空文件** → 跳过，不创建 DocEntity
- **编码问题** → `encoding="utf-8"`，失败回退 `"latin-1"`（不会抛异常）
- **超大文件** → 超过 5MB 跳过，logging.warning
- **空内容 section**（有标题无正文） → 仍创建 DocEntity（content=""），由向量写入时 `if text.strip()` 过滤

**RST 解析策略（纯正则）：**
- section 标题识别：`===` / `---` / `~~~` 下划线语法
- directive 识别：`.. code-block::`, `.. note::` 等
- 同样的空文件跳过和编码回退

**文档类型检测（启发式，基于路径+文件名）：**
- `README*` → `"readme"`
- `docs/` 下 + 文件名含 `api` → `"api_doc"`
- `docs/` 下 + 其他 → `"module_doc"`
- 路径含 `arch`/`design` → `"architecture_doc"`
- 其他 → `"comment"`

### 2.2 Builder 扩展（`src/layerkg/builder.py`）

#### 2.2.1 `_scan_python_files()` → `_scan_files()`

```python
@staticmethod
def _scan_files(repo_path: Path) -> tuple[list[Path], list[Path]]:
    """返回 (python_files, doc_files)"""
```
- Python: `*.py`，现有 skip_dirs 不变
- Doc: `*.md`, `*.rst`，同样跳过 `.git`, `__pycache__`, `.venv` 等 + 额外跳过 `site/`（MkDocs/Sphinx 产物）

#### 2.2.2 `_stage_parse()` 返回扩展

```python
def _stage_parse(self, repo_path: Path) -> tuple[list[CodeEntity], list[DocEntity], list[Relation], int]:
    """返回 (code_entities, doc_entities, relations, files_scanned)"""
```

**内部流程：**
```python
def _stage_parse(self, repo_path: Path):
    py_files, doc_files = self._scan_files(repo_path)
    files_scanned = len(py_files) + len(doc_files)

    # 解析 Python 文件
    all_entities: list[CodeEntity] = []
    relations: list[Relation] = []
    for f in py_files:
        result = self._parser.parse_file(f)
        if result.error:
            self._logger.warning(...)
            continue
        all_entities.extend(result.entities)
        relations.extend(self._convert_relations(result.relations, result.entities))

    # 解析文档文件
    doc_entities: list[DocEntity] = []
    if doc_files:
        doc_parser = self._get_doc_parser()  # lazy init
        for f in doc_files:
            result = doc_parser.parse_file(f)
            if result.error:
                self._logger.warning(...)
                continue
            doc_entities.extend(result.entities)

    return all_entities, doc_entities, relations, files_scanned
```

- `_scan_files()` 替代 `_scan_python_files()`，返回两类文件
- DocParser lazy init（与 SemanticExtractor 同模式），新增 `_get_doc_parser()` 方法
- 文档解析错误不中止，warning + continue

#### 2.2.3 `_stage_write_structural()` 增加 DocEntity 写入

```python
def _stage_write_structural(
    self,
    all_entities: list[CodeEntity],
    doc_entities: list[DocEntity],
    relations: list[Relation],
) -> Neo4jGraphStore:
```
- 在写入 CodeEntity 之后，循环写入 DocEntity（标签 `"DocEntity"`）
- 使用专用的 `_doc_entity_to_dict()` 方法（而非 `_entity_to_dict()`，因为后者类型标注为 CodeEntity 且访问 `start_line`/`end_line`/`source`/`language` 等 DocEntity 不存在的字段）

```python
@staticmethod
def _doc_entity_to_dict(entity: DocEntity) -> dict:
    d: dict[str, Any] = {"id": entity.id, "name": entity.name, "entity_type": entity.entity_type}
    if entity.file_path:
        d["file_path"] = entity.file_path
    if entity.content:
        d["content"] = entity.content
    if entity.language:
        d["language"] = entity.language
    return d
```

#### 2.2.4 新增 `_link_docs_to_code()` 方法

```python
def _link_docs_to_code(
    self,
    doc_entities: list[DocEntity],
    entity_index: dict[tuple[str, str, str], list[str]],
) -> list[Relation]:
```

**匹配策略（两级）：**
1. **路径匹配**（高置信度）：从 `entity_index` 中提取所有已存在的 `file_path` → 检查哪些路径字符串出现在文档 `content` 中 → 匹配该路径下的所有 CodeEntity
2. **函数名匹配**（低置信度，仅路径匹配范围内）：代码块中出现的标识符 → 匹配 CodeEntity.name（长度 > 3，避免误匹配 `get`, `set` 等）

**路径匹配实现（带边界检查，防止子串误匹配）：**
```python
def _link_docs_to_code(self, doc_entities, entity_index):
    # 1. 从 entity_index 提取所有唯一的 file_path
    #    entity_index 类型: dict[tuple[str, str, str], list[str]]
    #    键: (entity_type, file_path, name)，值: [id1, id2, ...]
    #    注意：entity_index 仅包含 CodeEntity（调用时只传 all_entities）
    known_paths: set[str] = set()
    for (etype, fpath, name), eid_list in entity_index.items():
        known_paths.add(fpath)

    # 2. 对每个 DocEntity，检查哪些 known_paths 出现在 content 中
    #    使用边界检查防止子串误匹配（如 src/lib/foo.py 匹配到 src/lib/foo_backup.py）
    _BOUNDARY_CHARS = set('\n \t(\"\')`/,.:;[]{}')
    for doc in doc_entities:
        if not doc.content:
            continue
        matched_paths: set[str] = set()
        for p in known_paths:
            idx = doc.content.find(p)
            while idx != -1:
                before = doc.content[idx - 1] if idx > 0 else ''
                after = doc.content[idx + len(p)] if idx + len(p) < len(doc.content) else ''
                if before in _BOUNDARY_CHARS and after in _BOUNDARY_CHARS:
                    matched_paths.add(p)
                    break  # 找到一个匹配就够了
                idx = doc.content.find(p, idx + 1)
        # 3. 收集 matched_paths 下的所有 CodeEntity id → 生成 describes 关系
        #    source=DocEntity.id, target=CodeEntity.id
        for matched_path in matched_paths:
            for (etype, fpath, name), eid_list in entity_index.items():
                if fpath == matched_path:
                    for target_id in eid_list:
                        relations.append(Relation(
                            source_id=doc.id, target_id=target_id,
                            relation_type="describes"
                        ))

        # 4. 函数名匹配（补充，仅在路径匹配范围内）
        #    从代码块中提取标识符，匹配 entity_index 中的 name
        #    仅匹配长度 > 3 的标识符，避免误匹配 get/set 等
        code_identifiers = _extract_identifiers_from_code_blocks(doc.content)
        for ident in code_identifiers:
            if len(ident) <= 3:
                continue
            for (etype, fpath, name), eid_list in entity_index.items():
                if fpath not in matched_paths:
                    continue  # 仅在路径匹配范围内
                if name == ident:
                    for target_id in eid_list:
                        relations.append(Relation(
                            source_id=doc.id, target_id=target_id,
                            relation_type="describes"
                        ))
```

**优势**：不依赖硬编码的 `src/lib` 目录前缀，利用已有 entity_index 的实际路径。

**防误匹配约束：**
- 路径匹配优先级高于函数名
- 函数名匹配仅限长度 > 3
- 每个 DocEntity 最多生成 50 条 describes 关系（防止大文档爆炸）

#### 2.2.5 `_write_all_vectors()` 增加 DocEntity

新增参数 `doc_entities: list[DocEntity]`。

**DocEntity → 向量元组转换：**
```python
# DocEntity 向量写入
for doc in doc_entities:
    text = (doc.content or "")[:2000]  # 截断防过长
    if text.strip():
        items.append((doc.id, text, {"entity_type": doc.entity_type, "name": doc.name}))
```

截断到 2000 字符，避免嵌入模型输入过长。

#### 2.2.6 `build()` 流水线更新

**修改后完整流水线：**
```python
def build(self, repo_path: Path) -> BuildResult:
    # Stage 1: 解析（返回值从 3 元组扩展为 4 元组）
    all_entities, doc_entities, relations, files_scanned = self._stage_parse(repo_path)

    # Stage 2: 结构写入（新增 doc_entities 参数）
    graph_store = self._stage_write_structural(all_entities, doc_entities, relations)

    # ★ Stage 2.5: 文档→代码关联（新增）
    # entity_index 仅基于 CodeEntity 构建（all_entities 是 CodeEntity 列表）
    entity_index = self._build_entity_index(all_entities, repo_path)
    describes_rels = self._link_docs_to_code(doc_entities, entity_index)
    for rel in describes_rels:
        graph_store.merge_relation(rel.source_id, rel.target_id, rel.relation_type)

    # Stage 3: 语义提取（不变，但内部 entity_index 已在 Stage 2.5 构建）
    concepts_created, ..., new_concepts = self._stage_semantic(all_entities, graph_store, repo_path)

    # Stage 4: 模块聚类（不变）
    clusters_count, clusters = self._detect_and_write_modules(graph_store)

    # Stage 5: 向量写入（新增 doc_entities 参数）
    self._write_all_vectors(all_entities, doc_entities, new_concepts, clusters)

    # BuildResult 填充
    return BuildResult(
        ...
        doc_entities_created=len(doc_entities),
        relations_created=len(relations) + len(describes_rels),  # 包含 describes
        ...
    )
```

**关键时序说明：**
1. `_build_entity_index(all_entities)` — 只传 CodeEntity，不包含 DocEntity
2. `_link_docs_to_code(doc_entities, entity_index)` — DocEntity 是 source，CodeEntity 是 target
3. describes 关系在 Stage 2 之后、Stage 3 之前写入 — 确保语义提取可见完整图谱
4. `_stage_semantic()` 内部会再次调用 `_build_entity_index`（已有逻辑），不受影响

**`_stage_semantic()` 注意：** 该方法内部（builder.py:218）已自行构建 entity_index 并使用，Day 5 不需修改其内部逻辑，只需确保 `all_entities` 仍是 CodeEntity 列表（保持不变）。

### 2.3 parser/__init__.py 更新

```python
from layerkg.parser.doc_parser import DocParser, DocParseResult
# 添加到 __all__
```

## 3. 不变更的范围

- **schema.py** — DocEntity 和 describes 关系已预定义，零修改
- **config.py** — Day 6 再添加 build 配置项，Day 5 硬编码 `.md/.rst`
- **neo4j_store.py** — 写入模式不变，只是多调几次 `merge_node`
- **chroma_store.py** — 写入模式不变
- **BaseParser** — 不继承，避免泛型化复杂度

## 4. 依赖与集成点

| 依赖 | 说明 |
|------|------|
| DocEntity (schema.py) | 已有完整 dataclass |
| describes (schema.py) | 已在 VALID_RELATION_TYPES |
| entity_index | Day 1 实现的 `(entity_type, file_path, name) → id` 映射 |
| `_stage_write_structural()` | Day 4 实现的 Stage 2，需扩展参数 |
| `_write_all_vectors()` | Day 3 实现的 Stage 5，需扩展参数 |
| `_build_entity_index()` | Day 1 实现，`_link_docs_to_code()` 使用它查找 CodeEntity |

## 5. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| DocParser 是否继承 BaseParser | **否** | ParseResult.entities 类型不兼容，避免泛型化 |
| Markdown 解析方式 | **纯正则** | 零依赖，文档结构简单，不需要 AST |
| describes 匹配策略 | **路径优先+函数名补充** | 路径匹配高置信度，函数名仅作补充 |
| describes 写入时机 | **Stage 2 内部** | 确保后续 Stage 可见完整图谱 |
| 向量文本长度 | **截断 2000 字符** | 嵌入模型输入限制 |

## 6. 预期测试（约 14 个）

1. `test_doc_parse_result_dataclass` — DocParseResult 基础功能
2. `test_doc_parser_markdown_basic` — README → DocEntity（标题+正文）
3. `test_doc_parser_markdown_no_title` — 无标题 → 整文件一个 DocEntity
4. `test_doc_parser_rst_basic` — .rst → DocEntity
5. `test_doc_parser_type_detection` — README→readme, docs/api→api_doc, etc.
6. `test_doc_parser_code_blocks` — 提取代码块
7. `test_doc_parser_empty_content` — 有标题无正文仍创建 DocEntity
8. `test_doc_parser_large_file_skip` — 超大文件（>5MB）跳过
9. `test_scan_files_returns_both` — 同时返回 .py 和 .md 文件
10. `test_link_docs_to_code_path_match` — 路径匹配 → describes 关系
11. `test_link_docs_to_code_name_match` — 函数名匹配（路径范围内）
12. `test_link_docs_to_code_path_collision` — 路径名子串不误匹配（如 foo.py vs foo_backup.py）
13. `test_doc_entities_in_build_result` — BuildResult.doc_entities_created > 0
14. `test_doc_entity_to_dict` — _doc_entity_to_dict 正确序列化 DocEntity

## 7. 风险

| 风险 | 缓解 |
|------|------|
| 正则解析 Markdown 边界情况 | 不追求完美，覆盖 80% 常见结构即可 |
| describes 误匹配 | 长度 > 3 约束 + 路径范围内匹配 + 上限 50 条 |
| 大文档向量过长 | content 截断到 2000 字符 |
