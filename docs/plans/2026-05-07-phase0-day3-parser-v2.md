# Phase 0 Day 3 — Tree-sitter Python Parser + 关系提取器 (V3)

> 审核后修订版。V1→V2 修复 3 个 Critical，V2→V3 新增 imports 提取。

## 修改记录
| 版本 | 变更 |
|------|------|
| V1 | 初版 |
| V2 | ① entities→CodeEntity；② ExtractedRelation 中间表示；③ AST 父子追踪 |
| V3 | ④ 新增 imports 关系提取；⑤ 明确 C1(GraphStore适配)推迟到集成阶段 |

## 目标
实现 `parser/base.py`、`parser/python_parser.py`、`extractor/relation.py`，完成 Python 源码的 AST 解析与结构关系提取。

## 已验证的技术约束
- **tree-sitter 0.24+**：`Query.matches()` 和 `Query.captures()` 已废弃，必须使用**手动树遍历**
- **tree-sitter-python 0.25**：`Language(tspython.language())` 创建语言对象
- 节点访问：`node.child_by_field_name("name")`、`node.start_point[0]`、`node.text.decode()`
- 依赖已安装：`tree-sitter>=0.24`、`tree-sitter-python==0.25.0`

---

## 任务 1: parser/base.py — 解析器抽象基类

### 文件：`src/layerkg/parser/base.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from dataclasses import dataclass, field

from layerkg.schema import CodeEntity


@dataclass
class ExtractedRelation:
    """解析阶段提取的关系（使用实体名称，非 UUID）。

    这是中间表示。存储到 GraphStore 前，需要通过 EntityResolver
    将 source_name/target_name 解析为 UUID，转换为 schema.Relation。

    Attributes:
        source_name: 源实体名称。
        source_type: 源实体类型（function/class/module）。
        target_name: 目标实体名称。
        target_type: 目标实体类型。
        relation_type: 关系类型（contains/extends）。
        file_path: 所属文件路径。
    """
    source_name: str
    source_type: str
    target_name: str
    target_type: str
    relation_type: str
    file_path: str


@dataclass
class ParseResult:
    """单个文件的解析结果。

    Attributes:
        file_path: 源文件路径。
        entities: 提取到的 CodeEntity 列表（强类型）。
        relations: 提取到的关系信息列表（中间表示）。
        language: 文件语言。
        error: 解析错误信息（可选）。
    """
    file_path: str
    entities: list[CodeEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
    language: str = "python"
    error: str | None = None


class BaseParser(ABC):
    """源码解析器抽象基类。"""

    @abstractmethod
    def parse_file(self, file_path: Path) -> ParseResult:
        """解析单个文件。

        Args:
            file_path: 源文件路径。

        Returns:
            ParseResult 包含提取的实体和关系。
        """

    @abstractmethod
    def parse_source(self, source: bytes, file_path: str = "<string>") -> ParseResult:
        """解析源码字符串。

        Args:
            source: 源码字节流。
            file_path: 虚拟文件路径（用于定位）。

        Returns:
            ParseResult 包含提取的实体和关系。
        """

    @property
    @abstractmethod
    def language(self) -> str:
        """解析器支持的语言名称。"""
```

### 测试：`tests/unit/test_parser_base.py`

```python
from __future__ import annotations

from pathlib import Path

import pytest

from layerkg.parser.base import BaseParser, ParseResult, ExtractedRelation
from layerkg.schema import CodeEntity


def test_parse_result_creation_with_code_entities():
    """测试 ParseResult 使用 CodeEntity 强类型。"""
    entity = CodeEntity(name="hello", entity_type="function", file_path="/test/foo.py")
    relation = ExtractedRelation(
        source_name="foo", source_type="module",
        target_name="hello", target_type="function",
        relation_type="contains", file_path="/test/foo.py",
    )
    result = ParseResult(
        file_path="/test/foo.py",
        entities=[entity],
        relations=[relation],
    )
    assert result.file_path == "/test/foo.py"
    assert len(result.entities) == 1
    assert isinstance(result.entities[0], CodeEntity)
    assert result.entities[0].name == "hello"
    assert result.error is None


def test_parse_result_with_error():
    """测试 ParseResult 带错误信息。"""
    result = ParseResult(file_path="/test/bad.py", error="SyntaxError: invalid syntax")
    assert result.error == "SyntaxError: invalid syntax"
    assert result.entities == []


def test_base_parser_is_abstract():
    """测试 BaseParser 不能直接实例化。"""
    with pytest.raises(TypeError):
        BaseParser()


def test_base_parser_subclass_must_implement():
    """测试子类必须实现所有抽象方法。"""
    class IncompleteParser(BaseParser):
        pass

    with pytest.raises(TypeError):
        IncompleteParser()


def test_extracted_relation_creation():
    """测试 ExtractedRelation 正常创建。"""
    rel = ExtractedRelation(
        source_name="Dog", source_type="class",
        target_name="Animal", target_type="class",
        relation_type="extends", file_path="/test/animal.py",
    )
    assert rel.source_name == "Dog"
    assert rel.relation_type == "extends"
```

---

## 任务 2: parser/python_parser.py — Python AST 解析器

### 文件：`src/layerkg/parser/python_parser.py`

**核心要求：**
- 使用 `tree_sitter.Language(tspython.language())` + `tree_sitter.Parser(PY_LANG)` 创建解析器
- **禁止使用 Query API**，必须用递归树遍历
- **实体输出为 `CodeEntity` dataclass**（来自 schema.py），不是 dict
- **在遍历时记录 AST 父子关系**，直接生成 `ExtractedRelation`，不用行号推断

### 提取目标：

| 实体类型 | tree-sitter 节点 | 备注 |
|---------|-----------------|------|
| `module` | 根节点 | name = Path(file_path).stem |
| `function` | `function_definition` | 包括类内方法 |
| `class` | `class_definition` | 包括 parent_classes |

### AST 父子追踪（关键！不用行号推断）：

```python
def _walk(self, node, source_bytes, file_path, parent_class_name: str | None = None):
    """递归遍历 AST，提取实体并追踪父子关系。

    Args:
        node: 当前 tree-sitter 节点。
        source_bytes: 源码字节流。
        file_path: 文件路径。
        parent_class_name: 如果当前在类内部，记录类名。用于生成 contains 关系。
    """
    if node.type == "function_definition":
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode() if name_node else "<anonymous>"
        entity = CodeEntity(
            name=name,
            entity_type="function",
            file_path=file_path,
            start_line=node.start_point[0],
            end_line=node.end_point[0],
            source=node.text.decode(),
            language="python",
        )
        self._entities.append(entity)
        # 生成 contains 关系
        container = parent_class_name or Path(file_path).stem
        container_type = "class" if parent_class_name else "module"
        self._relations.append(ExtractedRelation(
            source_name=container,
            source_type=container_type,
            target_name=name,
            target_type="function",
            relation_type="contains",
            file_path=file_path,
        ))

    elif node.type == "class_definition":
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode() if name_node else "<anonymous>"
        # 提取父类
        parent_classes = self._extract_parent_classes(node)
        entity = CodeEntity(
            name=name,
            entity_type="class",
            file_path=file_path,
            start_line=node.start_point[0],
            end_line=node.end_point[0],
            source=node.text.decode(),
            language="python",
        )
        self._entities.append(entity)
        # module contains class
        self._relations.append(ExtractedRelation(
            source_name=Path(file_path).stem,
            source_type="module",
            target_name=name,
            target_type="class",
            relation_type="contains",
            file_path=file_path,
        ))
        # class extends parent
        for parent in parent_classes:
            self._relations.append(ExtractedRelation(
                source_name=name,
                source_type="class",
                target_name=parent,
                target_type="class",
                relation_type="extends",
                file_path=file_path,
            ))
        # 继续递归子节点，传入当前类名
        for child in node.children:
            self._walk(child, source_bytes, file_path, parent_class_name=name)
        return  # 已经递归过子节点，直接返回

        # 继续递归子节点
        for child in node.children:
            self._walk(child, source_bytes, file_path, parent_class_name)

    elif node.type == "import_statement":
        # import os, sys → 每个导入生成 imports 关系
        for child in node.children:
            if child.type in ("dotted_name", "identifier"):
                self._relations.append(ExtractedRelation(
                    source_name=Path(file_path).stem,
                    source_type="module",
                    target_name=child.text.decode(),
                    target_type="module",
                    relation_type="imports",
                    file_path=file_path,
                ))
                break  # import os 只取第一个 name（完整模块路径）

    elif node.type == "import_from_statement":
        # from typing import Optional → target = "typing"
        module_node = node.child_by_field_name("module_name")
        if module_node:
            self._relations.append(ExtractedRelation(
                source_name=Path(file_path).stem,
                source_type="module",
                target_name=module_node.text.decode(),
                target_type="module",
                relation_type="imports",
                file_path=file_path,
            ))
        # 继续递归子节点（import_from_statement 可能有嵌套结构）
        for child in node.children:
            self._walk(child, source_bytes, file_path, parent_class_name)

    else:
        # 其他节点类型，继续递归
        for child in node.children:
            self._walk(child, source_bytes, file_path, parent_class_name)
```

### module 实体定义（明确）：
```python
module_entity = CodeEntity(
    name=Path(file_path).stem,  # 文件名不含扩展名
    entity_type="module",
    file_path=file_path,
    start_line=0,
    end_line=source_bytes.count(b"\n"),
    source=source_bytes.decode(errors="replace"),
    language="python",
)
```

### 测试：`tests/unit/test_python_parser.py`（至少 12 个）

```python
# 共享 fixture 放在 tests/conftest.py

SIMPLE_CODE = b'''
def hello():
    print("hello")
'''

CLASS_CODE = b'''
class Animal:
    def speak(self):
        pass

class Dog(Animal):
    def speak(self):
        return "Woof"
'''

COMPLEX_CODE = b'''
import os
from typing import Optional

def helper():
    pass

class Service:
    """A service class."""
    
    def process(self, data: str) -> Optional[str]:
        return data.upper()
    
    def _internal(self):
        pass
'''

ASYNC_CODE = b'''
async def fetch_data():
    await some_io()
'''

DECORATOR_CODE = b'''
@staticmethod
def my_static():
    pass

@dataclass
class MyClass:
    name: str
'''

NESTED_CLASS_CODE = b'''
class Outer:
    class Inner:
        pass
    def method(self):
        pass
'''

MULTI_INHERIT_CODE = b'''
class Dog(Animal, Pet):
    pass
'''
```

测试用例清单：
| # | 测试名 | 验证点 |
|---|--------|--------|
| 1 | `test_parse_simple_function` | 提取 function 实体，验证 name/start_line/end_line |
| 2 | `test_parse_class` | 提取 class 实体 |
| 3 | `test_parse_class_methods_as_functions` | 类内方法也提取为 function 实体 |
| 4 | `test_module_entity_always_present` | parse_source 总是包含 module 实体 |
| 5 | `test_parse_empty_source` | 空源码只有 module 实体 |
| 6 | `test_parse_syntax_error_resilient` | 语法错误不抛异常，tree-sitter 容错解析 |
| 7 | `test_parse_file_not_found` | 文件不存在返回 error |
| 8 | `test_parse_multiple_functions` | 多个函数全部提取 |
| 9 | `test_parse_class_with_inheritance` | extends 关系正确提取 |
| 10 | `test_language_property` | language 属性返回 "python" |
| 11 | `test_parse_decorated_function` | 装饰器不影响提取 |
| 12 | `test_parse_async_function` | async def 正确提取 |
| 13 | `test_parse_class_multi_inheritance` | 多继承提取多个 extends 关系 |
| 14 | `test_parse_nested_class` | 嵌套类正确提取 |
| 15 | `test_lambda_not_extracted` | lambda 不提取为实体 |
| 16 | `test_parse_import_statement` | `import os` 生成 imports 关系 |
| 17 | `test_parse_import_from_statement` | `from typing import Optional` 生成 imports 关系 |
| 18 | `test_parse_import_not_entity` | import 不生成实体，只生成关系 |

---

## 任务 3: extractor/relation.py — 结构关系提取器

### 文件：`src/layerkg/extractor/relation.py`

**说明：** 因为 parser 已经在遍历时直接生成了 `ExtractedRelation`，`RelationExtractor` 的职责是：

1. **聚合多个文件的解析结果** — 合并去重
2. **转换为 schema.Relation** — 将名称级关系转换为 ID 级关系（需要 entity 名称→ID 映射）
3. **过滤无效关系** — 源/目标实体必须存在

```python
from __future__ import annotations

from dataclasses import dataclass

from layerkg.parser.base import ExtractedRelation
from layerkg.schema import CodeEntity, Relation


class RelationExtractor:
    """从 ParseResult 提取并转换结构关系。

    职责：
    1. 聚合多个文件的 ExtractedRelation
    2. 将名称级关系（ExtractedRelation）转换为 ID 级关系（schema.Relation）
    3. 过滤无效关系（源/目标实体不在已知集合中）
    """

    def __init__(self) -> None:
        self._relations: list[ExtractedRelation] = []

    def add_parse_result(
        self, entities: list[CodeEntity], relations: list[ExtractedRelation]
    ) -> None:
        """添加一个文件的解析结果。

        Args:
            entities: 该文件的 CodeEntity 列表。
            relations: 该文件的 ExtractedRelation 列表。
        """
        self._relations.extend(relations)

    def resolve(self, all_entities: list[CodeEntity]) -> list[Relation]:
        """将所有名称级关系转换为 ID 级关系。

        Args:
            all_entities: 所有已知实体列表。

        Returns:
            转换后的 Relation 列表，过滤掉无效引用。
        """
        name_to_id = self._build_name_map(all_entities)
        resolved = []
        for rel in self._relations:
            source_id = name_to_id.get(rel.source_name)
            target_id = name_to_id.get(rel.target_name)
            if source_id and target_id:
                resolved.append(Relation(
                    source_id=source_id,
                    target_id=target_id,
                    relation_type=rel.relation_type,
                ))
        return resolved

    @staticmethod
    def _build_name_map(entities: list[CodeEntity]) -> dict[str, str]:
        """构建实体名称到 ID 的映射。

        注意：同名实体（不同文件）会产生覆盖，以最后出现的为准。
        未来可通过 EntityResolver + 概念对齐器处理。

        Args:
            entities: 实体列表。

        Returns:
            {name: id} 映射。
        """
        return {e.name: e.id for e in entities}
```

### 测试：`tests/unit/test_relation_extractor.py`

| # | 测试名 | 验证点 |
|---|--------|--------|
| 1 | `test_extract_contains_module_to_function` | module → function contains |
| 2 | `test_extract_contains_module_to_class` | module → class contains |
| 3 | `test_extract_contains_class_to_method` | class → method contains |
| 4 | `test_extract_extends_single_parent` | Dog extends Animal |
| 5 | `test_extract_extends_no_parent` | 无父类不生成关系 |
| 6 | `test_extract_empty_input` | 空输入返回空 |
| 7 | `test_resolve_filters_invalid` | 无效引用被过滤 |
| 8 | `test_resolve_multiple_files` | 多文件聚合解析 |
| 9 | `test_name_to_id_mapping` | 名称→ID 映射正确 |
| 10 | `test_full_pipeline` | 完整解析+提取+resolve pipeline |
| 11 | `test_resolve_imports_relations` | imports 关系也参与 resolve |

---

## 编码规范
- `from __future__ import annotations` 每个文件头部
- 类型注解必须
- Google docstring
- ruff format/check 通过
- **禁止使用 tree_sitter.Query API** — 只用递归遍历 node.children
- `__all__` 导出控制

## 执行顺序（严格 TDD）
1. 先写 `test_parser_base.py` → 实现 `base.py`
2. 再写 `test_python_parser.py` → 实现 `python_parser.py`
3. 最后写 `test_relation_extractor.py` → 实现 `relation.py`
4. 全量测试 + ruff check
