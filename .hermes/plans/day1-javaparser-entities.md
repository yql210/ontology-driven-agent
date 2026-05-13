# Day 1 实现计划：JavaParser 核心实体提取

> 目标：创建 `JavaParser`，从 Java 源码提取 class/interface/enum/record/method/constructor/field/package/file 实体。
> Day 1 只做实体提取，不做关系提取（Day 2 做）。

## Java tree-sitter AST 节点类型映射

| Java 构造 | tree-sitter 节点类型 | CodeEntity entity_type |
|-----------|---------------------|----------------------|
| package | `package_declaration` | `module` |
| class | `class_declaration` | `class` |
| interface | `interface_declaration` | `interface` |
| enum | `enum_declaration` | `enum` |
| record | `record_declaration` | `record` |
| method | `method_declaration` | `function` |
| constructor | `constructor_declaration` | `function` |
| field | `field_declaration` | `field` |
| file | (文件本身) | `file` |

## 文件清单

### 新增文件

**1. `src/layerkg/parser/java_parser.py`** — JavaParser 实现

结构参照 `python_parser.py`（708 行），预估 ~400 行（Java 不需要 PythonParser 的 builtins 过滤）。

```python
from __future__ import annotations

from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from layerkg.parser.base import BaseParser, ExtractedRelation, ParseResult
from layerkg.schema import CodeEntity

JAVA_LANG = Language(tsjava.language())


class JavaParser(BaseParser):
    """Java 源码解析器，使用 tree-sitter 提取实体。"""

    def __init__(self) -> None:
        self._parser = Parser(JAVA_LANG)

    @property
    def language(self) -> str:
        return "java"

    def parse_file(self, file_path: Path) -> ParseResult:
        # 同 PythonParser：exists check → read_bytes → parse_source
        ...

    def parse_source(self, source: bytes, file_path: str = "<string>") -> ParseResult:
        # 1. 创建 file 实体（Java 用 file 而非 module）
        # 2. 解析 AST
        # 3. _walk 递归遍历
        ...

    def _walk(self, node, source, file_path, entities, relations, 
              package_name, parent_class_name=None):
        # 遍历顶层节点：
        # - package_declaration → 提取 package 名作为 module 实体
        # - import_declaration → 跳过（Day 2 做关系）
        # - class_declaration → _extract_class()
        # - interface_declaration → _extract_interface()
        # - enum_declaration → _extract_enum()
        # - record_declaration → _extract_record()
        # 对于 class/interface/enum/record 的 body，递归提取内部：
        # - method_declaration → _extract_method()
        # - constructor_declaration → _extract_constructor()
        # - field_declaration → _extract_field()
        ...

    def _extract_class(self, node, source, file_path, entities, relations, 
                       package_name, parent_class_name):
        # 获取 name: node.child_by_field_name("name")
        # 获取 modifiers: 遍历 children 找 "modifiers" 节点
        # entity_type = "class"
        # 创建 CodeEntity
        # 创建 contains 关系（package → class 或 parent_class → inner_class）
        # 递归 class_body 提取 method/constructor/field
        ...

    def _extract_interface(self, ...):
        # 同 _extract_class 但 entity_type = "interface"
        ...

    def _extract_enum(self, ...):
        # entity_type = "enum"
        # 提取 enum_constant（暂不创建实体，Day 2 可扩展）
        ...

    def _extract_record(self, ...):
        # entity_type = "record"
        # 注意 compact canonical constructor 参数
        ...

    def _extract_method(self, ...):
        # entity_type = "function"
        # name: node.child_by_field_name("name")
        # parameters: 遍历 formal_parameters 子节点
        # 返回类型: node.child_by_field_name("type")
        # docstring: 提取上方 Javadoc 注释
        # 如果在 class 内，full_name = ClassName.methodName
        ...

    def _extract_constructor(self, ...):
        # entity_type = "function"
        # name: 用所在 class 的名字（constructor 没有 name 字段，用 identifier 子节点）
        # parameters: 同 method
        ...

    def _extract_field(self, ...):
        # entity_type = "field"
        # field_declaration 结构: modifiers type variable_declarator (= value)?
        # variable_declarator 内有 identifier（字段名）
        # 可能一行声明多个字段: int x, y, z; → 产生多个 field 实体
        ...

    def _get_javadoc(self, node, source) -> str | None:
        # 检查 node 前一个兄弟节点是否是 block_comment 且以 /** 开头
        ...

    @staticmethod
    def _node_source(node, source: bytes) -> str:
        return node.text.decode("utf-8", errors="replace")
```

**关键设计决策**：

1. **Java 用 `file` 而非 `module` 表示文件**：Java 的 module 是 package 级别概念，单文件用 `file` 更准确。`package_declaration` 映射为 `module` 实体。
2. **constructor 的 name**：用所在 class 的名字（因为 Java constructor 名 = 类名）。
3. **field 可能一对多**：`int x, y, z;` 一个 field_declaration 产生 3 个 field 实体。
4. **Javadoc 提取**：检查节点前一个兄弟是否是 `block_comment` 且以 `/**` 开头。
5. **inner class**：支持嵌套，parent_class_name 传递机制同 PythonParser。
6. **modifiers 不创建实体**：仅作为元数据附加到对应实体（可放在 source 或后续扩展）。
7. **Day 1 的 relations 列表留空**：只做 `contains` 关系（file→class, class→method 等），其他关系 Day 2 做。`ExtractedRelation` 中 `contains` 关系可以在 Day 1 实现。

### 新增文件

**2. `tests/unit/test_java_parser.py`** — 测试文件

测试用例清单（~15 个）：

```python
# 基础解析
test_parse_simple_class          # class Foo { }
test_parse_class_with_methods    # class Foo { void bar() {} int baz() { return 1; } }
test_parse_class_with_fields     # class Foo { int x; String y; }
test_parse_class_with_constructor # class Foo { Foo(int x) { this.x = x; } }
test_parse_interface             # interface Animal { void speak(); }
test_parse_enum                  # enum Color { RED, GREEN, BLUE }
test_parse_record                # record Point(int x, int y) {}
test_parse_package               # package com.example; class Foo {}
test_parse_imports               # import java.util.List; (imports 不产生实体，Day 2)
test_parse_multiple_classes      # 一个文件多个 class
test_parse_inner_class           # class Foo { class Bar {} }
test_parse_javadoc               # /** doc */ public void foo() {}
test_parse_method_parameters     # void foo(int x, String y)
test_parse_multiple_fields       # int x, y, z; → 3 个 field 实体
test_parse_empty_file            # 空文件至少有 file 实体
test_parse_syntax_error          # 语法错误不崩溃

# 继承 BaseParser 接口
test_language_property           # parser.language == "java"
test_parse_file_not_found        # 文件不存在返回 error
```

### 修改文件

**3. `src/layerkg/parser/__init__.py`** — 导出 JavaParser

```python
from layerkg.parser.java_parser import JavaParser

__all__ = [
    ...,
    "JavaParser",
]
```

### 不修改的文件
- `builder.py` — Day 3 做
- `schema.py` — Day 0 已改
- `python_parser.py` — 不碰

## 执行顺序

1. 基线测试确认 827 passed
2. 创建 `src/layerkg/parser/java_parser.py`
3. 修改 `src/layerkg/parser/__init__.py` 导出 JavaParser
4. 创建 `tests/unit/test_java_parser.py`
5. 跑测试直到全绿
6. `ruff check` + `ruff format`
7. 全量测试确认 827 + 17+ = 844+ passed
8. `git add -A && git commit -m "feat(parser): add JavaParser with entity extraction (Day 1)"`

## 验证标准

- `uv run pytest tests/unit/test_java_parser.py -v` 全绿（17+ tests）
- `uv run pytest tests/ -v --tb=no -q` 全绿（844+ passed）
- `uv run python -c "from layerkg.parser import JavaParser; p = JavaParser(); print(p.language)"` 输出 "java"
- `uv run ruff check src/layerkg/parser/java_parser.py` 无错误
