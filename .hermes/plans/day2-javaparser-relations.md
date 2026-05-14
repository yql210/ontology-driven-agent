# Day 2 实现计划：JavaParser 关系提取

> 目标：在 JavaParser 中添加 extends/implements/imports/calls 四种关系提取。

## 当前状态
- JavaParser 697 行，只实现了 `contains` 关系
- 858 tests passed
- Day 1 代码中 `import_declaration` 有 `pass` 占位（第 169-171 行）

## Java AST 关系节点结构（已验证）

### extends（继承）
```
class_declaration > superclass > type_identifier = "Base"
```

### implements（实现）
```
class_declaration > super_interfaces > type_list > type_identifier = "Runnable"
                                                   > type_identifier = "Serializable"
```

### imports（导入）
```
import_declaration > scoped_identifier  # java.util.List
import_declaration > scoped_identifier + asterisk  # java.io.*
```
- 需要提取完整路径，取最后一段作为 target_name

### calls（方法调用）
```
method_invocation
  identifier = "items"       ← 对象
  .
  identifier = "size"        ← 方法名（取这个）
  argument_list

method_invocation
  identifier = "helper"      ← 直接调用（无对象前缀）

object_creation_expression
  new
  type_identifier = "Bar"    ← new 调用（取这个）
  argument_list

method_invocation
  this / super
  .
  identifier = "doSomething" ← this/super 调用
```

## 要修改的文件

### `src/layerkg/parser/java_parser.py`

#### 1. _walk 中添加 import 处理（第 169-171 行）

替换 `pass` 为 `_extract_import` 调用：
```python
elif node_type == "import_declaration":
    self._extract_import(node, source, file_path, relations, package_name)
```

#### 2. 新增 `_extract_import` 方法

```python
def _extract_import(self, node, source, file_path, relations, package_name):
    """提取 import 关系。"""
    # 找 scoped_identifier 或 identifier
    # 提取完整路径如 "java.util.List"
    # target_name 取最后一段 "List"
    # source_name 用 package_name 或 file_name
    # relation_type = "imports"
    # 注意 static import 和 wildcard import (java.io.*)
```

#### 3. 在 `_extract_class` 中添加 extends 提取

在现有 `_extract_class` 方法（第 207-259 行）中，创建实体之后添加：
```python
# 提取 extends 关系
for child in node.children:
    if child.type == "superclass":
        type_id = child.child_by_field_name("name")
        if type_id is None:
            for sub in child.children:
                if sub.type == "type_identifier":
                    type_id = sub
                    break
        if type_id:
            parent_name = type_id.text.decode("utf-8", errors="replace")
            relations.append(ExtractedRelation(
                source_name=class_name,
                source_type="class",
                target_name=parent_name,
                target_type="class",
                relation_type="extends",
                file_path=file_path,
            ))
```

#### 4. 在 `_extract_class` 中添加 implements 提取

```python
# 提取 implements 关系
for child in node.children:
    if child.type == "super_interfaces":
        type_list = None
        for sub in child.children:
            if sub.type == "type_list":
                type_list = sub
                break
        if type_list:
            for t in type_list.children:
                if t.type == "type_identifier":
                    iface_name = t.text.decode("utf-8", errors="replace")
                    relations.append(ExtractedRelation(
                        source_name=class_name,
                        source_type="class",
                        target_name=iface_name,
                        target_type="interface",
                        relation_type="implements",
                        file_path=file_path,
                    ))
```

#### 5. 新增 `_extract_calls` 方法（在 `_extract_method` 中调用）

```python
def _extract_calls(self, method_node, source, file_path, relations, caller_name):
    """BFS 遍历方法体，提取 method_invocation 和 object_creation_expression。"""
    queue = list(method_node.children)
    while queue:
        node = queue.pop(0)
        if node.type == "method_invocation":
            callee = self._extract_callee_name(node)
            if callee and len(callee) >= 2:
                relations.append(ExtractedRelation(
                    source_name=caller_name,
                    source_type="function",
                    target_name=callee,
                    target_type="function",
                    relation_type="calls",
                    file_path=file_path,
                ))
        elif node.type == "object_creation_expression":
            # new Bar() → calls Bar
            for child in node.children:
                if child.type == "type_identifier":
                    callee = child.text.decode("utf-8", errors="replace")
                    if len(callee) >= 2:
                        relations.append(ExtractedRelation(
                            source_name=caller_name,
                            source_type="function",
                            target_name=callee,
                            target_type="function",
                            relation_type="calls",
                            file_path=file_path,
                        ))
                    break
        queue.extend(node.children)

def _extract_callee_name(self, node):
    """从 method_invocation 提取被调用方法名。"""
    # method_invocation 的子节点中，最后一个 identifier 是方法名
    # 例如: items.size → "size", helper → "helper", this.doSomething → "doSomething"
    identifiers = [c for c in node.children if c.type == "identifier"]
    if identifiers:
        return identifiers[-1].text.decode("utf-8", errors="replace")
    return None
```

#### 6. 在 `_extract_method` 中调用 `_extract_calls`

在 `_extract_method` 末尾（第 522 行之后）添加：
```python
self._extract_calls(node, source, file_path, relations, full_name)
```

同理在 `_extract_constructor` 末尾（第 566 行之后）添加：
```python
self._extract_calls(node, source, file_path, relations, full_name)
```

#### 7. JDK 常用类过滤

添加 JDK 内置类名集合（类似 PythonParser 的 `_BUILTIN_NAMES`），过滤 `System`/`String`/`Object`/`Integer` 等：
```python
_JDK_COMMON_TYPES = {
    "String", "Integer", "Long", "Double", "Float", "Boolean",
    "Byte", "Short", "Character", "Object", "Class",
    "List", "Map", "Set", "ArrayList", "HashMap", "HashSet",
    "System", "Math", "Arrays", "Collections",
    "Exception", "RuntimeException", "Thread", "Runnable",
    "Override", "Deprecated", "SuppressWarnings",
    "println", "print", "format", "valueOf", "toString",
    "equals", "hashCode", "compareTo", "getClass",
    "intValue", "longValue", "doubleValue", "floatValue",
    "booleanValue", "byteValue", "shortValue", "charValue",
}
```

## 测试文件：`tests/unit/test_java_parser.py`

在现有 31 个测试基础上追加 ~12 个：

```python
# extends 关系
test_extends_relation
test_extends_with_generic  # class Foo<T> extends Base<T>
test_no_extends_no_relation  # 无 extends 不产生关系

# implements 关系
test_implements_relation
test_implements_multiple  # implements A, B, C

# imports 关系
test_import_single  # import java.util.List
test_import_wildcard  # import java.io.*
test_import_static  # import static org.junit.Assert.assertEquals

# calls 关系
test_method_call_relation  # items.size()
test_direct_method_call  # helper()
test_new_object_call  # new Bar()
test_this_super_call  # this.doSomething(), super.init()
test_calls_filtered_jdk  # System.out.println 被过滤
test_method_with_multiple_calls
test_no_calls_no_relation

# 组合
test_class_with_extends_implements_and_methods_with_calls
```

## 执行顺序

1. 基线测试确认 858 passed
2. 修改 `java_parser.py`：添加 4 种关系提取
3. 追加测试到 `test_java_parser.py`
4. 跑测试直到全绿
5. `ruff check` + `ruff format`
6. 全量测试确认 870+ passed
7. `git add -A && git commit -m "feat(parser): add relation extraction to JavaParser - extends/implements/imports/calls (Day 2)"`

## 验证标准

- `uv run pytest tests/unit/test_java_parser.py -v` 全绿（43+ tests）
- `uv run pytest tests/ -v --tb=no -q` 全绿
- extends: `class Foo extends Bar` → ExtractedRelation(source="Foo", target="Bar", type="extends")
- implements: `class Foo implements A, B` → 2 个 implements 关系
- imports: `import java.util.List` → ExtractedRelation(source=file, target="List", type="imports")
- calls: `items.size()` → ExtractedRelation(source=Foo.bar, target="size", type="calls")
