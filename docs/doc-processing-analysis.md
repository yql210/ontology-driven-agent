# LayerKG 文档处理管线 — 现状分析与改进方向

> 本文档聚焦 LayerKG 对 Markdown/RST 文档的处理流程，逐阶段分析现状、语义丢失风险，并提出改进方案。

---

## 一、处理管线全景

```
 .md/.rst 文件
      │
      ▼
 ┌─────────────────┐
 │ Stage 1: 发现    │  builder._scan_files()  rglob("*.md")
 │                 │  受 build_include_docs 控制
 └────────┬────────┘
          │
          ▼
 ┌─────────────────┐
 │ Stage 1: 解析    │  DocParser._parse_markdown()
 │                 │  按 #/##/### 标题切分 → DocEntity 列表
 │                 │  _detect_doc_type() 路径启发式分类
 └────────┬────────┘
          │
          ▼
 ┌─────────────────┐
 │ Stage 2: 写入    │  Neo4j MERGE 批量写入 DocEntity 节点
 │                 │  与 CodeEntity 同批次，UNWIND+MERGE
 └────────┬────────┘
          │
          ▼
 ┌──────────────────┐
 │ Stage 2.5: 关联   │  _link_docs_to_code() 启发式匹配
 │                  │  路径匹配 → 文件名匹配 → 标识符匹配
 │                  │  产出 DESCRIBES 关系
 └────────┬─────────┘
          │
          ▼
 ┌──────────────────┐
 │ Stage 3: 语义     │  SemanticExtractor._extract_cross_type_relations()
 │                  │  固定 confidence=0.7 的 DESCRIBES 关系
 └────────┬─────────┘
          │
          ▼
 ┌──────────────────┐
 │ Stage 5: 向量     │  ChromaDB 向量索引
 │                  │  截断到 build_doc_max_length (默认 800 字符)
 └──────────────────┘
```

---

## 二、逐阶段现状与问题

### 2.1 文件发现

**现状**（`builder.py:695-723`）

- `rglob("*.md")` 和 `rglob("*.rst")` 扫描，受 `build_include_docs` 开关控制
- 扩展名可配置：`build_doc_extensions` 默认 `[".md", ".rst"]`
- 跳过 `node_modules`、`.git`、`__pycache__` 等目录

**问题**

- 不支持 `.adoc`（AsciiDoc）、`.org`（Org-mode）、`.html` 等常见文档格式
- 没有文件内容预检——空文件、二进制误命名文件、模板文件（如 Jekyll `_template.md`）都会进入管线

### 2.2 Markdown 解析与切分

**现状**（`doc_parser.py:98-145`）

```python
_MD_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
```

- 只匹配 `#`/`##`/`###`（1-3 级标题），`####` 及更深层级被忽略
- 每个 heading section 变成独立 `DocEntity`
- 无标题的文件 → 整个文件变成一个 DocEntity
- heading 之间的内容直接按位置切片 `source[start_pos:end_pos]`

**问题 ①：跨 section 上下文断裂**

文档中 "如上文所述的缓存策略" 被切到另一个 DocEntity。向量检索命中时，用户只能看到一个 section 的片段，丢失了跨 section 的语义连贯性。

**问题 ②：标题层级结构丢失**

```markdown
# 用户认证
## JWT Token 管理
### Token 刷新策略
```

三级标题之间是父子包含关系，但 LayerKG 只取 heading text 做 name，**三个 DocEntity 之间没有任何关系**。图谱查询无法从 "JWT Token 管理" 回溯到它的父 topic "用户认证"。

**问题 ③：`####` 及以下层级被切割到上一个 section**

`####` 不匹配正则，其内容会被合并到最近的 `###` section 中，导致细粒度内容被粗粒度标题吞没。

**问题 ④：无标题文档的粒度问题**

一个 2000 行的 `CHANGELOG.md` 如果没有 `#` 标题，会变成一个巨大的 DocEntity。向量索引时截断到 800 字符，大量版本记录信息丢失。

### 2.3 文档类型判定

**现状**（`doc_parser.py:75-96`）

```python
def _detect_doc_type(self, file_path: str) -> str:
    path_lower = file_path.lower()
    if "readme" in path_lower:        return "readme"
    if "docs/" in path_lower and "api" in path_lower: return "api_doc"
    if "docs/" in path_lower:          return "module_doc"
    if "arch" in path_lower or "design" in path_lower: return "architecture_doc"
    return "comment"
```

纯路径启发式，不读取内容。

**问题**

| 文件 | 判定结果 | 实际语义 |
|------|---------|---------|
| `docs/guide.md`（描述 API 契约） | `module_doc` | 应为 `api_doc` |
| `CONTRIBUTING.md` | `comment` | 应为 `architecture_doc` 或 `module_doc` |
| `src/README.md` | `readme` | 正确 |
| `wiki/Home.md` | `comment` | Schema 定义了 `wiki` 类型但永远不会被赋值 |
| `design/adr-001.md` | `architecture_doc` | 正确 |

`wiki` 类型在 Schema 中定义（`schema.py:112-119`）但从未被任何路径规则触发，是死代码。

### 2.4 文档-代码关联

**现状**（`builder.py:815-897`）

三种串行策略，每个文档最多 50 条关系：

1. **路径匹配** — `file_path in doc_content` + 边界字符检查
2. **文件名匹配** — `os.path.basename(file_path) in doc_content` + 边界检查
3. **标识符匹配** — 从 ` ```python ` 代码块提取标识符，匹配 CodeEntity name（> 3 字符）

**问题 ①：自然语言中的引用完全忽略**

文档写 "LayerKGBuilder 负责整个构建流程"，其中 `LayerKGBuilder` 是代码中的类名，但它不在代码块内，不在路径中，不在文件名中——**三条规则全部无法匹配**。

**问题 ②：语义关联 vs 字面匹配**

```markdown
# 缓存策略

本模块使用 Redis 作为缓存后端，通过 CacheManager 管理缓存生命周期。
```

人类读者能理解这里描述了 `CacheManager` 的行为。但当前方案只做子串匹配 `other.name in code.source`，无法建立这种语义关联。

**问题 ③：一对多描述无区分**

一个 section 可能同时引用 5 个代码实体，当前全部建立 `describes` 关系，没有区分"核心描述"和"顺带提及"。所有关系权重相同（`confidence=1.0` for AST / `0.7` for semantic）。

### 2.5 语义层的文档处理

**现状**（`semantic.py:471-519`）

```python
for other in other_entities:
    for code in code_entities:
        if code.source and other.name in code.source:
            # confidence 固定 0.7
            SemanticRelation(confidence=0.7, ...)
```

遍历所有 code × doc 组合，检查 doc name 是否出现在 code source 中。

**问题**

- 固定置信度 0.7，不区分"标题完全匹配"和"偶然子串匹配"
- `other.name in code.source` 是纯子串匹配，`"api"` in `"the capital of France"` → True
- 时间复杂度 O(doc × code)，大项目下非常慢
- 没有利用上一阶段已有的 DESCRIBES 关系去增强或去重

### 2.6 向量索引

**现状**（`builder.py:1004-1008`）

```python
text = (doc.content or "")[: self._config.build_doc_max_length]  # 默认 800
if text.strip():
    items.append((doc.id, text, {...}))
```

**问题**

- 800 字符 ≈ 200-300 个中文 token，一个典型架构 section 3000+ 字
- 关键信息在中后段 → 向量搜索完全搜不到
- 一个 DocEntity 只生成一条向量记录，不支持多粒度检索
- 没有 chunk 重叠策略，切断处可能正好在句子中间

---

## 三、问题严重度矩阵

| 问题 | 影响范围 | 语义损失程度 | 修复难度 |
|------|---------|-------------|---------|
| 标题层级结构丢失 | 所有有多级标题的文档 | 高 | 中 |
| 向量截断丢信息 | 长文档 section | 高 | 低 |
| 自然语言引用无法匹配 | 大量文档 | 高 | 中 |
| 跨 section 上下文断裂 | 有交叉引用的文档 | 中 | 中 |
| 类型判定纯靠路径 | 非标准路径的文档 | 中 | 低 |
| 置信度固定不分层 | 所有语义关联 | 中 | 低 |
| `wiki` 类型死代码 | wiki 类文档 | 低 | 低 |
| `####` 深层标题被吞没 | 有 4+ 级标题的文档 | 低 | 低 |

---

## 四、改进方向

### 4.1 短期优化（低成本高收益）

#### A. 向量索引 Chunking 替代截断

**现状**：`(doc.content)[:800]` 硬截断

**改进**：滑动窗口分块，带重叠

```python
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """按 chunk_size 切分，相邻 chunk 有 overlap 字符重叠。"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks
```

每个 DocEntity 产出多条向量记录，共用同一个 `doc.id` 前缀（如 `doc-123-chunk-0`）。检索时通过 ID 前缀聚合回原始 DocEntity。

**收益**：长文档检索召回率大幅提升。

#### B. 文档类型判定加入内容特征

```python
def _detect_doc_type(self, file_path: str, content: str = "") -> str:
    # 1. 路径启发式（保留现有逻辑）
    # 2. 内容特征补充
    if "wiki" in file_path.lower():
        return "wiki"
    if content:
        api_signals = ["endpoint", "HTTP", "REST", "API", "请求", "响应"]
        if sum(1 for s in api_signals if s in content) >= 2:
            return "api_doc"
    return "comment"
```

**收益**：消除 `wiki` 死代码，减少误分类。

#### C. 置信度分层

```python
# 替代固定 0.7
if heading_exact_match:
    confidence = 0.95
elif identifier_in_code_block:
    confidence = 0.8
elif substring_in_source:
    confidence = 0.5
```

**收益**：下游查询可按置信度过滤，减少噪音关系。

### 4.2 中期增强（中等投入）

#### D. 保留标题层级结构

在 DocEntity 间建立 `CONTAINS` 关系：

```python
# 解析时记录父子关系
for i, match in enumerate(matches):
    level = len(match.group(1))  # # → 1, ## → 2, ### → 3
    # 同文件内，低 level 包含高 level
```

在 `_parse_markdown()` 返回时额外产出 `CONTAINS` Relation，写入 Neo4j。

查询时从任意 DocEntity 可以向上回溯到根 topic，向下展开子 topic。

**收益**：支持层次化文档导航和上下文聚合查询。

#### E. 自然语言实体识别（NER）

Stage 2.5 增加第四种匹配策略：

```python
def _extract_prose_mentions(self, doc_content: str, entity_names: set[str]) -> list[str]:
    """从正文中提取代码实体名称（非代码块部分）。"""
    # 移除代码块，只保留正文
    prose = re.sub(r"```.*?```", "", doc_content, flags=re.DOTALL)
    # 反向模板匹配：已知实体名称在正文中的出现
    mentions = []
    for name in entity_names:
        if len(name) > 3 and re.search(rf'\b{re.escape(name)}\b', prose):
            mentions.append(name)
    return mentions
```

用已知的 CodeEntity 名称集合做反向匹配，不依赖 LLM，零成本增加召回。

**收益**：捕获文档正文中对代码实体的自然语言引用。

#### F. 语义层 LLM 辅助精排

在启发式粗筛之后，对候选关联用 LLM 判断：

```python
prompt = f"""文档段落：{doc_section[:500]}
代码实体：{code_entity.name} ({code_entity.entity_type})
这段文档是否主要描述了这个代码实体？回答 YES 或 NO，并给出 0-1 的相关度评分。"""
```

只对启发式命中的候选调用 LLM（不是全量），成本可控。

**收益**：区分"核心描述"和"偶然提及"，提升关系质量。

### 4.3 长期演进（架构级改进）

#### G. 引入 GraphRAG 思路：文档社区摘要

借鉴 GraphRAG 的 Leiden 聚类 + 社区报告机制：

1. 对文档 DocEntity 按 `CONTAINS` 关系和向量相似度做层次聚类
2. 每个文档社区生成 LLM 摘要（如 "认证模块包含 JWT 管理、Token 刷新、Session 存储三个子 topic"）
3. 支持 Global Query：回答"这个项目的整体架构是什么"

**与当前架构的融合点**：社区摘要存为 `ConceptEntity`（`entity_type="process"`），通过 `DESCRIBES` 关联到下属 DocEntity。

#### H. 增量文档更新

当前 Butler 的 GitWatcher 只监听代码变更。扩展为：

```python
# butler/git_watcher.py
DOC_EXTENSIONS = {".md", ".rst", ".adoc"}

def _classify_change(self, path: Path) -> str:
    if path.suffix in CODE_EXTENSIONS:
        return "code"
    if path.suffix in DOC_EXTENSIONS:
        return "doc"
    return "other"
```

文档变更触发增量 DocParser 重解析 → 差异化更新 DESCRIBES 关系。

#### I. 多格式文档支持

```
.md   → DocParser._parse_markdown()   (已有)
.rst  → DocParser._parse_rst()        (已有)
.adoc → DocParser._parse_asciidoc()   (新增)
.org  → DocParser._parse_orgmode()    (新增)
.html → DocParser._parse_html()       (新增，提取 <h1>-<h3> + 正文)
```

统一为 `DocParseResult`，后续流程不变。

---

## 五、改进优先级建议

| 优先级 | 改进项 | 预计投入 | 收益 |
|--------|--------|---------|------|
| P0 | 向量 Chunking（替代截断） | 2 小时 | 长文档召回率翻倍 |
| P0 | 自然语言实体识别（NER） | 3 小时 | doc-code 关联量 +30~50% |
| P1 | 标题层级 CONTAINS 关系 | 4 小时 | 层次化文档导航 |
| P1 | 置信度分层 | 1 小时 | 关系质量提升 |
| P1 | 类型判定增强 + wiki 修复 | 1 小时 | 消除死代码和误分类 |
| P2 | LLM 精排 | 4 小时 | 关系精度提升 |
| P2 | 文档社区摘要 | 2 天 | 支持全局查询 |
| P3 | 增量文档更新 | 1 天 | Butler 文档感知 |
| P3 | 多格式支持 | 2 天 | 扩展数据源覆盖 |

---

## 六、与 GraphRAG 的能力差距总结

| 能力 | GraphRAG | LayerKG 现状 | LayerKG 改进后 |
|------|----------|-------------|---------------|
| 文档切分 | 1200 token 滑动窗口 | 按 heading 硬切 | heading + 滑动窗口 |
| 向量索引 | 三类嵌入（实体/关系/文本） | 单条截断 800 字符 | Chunking 多向量 |
| 文档-代码关联 | LLM 自由抽取 | 启发式三策略 | 启发式 + NER + LLM 精排 |
| 全局查询 | Map-Reduce 社区报告 | 不支持 | 文档社区摘要 |
| 层次结构 | Leiden 多分辨率 | 无 | CONTAINS 关系 |
| 增量更新 | 全量重建 | 代码增量，文档不支持 | 文档也支持增量 |

LayerKG 的核心优势仍然是 **AST 精确解析** 和 **本体约束**，文档处理是短板。上述改进方向的目标是让文档处理达到"够用"的水平，而非复制 GraphRAG 的完整能力。
