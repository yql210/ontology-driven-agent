from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from layerkg.parser.python_parser import PythonParser

# Test code snippets
SIMPLE_CODE = b"""
def hello():
    print("hello")
"""

CLASS_CODE = b"""
class Animal:
    def speak(self):
        pass

class Dog(Animal):
    def speak(self):
        return "Woof"
"""

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

ASYNC_CODE = b"""
async def fetch_data():
    await some_io()
"""

DECORATOR_CODE = b"""
@staticmethod
def my_static():
    pass
"""

MULTI_INHERIT_CODE = b"""
class Dog(Animal, Pet):
    pass
"""

NESTED_CLASS_CODE = b"""
class Outer:
    class Inner:
        pass
    def method(self):
        pass
"""

LAMBDA_CODE = b"""
f = lambda x: x + 1
"""

IMPORT_CODE = b"""
import os
import sys.path
"""

IMPORT_FROM_CODE = b"""
from typing import Optional, List
from collections import defaultdict
"""

EMPTY_CODE = b""


@pytest.fixture
def parser() -> PythonParser:
    """创建 PythonParser 实例。"""
    return PythonParser()


def test_language_property(parser: PythonParser) -> None:
    """测试 language 属性返回 python。"""
    assert parser.language == "python"


def test_parse_simple_function(parser: PythonParser) -> None:
    """测试解析简单函数。"""
    result = parser.parse_source(SIMPLE_CODE, "test.py")

    assert result.error is None
    assert result.language == "python"

    # 应该有 module 和 function 两个实体
    entities_by_name = {e.name: e for e in result.entities}
    assert "test" in entities_by_name  # module
    assert entities_by_name["test"].entity_type == "module"

    assert "hello" in entities_by_name  # function
    hello = entities_by_name["hello"]
    assert hello.entity_type == "function"
    assert hello.start_line == 1
    assert hello.end_line == 2


def test_parse_class(parser: PythonParser) -> None:
    """测试解析类定义。"""
    result = parser.parse_source(CLASS_CODE, "animals.py")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # Module
    assert "animals" in entities_by_name
    assert entities_by_name["animals"].entity_type == "module"

    # Classes
    assert "Animal" in entities_by_name
    assert entities_by_name["Animal"].entity_type == "class"

    assert "Dog" in entities_by_name
    assert entities_by_name["Dog"].entity_type == "class"


def test_parse_class_methods_as_functions(parser: PythonParser) -> None:
    """测试类内方法也作为函数提取。"""
    result = parser.parse_source(CLASS_CODE, "animals.py")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # Animal.speak
    assert "Animal.speak" in entities_by_name
    assert entities_by_name["Animal.speak"].entity_type == "function"

    # Dog.speak (same name, different class)
    assert "Dog.speak" in entities_by_name
    assert entities_by_name["Dog.speak"].entity_type == "function"


def test_module_entity_always_present(parser: PythonParser) -> None:
    """测试总有 module 实体。"""
    result = parser.parse_source(SIMPLE_CODE, "test.py")

    entities_by_name = {e.name: e for e in result.entities}
    assert "test" in entities_by_name
    assert entities_by_name["test"].entity_type == "module"
    assert entities_by_name["test"].start_line == 0


def test_parse_empty_source(parser: PythonParser) -> None:
    """测试空源码只有 module 实体。"""
    result = parser.parse_source(EMPTY_CODE, "empty.py")

    assert result.error is None
    assert len(result.entities) == 1
    assert result.entities[0].name == "empty"
    assert result.entities[0].entity_type == "module"


def test_parse_syntax_error_resilient(parser: PythonParser) -> None:
    """测试语法错误不抛异常。"""
    bad_code = b"""
def foo(
    # Missing closing paren
    print("hello")
"""
    result = parser.parse_source(bad_code, "broken.py")

    # 应该返回结果但可能有 error
    # 不应该抛出异常
    assert result is not None
    assert result.file_path == "broken.py"


def test_parse_file_not_found(parser: PythonParser) -> None:
    """测试文件不存在返回 error。"""
    result = parser.parse_file(Path("/nonexistent/path.py"))

    assert result.error is not None
    assert "not found" in result.error.lower() or "no such" in result.error.lower()


def test_parse_multiple_functions(parser: PythonParser) -> None:
    """测试解析多个函数。"""
    code = b"""
def foo():
    pass

def bar():
    pass

def baz():
    pass
"""
    result = parser.parse_source(code, "multi.py")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "foo" in entities_by_name
    assert "bar" in entities_by_name
    assert "baz" in entities_by_name


def test_parse_class_with_inheritance(parser: PythonParser) -> None:
    """测试继承关系提取。"""
    result = parser.parse_source(CLASS_CODE, "animals.py")

    assert result.error is None

    # Dog extends Animal
    extends_relations = [r for r in result.relations if r.relation_type == "extends"]
    assert len(extends_relations) == 1
    assert extends_relations[0].source_name == "Dog"
    assert extends_relations[0].target_name == "Animal"


def test_parse_decorated_function(parser: PythonParser) -> None:
    """测试装饰器不影响函数提取。"""
    result = parser.parse_source(DECORATOR_CODE, "decorated.py")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "my_static" in entities_by_name
    assert entities_by_name["my_static"].entity_type == "function"


def test_parse_async_function(parser: PythonParser) -> None:
    """测试 async def 正确识别。"""
    result = parser.parse_source(ASYNC_CODE, "async_test.py")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "fetch_data" in entities_by_name
    assert entities_by_name["fetch_data"].entity_type == "function"


def test_parse_class_multi_inheritance(parser: PythonParser) -> None:
    """测试多继承关系。"""
    result = parser.parse_source(MULTI_INHERIT_CODE, "multi.py")

    assert result.error is None

    extends_relations = [r for r in result.relations if r.relation_type == "extends"]
    assert len(extends_relations) == 2

    source_names = {r.source_name for r in extends_relations}
    target_names = {r.target_name for r in extends_relations}

    assert "Dog" in source_names
    assert "Animal" in target_names
    assert "Pet" in target_names


def test_parse_nested_class(parser: PythonParser) -> None:
    """测试嵌套类。"""
    result = parser.parse_source(NESTED_CLASS_CODE, "nested.py")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # Outer class
    assert "Outer" in entities_by_name

    # Nested Inner class
    assert "Inner" in entities_by_name

    # Method in Outer
    assert "Outer.method" in entities_by_name


def test_lambda_not_extracted(parser: PythonParser) -> None:
    """测试 lambda 不作为函数实体提取。"""
    result = parser.parse_source(LAMBDA_CODE, "lambda_test.py")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 只应该有 module
    assert len(entities_by_name) == 1
    assert "lambda_test" in entities_by_name


def test_parse_import_statement(parser: PythonParser) -> None:
    """测试 import 语句生成 imports 关系。"""
    # IMPORT_CODE = b"\nimport os\nimport sys.path\n"
    # os -> 最后一段 "os"
    # sys.path -> 最后一段 "path"
    result = parser.parse_source(IMPORT_CODE, "imports.py")

    assert result.error is None

    imports_relations = [r for r in result.relations if r.relation_type == "imports"]
    assert len(imports_relations) == 2

    targets = [r.target_name for r in imports_relations]
    assert "os" in targets
    assert "path" in targets


def test_parse_import_from_statement(parser: PythonParser) -> None:
    """测试 from...import 语句生成 imports 关系。"""
    # IMPORT_FROM_CODE = b"\nfrom typing import Optional, List\nfrom collections import defaultdict\n"
    # from typing import Optional, List -> typing, Optional, List
    # from collections import defaultdict -> collections, defaultdict
    result = parser.parse_source(IMPORT_FROM_CODE, "from_import.py")

    assert result.error is None

    imports_relations = [r for r in result.relations if r.relation_type == "imports"]
    # 应该有 5 个: typing, Optional, List, collections, defaultdict
    assert len(imports_relations) >= 4

    targets = {r.target_name for r in imports_relations}
    assert "typing" in targets
    assert "Optional" in targets
    assert "List" in targets
    assert "collections" in targets
    assert "defaultdict" in targets


def test_imports_not_entity(parser: PythonParser) -> None:
    """测试 import 不生成实体。"""
    result = parser.parse_source(IMPORT_CODE, "imports.py")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 只有 module，没有 os 或 sys 实体
    assert "imports" in entities_by_name
    assert "os" not in entities_by_name
    assert "sys" not in entities_by_name


def test_contains_relation_for_functions(parser: PythonParser) -> None:
    """测试函数的 contains 关系。"""
    result = parser.parse_source(SIMPLE_CODE, "test.py")

    assert result.error is None

    contains_relations = [r for r in result.relations if r.relation_type == "contains"]
    assert len(contains_relations) == 1

    assert contains_relations[0].source_name == "test"  # module
    assert contains_relations[0].target_name == "hello"  # function


def test_contains_relation_for_class_methods(parser: PythonParser) -> None:
    """测试类方法的 contains 关系来源是 class。"""
    result = parser.parse_source(CLASS_CODE, "animals.py")

    assert result.error is None

    # Animal.speak 的 contains 来源应该是 Animal
    speak_contains = [r for r in result.relations if r.relation_type == "contains" and r.target_name == "Animal.speak"]
    assert len(speak_contains) == 1
    assert speak_contains[0].source_name == "Animal"


def test_parse_file_with_real_file(parser: PythonParser) -> None:
    """测试解析真实文件。"""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".py", delete=False) as f:
        f.write(SIMPLE_CODE)
        temp_path = Path(f.name)

    try:
        result = parser.parse_file(temp_path)

        assert result.error is None
        assert str(temp_path) in result.file_path
        entities_by_name = {e.name: e for e in result.entities}
        assert "hello" in entities_by_name
    finally:
        temp_path.unlink()


def test_module_end_line_calculation(parser: PythonParser) -> None:
    """测试 module 的 end_line 计算正确。"""
    multi_line = b"""line 1
line 2
line 3
"""
    result = parser.parse_source(multi_line, "lines.py")

    entities_by_name = {e.name: e for e in result.entities}
    module = entities_by_name["lines"]

    # 3 行 = 2 个换行符
    assert module.end_line == 2


def test_class_with_docstring(parser: PythonParser) -> None:
    """测试带 docstring 的类。"""
    result = parser.parse_source(COMPLEX_CODE, "service.py")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "Service" in entities_by_name
    assert "Service.process" in entities_by_name
    assert "Service._internal" in entities_by_name


def test_import_extracts_last_segment(parser: PythonParser) -> None:
    """测试 import os.path 提取最后一段 path。"""
    code = b"""
import os.path
import os.system
"""
    result = parser.parse_source(code, "test.py")

    assert result.error is None

    imports_relations = [r for r in result.relations if r.relation_type == "imports"]
    assert len(imports_relations) == 2

    targets = {r.target_name for r in imports_relations}
    assert "path" in targets
    assert "system" in targets


def test_import_from_extracts_last_segment(parser: PythonParser) -> None:
    """测试 from layerkg.schema import X 提取最后一段 schema。"""
    code = b"""
from layerkg.schema import CodeEntity
from collections.abc import Mapping
"""
    result = parser.parse_source(code, "test.py")

    assert result.error is None

    imports_relations = [r for r in result.relations if r.relation_type == "imports"]
    # 应该有：schema, CodeEntity, abc, Mapping
    assert len(imports_relations) >= 4

    targets = {r.target_name for r in imports_relations}
    assert "schema" in targets
    assert "abc" in targets
    assert "CodeEntity" in targets
    assert "Mapping" in targets


def test_import_from_with_specific_names(parser: PythonParser) -> None:
    """测试 from X import A, B 额外提取具名导入。"""
    code = b"""
from typing import Optional, List, Dict
"""
    result = parser.parse_source(code, "test.py")

    assert result.error is None

    imports_relations = [r for r in result.relations if r.relation_type == "imports"]
    # 应该有：typing (模块), Optional, List, Dict (具名导入)
    targets = {r.target_name for r in imports_relations}
    assert "typing" in targets
    assert "Optional" in targets
    assert "List" in targets
    assert "Dict" in targets


# Day 2: CALLS relation tests
CALL_CODE = b"""
def helper():
    pass

def caller():
    helper()
"""

SELF_METHOD_CODE = b"""
class Service:
    def method(self):
        self.helper()

    def helper(self):
        pass
"""

CLASS_METHOD_CODE = b"""
class Util:
    @staticmethod
    def parse():
        pass

def process():
    Util.parse()
"""

BUILTIN_CALL_CODE = b"""
def foo():
    print("hello")
    len([1, 2, 3])
"""

SHORT_NAME_CODE = b"""
def bar():
    fn()
    xy()
"""

NESTED_CALL_CODE = b"""
def check():
    if is_valid():
        process()

def is_valid():
    return True

def process():
    pass
"""


def test_extract_simple_call(parser: PythonParser) -> None:
    """测试简单函数调用提取 calls 关系。"""
    result = parser.parse_source(CALL_CODE, "test.py")

    assert result.error is None

    calls_relations = [r for r in result.relations if r.relation_type == "calls"]
    assert len(calls_relations) == 1
    assert calls_relations[0].source_name == "caller"
    assert calls_relations[0].target_name == "helper"


def test_extract_method_call_self(parser: PythonParser) -> None:
    """测试 self.method() 提取为 calls method。"""
    result = parser.parse_source(SELF_METHOD_CODE, "test.py")

    assert result.error is None

    calls_relations = [r for r in result.relations if r.relation_type == "calls"]
    assert len(calls_relations) == 1
    assert calls_relations[0].source_name == "Service.method"
    assert calls_relations[0].target_name == "helper"


def test_extract_method_call_class(parser: PythonParser) -> None:
    """测试 Class.method() 提取为 calls method。"""
    result = parser.parse_source(CLASS_METHOD_CODE, "test.py")

    assert result.error is None

    calls_relations = [r for r in result.relations if r.relation_type == "calls"]
    assert len(calls_relations) == 1
    assert calls_relations[0].source_name == "process"
    assert calls_relations[0].target_name == "parse"


def test_extract_call_filters_builtin(parser: PythonParser) -> None:
    """测试内置函数不生成 calls 关系。"""
    result = parser.parse_source(BUILTIN_CALL_CODE, "test.py")

    assert result.error is None

    calls_relations = [r for r in result.relations if r.relation_type == "calls"]
    # print 和 len 是内置函数，应该被过滤
    assert len(calls_relations) == 0


def test_extract_call_filters_short_name(parser: PythonParser) -> None:
    """测试短名称（<3字符）不生成 calls 关系。"""
    result = parser.parse_source(SHORT_NAME_CODE, "test.py")

    assert result.error is None

    calls_relations = [r for r in result.relations if r.relation_type == "calls"]
    # fn 和 xy 长度 < 3，应该被过滤
    assert len(calls_relations) == 0


def test_extract_nested_call(parser: PythonParser) -> None:
    """测试嵌套结构中提取多个 call。"""
    result = parser.parse_source(NESTED_CALL_CODE, "test.py")

    assert result.error is None

    calls_relations = [r for r in result.relations if r.relation_type == "calls"]
    assert len(calls_relations) == 2

    targets = {r.target_name for r in calls_relations}
    sources = {r.source_name for r in calls_relations}
    assert "is_valid" in targets
    assert "process" in targets
    assert "check" in sources
