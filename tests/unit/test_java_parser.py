from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from layerkg.parser.java_parser import JavaParser

# Test code snippets
SIMPLE_CLASS = b"""
public class Foo {
}
"""

CLASS_WITH_METHODS = b"""
public class Foo {
    public void bar() {
    }

    public int baz() {
        return 1;
    }
}
"""

CLASS_WITH_FIELDS = b"""
public class Foo {
    private int x;
    public String y;
}
"""

CLASS_WITH_CONSTRUCTOR = b"""
public class Foo {
    private int x;

    public Foo(int x) {
        this.x = x;
    }
}
"""

INTERFACE_CODE = b"""
interface Animal {
    void speak();
}
"""

ENUM_CODE = b"""
enum Color {
    RED, GREEN, BLUE
}
"""

RECORD_CODE = b"""
record Point(int x, int y) {
}
"""

PACKAGE_CODE = b"""
package com.example;

public class Foo {
}
"""

IMPORTS_CODE = b"""
import java.util.List;
import java.util.ArrayList;

public class Foo {
}
"""

MULTIPLE_CLASSES = b"""
class Foo {
}

class Bar {
}
"""

INNER_CLASS_CODE = b"""
class Outer {
    class Inner {
    }
}
"""

JAVADOC_CODE = b"""
class Test {
    /**
     * This is a test method.
     */
    public void foo() {
    }
}
"""

METHOD_PARAMETERS = b"""
class Test {
    void foo(int x, String y) {
    }
}
"""

MULTIPLE_FIELDS = b"""
class Foo {
    int x, y, z;
}
"""

EMPTY_FILE = b""

SYNTAX_ERROR = b"""
class Foo {
    public void method(
    // Missing closing paren
}
"""


@pytest.fixture
def parser() -> JavaParser:
    """创建 JavaParser 实例。"""
    return JavaParser()


def test_language_property(parser: JavaParser) -> None:
    """测试 language 属性返回 java。"""
    assert parser.language == "java"


def test_parse_simple_class(parser: JavaParser) -> None:
    """测试解析简单类。"""
    result = parser.parse_source(SIMPLE_CLASS, "Foo.java")

    assert result.error is None
    assert result.language == "java"

    entities_by_name = {e.name: e for e in result.entities}

    # file 实体
    assert "Foo.java" in entities_by_name
    assert entities_by_name["Foo.java"].entity_type == "file"

    # class 实体
    assert "Foo" in entities_by_name
    assert entities_by_name["Foo"].entity_type == "class"


def test_parse_class_with_methods(parser: JavaParser) -> None:
    """测试解析带方法的类。"""
    result = parser.parse_source(CLASS_WITH_METHODS, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 类
    assert "Foo" in entities_by_name
    assert entities_by_name["Foo"].entity_type == "class"

    # 方法
    assert "Foo.bar" in entities_by_name
    assert entities_by_name["Foo.bar"].entity_type == "function"

    assert "Foo.baz" in entities_by_name
    assert entities_by_name["Foo.baz"].entity_type == "function"


def test_parse_class_with_fields(parser: JavaParser) -> None:
    """测试解析带字段的类。"""
    result = parser.parse_source(CLASS_WITH_FIELDS, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 类
    assert "Foo" in entities_by_name

    # 字段
    assert "x" in entities_by_name
    assert entities_by_name["x"].entity_type == "field"

    assert "y" in entities_by_name
    assert entities_by_name["y"].entity_type == "field"


def test_parse_class_with_constructor(parser: JavaParser) -> None:
    """测试解析带构造器的类。"""
    result = parser.parse_source(CLASS_WITH_CONSTRUCTOR, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 类
    assert "Foo" in entities_by_name

    # 构造器
    assert "Foo.<init>" in entities_by_name
    assert entities_by_name["Foo.<init>"].entity_type == "function"


def test_parse_interface(parser: JavaParser) -> None:
    """测试解析接口。"""
    result = parser.parse_source(INTERFACE_CODE, "Animal.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 接口
    assert "Animal" in entities_by_name
    assert entities_by_name["Animal"].entity_type == "interface"


def test_parse_enum(parser: JavaParser) -> None:
    """测试解析枚举。"""
    result = parser.parse_source(ENUM_CODE, "Color.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 枚举
    assert "Color" in entities_by_name
    assert entities_by_name["Color"].entity_type == "enum"


def test_parse_record(parser: JavaParser) -> None:
    """测试解析 record。"""
    result = parser.parse_source(RECORD_CODE, "Point.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # record
    assert "Point" in entities_by_name
    assert entities_by_name["Point"].entity_type == "record"


def test_parse_package(parser: JavaParser) -> None:
    """测试解析 package 声明。"""
    result = parser.parse_source(PACKAGE_CODE, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # module 实体（来自 package）
    assert "com.example" in entities_by_name
    assert entities_by_name["com.example"].entity_type == "module"

    # 类
    assert "Foo" in entities_by_name
    assert entities_by_name["Foo"].entity_type == "class"

    # contains 关系
    contains_relations = [r for r in result.relations if r.relation_type == "contains"]
    package_contains = [r for r in contains_relations if r.source_name == "com.example"]
    assert len(package_contains) >= 1


def test_parse_imports(parser: JavaParser) -> None:
    """测试解析 import 语句（不产生实体，Day 2 做关系）。"""
    result = parser.parse_source(IMPORTS_CODE, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # file 和 class 存在
    assert "Foo.java" in entities_by_name
    assert "Foo" in entities_by_name

    # import 不产生实体
    assert "List" not in entities_by_name
    assert "ArrayList" not in entities_by_name


def test_parse_multiple_classes(parser: JavaParser) -> None:
    """测试解析一个文件多个类。"""
    result = parser.parse_source(MULTIPLE_CLASSES, "test.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 两个类都应该被提取
    assert "Foo" in entities_by_name
    assert "Bar" in entities_by_name


def test_parse_inner_class(parser: JavaParser) -> None:
    """测试解析内部类。"""
    result = parser.parse_source(INNER_CLASS_CODE, "test.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 外部类
    assert "Outer" in entities_by_name

    # 内部类
    assert "Inner" in entities_by_name


def test_parse_javadoc(parser: JavaParser) -> None:
    """测试解析 Javadoc 注释。"""
    result = parser.parse_source(JAVADOC_CODE, "Test.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 类
    assert "Test" in entities_by_name

    # 方法应该有 docstring
    assert "Test.foo" in entities_by_name
    foo = entities_by_name["Test.foo"]
    assert "This is a test method" in foo.docstring


def test_parse_method_parameters(parser: JavaParser) -> None:
    """测试解析方法参数。"""
    result = parser.parse_source(METHOD_PARAMETERS, "Test.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "Test.foo" in entities_by_name
    foo = entities_by_name["Test.foo"]
    assert foo.parameters is not None
    assert "int x" in foo.parameters
    assert "String y" in foo.parameters


def test_parse_multiple_fields(parser: JavaParser) -> None:
    """测试一行声明多个字段。"""
    result = parser.parse_source(MULTIPLE_FIELDS, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 应该有 3 个字段实体
    assert "x" in entities_by_name
    assert "y" in entities_by_name
    assert "z" in entities_by_name

    # 都是 field 类型
    assert entities_by_name["x"].entity_type == "field"
    assert entities_by_name["y"].entity_type == "field"
    assert entities_by_name["z"].entity_type == "field"


def test_parse_empty_file(parser: JavaParser) -> None:
    """测试空文件至少有 file 实体。"""
    result = parser.parse_source(EMPTY_FILE, "Empty.java")

    assert result.error is None
    assert len(result.entities) == 1
    assert result.entities[0].name == "Empty.java"
    assert result.entities[0].entity_type == "file"


def test_parse_syntax_error_resilient(parser: JavaParser) -> None:
    """测试语法错误不崩溃。"""
    result = parser.parse_source(SYNTAX_ERROR, "broken.java")

    # 应该返回结果但可能有 error
    # 不应该抛出异常
    assert result is not None
    assert result.file_path == "broken.java"
    # 至少有 file 实体
    assert len(result.entities) >= 1


def test_parse_file_not_found(parser: JavaParser) -> None:
    """测试文件不存在返回 error。"""
    result = parser.parse_file(Path("/nonexistent/path.java"))

    assert result.error is not None
    assert "not found" in result.error.lower() or "no such" in result.error.lower()


def test_contains_relation_for_class_methods(parser: JavaParser) -> None:
    """测试类方法的 contains 关系来源是 class。"""
    result = parser.parse_source(CLASS_WITH_METHODS, "Foo.java")

    assert result.error is None

    # Foo.bar 的 contains 来源应该是 Foo
    bar_contains = [r for r in result.relations if r.relation_type == "contains" and r.target_name == "Foo.bar"]
    assert len(bar_contains) == 1
    assert bar_contains[0].source_name == "Foo"
    assert bar_contains[0].source_type == "class"


def test_contains_relation_for_class_fields(parser: JavaParser) -> None:
    """测试字段的 contains 关系来源是 class。"""
    result = parser.parse_source(CLASS_WITH_FIELDS, "Foo.java")

    assert result.error is None

    # x 字段的 contains 来源应该是 Foo
    x_contains = [r for r in result.relations if r.relation_type == "contains" and r.target_name == "x"]
    assert len(x_contains) == 1
    assert x_contains[0].source_name == "Foo"
    assert x_contains[0].source_type == "class"


def test_javadoc_on_class(parser: JavaParser) -> None:
    """测试类上的 Javadoc 注释。"""
    code = b"""
/**
 * A test class.
 */
class Foo {
}
"""
    result = parser.parse_source(code, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "Foo" in entities_by_name
    foo = entities_by_name["Foo"]
    assert foo.docstring == "A test class."


def test_javadoc_on_method(parser: JavaParser) -> None:
    """测试方法上的 Javadoc 注释。"""
    code = b"""
class Foo {
    /**
     * A test method.
     */
    void bar() {
    }
}
"""
    result = parser.parse_source(code, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "Foo.bar" in entities_by_name
    bar = entities_by_name["Foo.bar"]
    assert bar.docstring == "A test method."


def test_parse_file_with_real_file(parser: JavaParser) -> None:
    """测试解析真实文件。"""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".java", delete=False) as f:
        f.write(SIMPLE_CLASS)
        temp_path = Path(f.name)

    try:
        result = parser.parse_file(temp_path)

        assert result.error is None
        assert str(temp_path) in result.file_path
        entities_by_name = {e.name: e for e in result.entities}
        assert "Foo" in entities_by_name
    finally:
        temp_path.unlink()


def test_constructor_has_parameters(parser: JavaParser) -> None:
    """测试构造器参数提取。"""
    result = parser.parse_source(CLASS_WITH_CONSTRUCTOR, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "Foo.<init>" in entities_by_name
    constructor = entities_by_name["Foo.<init>"]
    assert constructor.parameters is not None
    assert "int x" in constructor.parameters


def test_no_package_no_module_entity(parser: JavaParser) -> None:
    """测试没有 package 声明时不创建 module 实体。"""
    result = parser.parse_source(SIMPLE_CLASS, "Foo.java")

    assert result.error is None
    entities_by_type = {e.entity_type for e in result.entities}

    # 只有 file 和 class，没有 module
    assert "file" in entities_by_type
    assert "class" in entities_by_type
    assert "module" not in entities_by_type


def test_record_with_method(parser: JavaParser) -> None:
    """测试 record 带方法。"""
    code = b"""
record Point(int x, int y) {
    public int sum() {
        return x + y;
    }
}
"""
    result = parser.parse_source(code, "Point.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "Point" in entities_by_name
    assert entities_by_name["Point"].entity_type == "record"

    assert "Point.sum" in entities_by_name
    assert entities_by_name["Point.sum"].entity_type == "function"


def test_enum_with_method(parser: JavaParser) -> None:
    """测试 enum 带方法。"""
    code = b"""
enum Color {
    RED, GREEN, BLUE;

    public String getName() {
        return name();
    }
}
"""
    result = parser.parse_source(code, "Color.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "Color" in entities_by_name
    assert entities_by_name["Color"].entity_type == "enum"

    assert "Color.getName" in entities_by_name
    assert entities_by_name["Color.getName"].entity_type == "function"


def test_interface_with_method(parser: JavaParser) -> None:
    """测试 interface 带方法。"""
    code = b"""
interface Animal {
    void speak();

    default void sleep() {
    }
}
"""
    result = parser.parse_source(code, "Animal.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "Animal" in entities_by_name
    assert entities_by_name["Animal"].entity_type == "interface"

    # 接口方法
    assert "Animal.speak" in entities_by_name
    assert "Animal.sleep" in entities_by_name


def test_class_implements_and_extends(parser: JavaParser) -> None:
    """测试类带 extends 和 implements（Day 2 做关系）。"""
    code = b"""
class Foo extends BaseClass implements Runnable {
    public void run() {
    }
}
"""
    result = parser.parse_source(code, "test.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    # 类应该被提取
    assert "Foo" in entities_by_name
    assert "Foo.run" in entities_by_name


def test_field_end_line(parser: JavaParser) -> None:
    """测试字段有正确的行号。"""
    result = parser.parse_source(CLASS_WITH_FIELDS, "Foo.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    if "x" in entities_by_name:
        x = entities_by_name["x"]
        assert x.start_line is not None
        assert x.end_line is not None


def test_varargs_parameter(parser: JavaParser) -> None:
    """测试可变参数。"""
    code = b"""
class Test {
    void foo(String... args) {
    }
}
"""
    result = parser.parse_source(code, "Test.java")

    assert result.error is None
    entities_by_name = {e.name: e for e in result.entities}

    assert "Test.foo" in entities_by_name
    foo = entities_by_name["Test.foo"]
    assert foo.parameters is not None
    assert "String... args" in foo.parameters
