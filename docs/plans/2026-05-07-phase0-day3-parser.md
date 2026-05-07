# Phase 0 Day 3 — Tree-sitter Python Parser + 关系提取器

## 目标
实现 `parser/base.py`、`parser/python_parser.py`、`extractor/relation.py`，完成 Python 源码的 AST 解析与结构关系提取。

## 已验证的关键技术约束
- **tree-sitter 0.24+**：`Query.matches()` 和 `Query.captures()` 已废弃/不可用，必须使用**手动树遍历**（递归 node.children）
- **tree-sitter-python 0.25**：`Language(tspython.language())` 方式创建语言对象
- 节点访问方式：`node.child_by_field_name("name")`、`node.start_point[0]`（行号）、`node.text.decode()`
- 依赖已安装：`tree-sitter>=0.24`、`tree-sitter-python==0.25.0`

---

## 任务 1: parser/base.py — 解析器抽象基类

### 文件：`src/layerkg/parser/base.py`

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ParseResult:
    """单个文件的解析结果。
    
    Attributes:
        file_path: 源文件路径。
        entities: 提取到的 CodeEntity 属性字典列表。
        relations: 提取到的关系信息列表。
        language: 文件语言。
        error: 解析错误信息（可选）。
    """
    file_path: str
    entities: list[dict]
    relations: list[dict]
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

from layerkg.parser.base import BaseParser, ParseResult


def test_parse_result_creation():
    """测试 ParseResult 正常创建。"""
    result = ParseResult(
        file_path="/test/foo.py",
        entities=[{"name": "foo", "entity_type": "function"}],
        relations=[],
        language="python",
    )
    assert result.file_path == "/test/foo.py"
    assert len(result.entities) == 1
    assert result.error is None


def test_parse_result_with_error():
    """测试 ParseResult 带错误信息。"""
    result = ParseResult(
        file_path="/test/bad.py",
        entities=[],
        relations=[],
        error="SyntaxError: invalid syntax",
    )
    assert result.error == "SyntaxError: invalid syntax"


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
```

---

## 任务 2: parser/python_parser.py — Python AST 解析器

### 文件：`src/layerkg/parser/python_parser.py`

**核心要求：**
- 使用 `tree_sitter.Language(tspython.language())` + `tree_sitter.Parser(PY_LANG)` 创建解析器
- **禁止使用 Query API**（已废弃），必须用递归树遍历
- 提取以下实体类型为 CodeEntity：
  - `function`：`function_definition` 节点
  - `class`：`class_definition` 节点
  - `module`：文件级别的 module 隐含实体
- 每个实体输出为 dict（与 CodeEntity dataclass 字段对应）：
  - `name`: str — 实体名称
  - `entity_type`: "function" | "class" | "module"
  - `file_path`: str — 文件路径
  - `start_line`: int — 起始行号（0-based）
  - `end_line`: int — 结束行号
  - `source`: str — 源码片段
  - `language`: "python"

### 遍历逻辑伪代码：

```python
def _extract_functions(self, node, source_bytes, file_path):
    """递归提取 function_definition 节点。"""
    results = []
    if node.type == "function_definition":
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode() if name_node else "<anonymous>"
        results.append({
            "name": name,
            "entity_type": "function",
            "file_path": file_path,
            "start_line": node.start_point[0],
            "end_line": node.end_point[0],
            "source": node.text.decode(),
            "language": "python",
        })
    for child in node.children:
        results.extend(self._extract_functions(child, source_bytes, file_path))
    return results

def _extract_classes(self, node, source_bytes, file_path):
    """递归提取 class_definition 节点，包含父类信息。"""
    results = []
    if node.type == "class_definition":
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode() if name_node else "<anonymous>"
        # 提取父类列表
        parents = []
        for child in node.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type == "identifier":
                        parents.append(arg.text.decode())
        results.append({
            "name": name,
            "entity_type": "class",
            "file_path": file_path,
            "start_line": node.start_point[0],
            "end_line": node.end_point[0],
            "source": node.text.decode(),
            "language": "python",
            "parent_classes": parents,  # 额外字段，供关系提取使用
        })
    for child in node.children:
        results.extend(self._extract_classes(child, source_bytes, file_path))
    return results
```

### 测试：`tests/unit/test_python_parser.py`

需要测试的用例（至少 10 个）：
- `test_parse_simple_function` — 解析简单函数，验证 name/entity_type/start_line/end_line
- `test_parse_class` — 解析类定义，验证 parent_classes 提取
- `test_parse_nested_class_methods` — 类内方法也被提取为独立 function 实体
- `test_parse_imports` — 不提取 import 为实体（Phase 0 只关注 function/class）
- `test_parse_empty_source` — 空源码返回空 entities
- `test_parse_syntax_error` — 语法错误的代码，tree-sitter 会尽力解析，不抛异常
- `test_parse_file_not_found` — parse_file 传入不存在的路径，返回 error
- `test_module_entity` — parse_source 总是包含一个 module 级别实体
- `test_parse_multiple_functions` — 多个函数全部提取
- `test_parse_class_with_inheritance` — class Dog(Animal) 提取 parents=["Animal"]
- `test_language_property` — 验证 language 属性返回 "python"
- `test_decorator_handling` — 函数/类上的装饰器不影响提取

### 测试 fixture：共用的 Python 代码片段

```python
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
```

---

## 任务 3: extractor/relation.py — 结构关系提取器

### 文件：`src/layerkg/extractor/relation.py`

**从 ParseResult.entities 中提取结构关系：**

| 关系类型 | 提取逻辑 | 来源 |
|---------|---------|------|
| `contains` | module → function, module → class, class → method | AST 层级 |
| `extends` | class → parent_class | class_definition argument_list |
| `imports` | 不在 Phase 3 范围（Phase 1 做语义） | — |

**提取逻辑：**

```python
@dataclass
class ExtractedRelation:
    """提取的关系。"""
    source_name: str
    source_type: str
    target_name: str
    target_type: str
    relation_type: str  # "contains" | "extends"
    file_path: str


class RelationExtractor:
    """从 ParseResult 提取结构关系。"""

    def extract(self, parse_result: ParseResult) -> list[ExtractedRelation]:
        """从解析结果中提取所有结构关系。"""
        relations = []
        relations.extend(self._extract_contains(parse_result))
        relations.extend(self._extract_extends(parse_result))
        return relations

    def _extract_contains(self, parse_result: ParseResult) -> list[ExtractedRelation]:
        """提取 contains 关系。
        
        逻辑：
        1. 找到 module 实体
        2. 找到顶层 function 和 class → module contains function/class
        3. 找到 class 中的 function → class contains function
        """
        ...

    def _extract_extends(self, parse_result: ParseResult) -> list[ExtractedRelation]:
        """提取 extends 关系。
        
        逻辑：
        1. 遍历 entity_type="class" 的实体
        2. 如果有 parent_classes 字段且非空
        3. 对每个 parent 生成 class extends parent 关系
        """
        ...
```

### 测试：`tests/unit/test_relation_extractor.py`

- `test_extract_contains_module_to_functions` — module → function
- `test_extract_contains_module_to_classes` — module → class
- `test_extract_contains_class_to_methods` — class → method（通过行号范围判断）
- `test_extract_extends_single_parent` — Dog extends Animal
- `test_extract_extends_no_parent` — 无父类不生成关系
- `test_extract_empty_result` — 空输入返回空
- `test_extract_full_pipeline` — 完整解析+提取 pipeline
- `test_extract_contains_nested` — module → class → method 三级关系

---

## 编码规范提醒
- `from __future__ import annotations` 必须在每个文件头部
- 类型注解必须
- Google docstring
- ruff format/check 通过
- `uv run pytest tests/unit/test_parser_base.py tests/unit/test_python_parser.py tests/unit/test_relation_extractor.py -v`
- **禁止使用 tree_sitter.Query API** — 只用递归遍历 node.children
