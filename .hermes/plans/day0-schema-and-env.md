# Day 0 实现计划：Schema 扩展 + 环境准备（v2 — 审核修订版）

> 目标：为 Java 支持铺好基础 Schema + 依赖，不改解析/扫描逻辑。
> Claude Code 审核评分 6/10，已整合反馈。

## 审核结论整合

| 反馈点 | 处理 |
|--------|------|
| neo4j_store.py 硬编码？ | ✅ 无，grep 确认 |
| extractor/relation.py 硬编码？ | ✅ 无，grep 确认 |
| module_clustering.py 硬编码？ | 有 class/function 判断，新类型不受影响（enum/record/field 不会触发聚类），Day 0 不改 |
| _scan_files 扫 .java 但无 parser？ | ⚠️ **风险**：_stage_parse 把所有文件当 py_files 用 PythonParser 解析，.java 会 parse error 后 skip，不报错但不优雅。**Day 0 不改 _scan_files，推迟到 Day 3 统一做** |
| Neo4j constraint？ | ✅ id UNIQUE 与 entity_type 无关，不改 |
| tree-sitter-java 兼容性？ | Day 0 只安装验证，不影响现有代码 |
| CLI query entity_type 过滤？ | 查询参数是 str | None，不做类型校验，新类型自动支持 |
| .claude/rules/neo4j.md 文档？ | 非阻塞，Day 0 最后更新 |

---

## 任务 1：Schema 扩展（必做）

### 文件：`src/layerkg/schema.py`

**改动 1**：第 40 行 `VALID_ENTITY_TYPES`
```python
# 当前:
VALID_ENTITY_TYPES = {"function", "class", "interface", "module", "file"}
# 改为:
VALID_ENTITY_TYPES = {"function", "class", "interface", "module", "file", "enum", "record", "field"}
```

**改动 2**：第 16 行 docstring
```
entity_type: 实体类型，必须是 function/class/interface/module/file/enum/record/field 之一。
```

### 文件：`src/layerkg/builder.py`

**改动 1**：`ENTITY_TYPE_TO_LABEL` 字典（第 31-48 行），添加三行：
```python
"enum": "CodeEntity",
"record": "CodeEntity",
"field": "CodeEntity",
```

**改动 2**：`_CODE_ENTITY_TYPES` frozenset（第 62-70 行），添加三个类型：
```python
"enum",
"record",
"field",
```

### 新增测试：`tests/unit/test_schema_extra.py`

在文件末尾追加（不是覆盖）：
```python
def test_code_entity_accepts_enum():
    e = CodeEntity(name="Color", entity_type="enum")
    assert e.entity_type == "enum"

def test_code_entity_accepts_record():
    e = CodeEntity(name="Point", entity_type="record")
    assert e.entity_type == "record"

def test_code_entity_accepts_field():
    e = CodeEntity(name="x", entity_type="field")
    assert e.entity_type == "field"
```

---

## 任务 2：安装 tree-sitter-java（必做）

```bash
uv add tree-sitter-java
```

验证：
```bash
uv run python -c "import tree_sitter_java; print(tree_sitter_java.language())"
```

---

## ~~任务 3：_scan_files 扩展~~ → 推迟到 Day 3

Day 3 做 Builder 多语言支持时统一改造 _scan_files，添加按扩展名路由到不同 parser 的逻辑。

---

## 执行顺序

1. 先跑基线测试：`uv run pytest tests/ -v --tb=no -q`（确认 824 passed）
2. 修改 `schema.py` — VALID_ENTITY_TYPES + docstring
3. 修改 `builder.py` — ENTITY_TYPE_TO_LABEL + _CODE_ENTITY_TYPES
4. 添加 schema 测试用例到 `tests/unit/test_schema_extra.py`
5. 跑测试确认通过
6. `uv add tree-sitter-java` + 验证
7. 更新 `.claude/rules/neo4j.md` 文档
8. 最终全量测试
9. `git add -A && git commit -m "feat(schema): add enum/record/field entity types for Java support (Day 0)"`

## 验证标准

- `uv run pytest tests/ -v --tb=no -q` 全绿（827+ passed，新增3个测试）
- `uv run python -c "from layerkg.schema import CodeEntity; CodeEntity(name='X', entity_type='enum')"` 不报错
- `uv run python -c "import tree_sitter_java"` 不报错
- `uv run ruff check src/ tests/` 无错误
