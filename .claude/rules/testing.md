# 测试规范

## 适用范围
所有 `tests/` 目录下的 `.py` 文件。

## 框架与工具
- 测试框架：pytest
- 运行命令：`uv run pytest tests/ -v`
- 覆盖率：`uv run pytest tests/ --cov=layerkg --cov-report=term-missing`
- 配置在 `pyproject.toml [tool.pytest.ini_options]`

## TDD 强制流程（RED-GREEN-REFACTOR）
每个功能必须：
1. **先写失败测试** → 运行确认 FAIL
2. **写最小实现** → 运行确认 PASS
3. **重构** → 运行确认仍然 PASS
4. **提交**

## 测试文件组织
```
tests/
├── conftest.py          # 共享 fixtures
├── unit/
│   ├── test_schema.py   # Schema dataclass 测试
│   ├── test_graph_store.py
│   └── test_parser.py
├── integration/
│   ├── test_neo4j_store.py   # 需要真实 Neo4j
│   └── test_chroma_store.py  # 需要真实 ChromaDB
└── e2e/
    └── test_cli.py
```

## 测试命名
- 文件：`test_<module_name>.py`
- 函数：`test_<功能>_<场景>_<预期结果>`
- 例：`test_add_node_duplicate_raises_error`

## 测试结构（AAA 模式）
```python
def test_merge_node_creates_if_not_exists(neo4j_store: Neo4jGraphStore):
    # Arrange
    entity = CodeEntity(name="foo", entity_type="function")

    # Act
    neo4j_store.merge_node(entity)

    # Result
    result = neo4j_store.get_node(entity.id)
    assert result is not None
    assert result.name == "foo"
```

## Fixtures
- 共享 fixture 放 `conftest.py`
- Neo4j 连接 fixture 用 `scope="session"`
- 每个 fixture 要有 docstring
- 使用 `@pytest.fixture` 装饰器

## 标记（Markers）
```python
@pytest.mark.unit          # 纯单元测试，无外部依赖
@pytest.mark.integration   # 需要 Neo4j/ChromaDB
@pytest.mark.slow          # 耗时 >1s
```

运行特定标记：
- `uv run pytest -m unit` — 只跑单元测试
- `uv run pytest -m "not integration"` — 跳过集成测试

## Mock 规则
- 优先测试真实行为，不用 mock
- 只有外部服务（LLM API）才 mock
- Mock 放在测试函数内，不放 conftest

## 断言规范
- 使用 `assert` 语句，不用 `self.assertEqual`
- 断言消息：`assert x == y, f"Expected {y}, got {x}"`
- 异常断言用 `pytest.raises`
- 警告断言用 `pytest.warns`

## 覆盖率目标
- Phase 0：核心模块 > 80%
- 关键路径（Schema, GraphStore, Parser）：> 90%
