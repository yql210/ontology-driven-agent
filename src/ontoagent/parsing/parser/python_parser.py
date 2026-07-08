from __future__ import annotations

import json
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from ontoagent.domain.schema import CodeEntity
from ontoagent.parsing.extractor.entry_point_rules import classify_python_decorator
from ontoagent.parsing.parser.base import BaseParser, ExtractedRelation, ParseResult

PY_LANG = Language(tspython.language())

# 内置函数和异常类型名（约40个），用于过滤 calls 关系
_BUILTIN_NAMES = {
    # 内置函数
    "abs",
    "all",
    "any",
    "ascii",
    "bin",
    "bool",
    "breakpoint",
    "bytearray",
    "bytes",
    "callable",
    "chr",
    "classmethod",
    "compile",
    "complex",
    "delattr",
    "dict",
    "dir",
    "divmod",
    "enumerate",
    "eval",
    "exec",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "globals",
    "hasattr",
    "hash",
    "help",
    "hex",
    "id",
    "input",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "locals",
    "map",
    "max",
    "memoryview",
    "min",
    "next",
    "object",
    "oct",
    "open",
    "ord",
    "pow",
    "print",
    "property",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "setattr",
    "slice",
    "sorted",
    "staticmethod",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "vars",
    "zip",
    # 内置异常类型
    "Exception",
    "BaseException",
    "SystemExit",
    "KeyboardInterrupt",
    "GeneratorExit",
    "ArithmeticError",
    "FloatingPointError",
    "OverflowError",
    "ZeroDivisionError",
    "AssertionError",
    "AttributeError",
    "BufferError",
    "EOFError",
    "ImportError",
    "ModuleNotFoundError",
    "LookupError",
    "IndexError",
    "KeyError",
    "MemoryError",
    "NameError",
    "OSError",
    "FileExistsError",
    "FileNotFoundError",
    "IsADirectoryError",
    "NotADirectoryError",
    "PermissionError",
    "RuntimeError",
    "StopAsyncIteration",
    "StopIteration",
    "SyntaxError",
    "IndentationError",
    "TabError",
    "ReferenceError",
    "TypeError",
    "ValueError",
}


class PythonParser(BaseParser):
    """Python 源码解析器，使用 tree-sitter 提取实体和关系。"""

    def __init__(self) -> None:
        """初始化解析器。"""
        self._parser = Parser(PY_LANG)

    @property
    def language(self) -> str:
        """返回解析器语言名称。"""
        return "python"

    def _create_root_entity(self, source: bytes, file_path: str) -> CodeEntity:
        """Create the module entity for this Python file."""
        module_name = Path(file_path).stem
        if source:
            newline_count = source.count(b"\n")
            end_line = newline_count - 1 if source.endswith(b"\n") else newline_count
        else:
            end_line = 0
        source_text = source.decode("utf-8", errors="replace")
        return CodeEntity(
            name=module_name,
            entity_type="module",
            file_path=file_path,
            start_line=0,
            end_line=end_line,
            language="python",
            source=source_text[:500],
        )

    def _pre_scan(self, root_node, source, file_path, entities, relations) -> str | None:
        """Python pre-scan: return module name (stem of file path)."""
        return Path(file_path).stem

    def _extract_external_calls(self, root_node, source, file_path, module_name) -> list[ExtractedRelation]:
        from ontoagent.parsing.extractor.external_calls import extract_external_calls_python
        return extract_external_calls_python(root_node, source, module_name, file_path)

    def _walk(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        module_name: str,
        parent_class_name: str | None = None,
    ) -> None:
        """递归遍历 AST 节点。

        Args:
            node: tree-sitter 节点。
            source: 源码字节流。
            file_path: 文件路径。
            entities: 实体列表（累积）。
            relations: 关系列表（累积）。
            module_name: 模块名。
            parent_class_name: 当前所在类名（用于处理嵌套）。
        """
        if node is None:
            return

        node_type = node.type

        # 装饰器定义（在函数/类定义之前处理）
        if node_type == "decorated_definition":
            decorators = self._extract_decorators_from_node(node, source)
            # 遍历 children 找内层的 function_definition 或 class_definition
            for child in node.children:
                if child.type == "function_definition":
                    self._extract_function(
                        child,
                        source,
                        file_path,
                        entities,
                        relations,
                        module_name,
                        parent_class_name,
                        decorators=decorators,
                    )
                elif child.type == "class_definition":
                    self._extract_class(
                        child,
                        source,
                        file_path,
                        entities,
                        relations,
                        module_name,
                        parent_class_name,
                        decorators=decorators,
                    )
            # 不继续递归子节点，避免重复提取
            return

        # 函数定义
        elif node_type == "function_definition":
            self._extract_function(node, source, file_path, entities, relations, module_name, parent_class_name)
            # 不继续递归子节点，避免重复提取
            return

        # 类定义
        elif node_type == "class_definition":
            class_name = self._extract_class(
                node, source, file_path, entities, relations, module_name, parent_class_name
            )
            # 继续遍历类内部
            for child in node.children:
                self._walk(child, source, file_path, entities, relations, module_name, parent_class_name=class_name)
            return

        # 导入语句
        elif node_type == "import_statement":
            self._extract_import(node, source, file_path, entities, relations, module_name)

        # from...import 语句
        elif node_type == "import_from_statement":
            self._extract_import_from(node, source, file_path, entities, relations, module_name)

        # 递归子节点
        for child in node.children:
            self._walk(child, source, file_path, entities, relations, module_name, parent_class_name)

    def _extract_function(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        module_name: str,
        parent_class_name: str | None,
        decorators: list[dict] | None = None,
    ) -> None:
        """提取函数实体。

        Args:
            node: function_definition 节点。
            source: 源码字节流。
            file_path: 文件路径。
            entities: 实体列表。
            relations: 关系列表。
            module_name: 模块名。
            parent_class_name: 父类名。
            decorators: 装饰器列表（来自 decorated_definition）。
        """
        # 获取函数名
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        func_name = name_node.text.decode()

        # 如果在类内部，使用类名前缀
        full_name = f"{parent_class_name}.{func_name}" if parent_class_name else func_name

        start_line = node.start_point[0]
        end_line = node.end_point[0]

        source_text = node.text.decode("utf-8", errors="replace")
        docstring = self._extract_docstring(node, source)
        parameters = self._extract_parameters(node)

        entity = CodeEntity(
            name=full_name,
            entity_type="function",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="python",
            source=source_text,
            docstring=docstring,
            parameters=parameters,
        )

        # 装饰器分类：一个函数只取第一个匹配的分类
        if decorators:
            for dec in decorators:
                category = classify_python_decorator(dec["attr_path"], dec["name"])
                if category is not None:
                    entity.entry_category = category
                    # Build entry_metadata from decorator args
                    args = dec.get("args", [])
                    if category == "http_api" and args:
                        entity.entry_metadata = f'{{"route": "{args[0]}"}}'
                    elif category == "scheduled" and args:
                        entity.entry_metadata = f'{{"schedule": "{args[0]}"}}'
                    elif category == "mq_consumer" and args:
                        entity.entry_metadata = f'{{"topic": "{args[0]}"}}'
                    elif category == "event_handler" and args:
                        entity.entry_metadata = f'{{"event": "{args[0]}"}}'
                    break

        entities.append(entity)

        # 创建 contains 关系
        source_entity = parent_class_name if parent_class_name else module_name
        relation = ExtractedRelation(
            source_name=source_entity,
            source_type="class" if parent_class_name else "module",
            target_name=full_name,
            target_type="function",
            relation_type="contains",
            file_path=file_path,
        )
        relations.append(relation)

        # 提取调用关系
        self._extract_calls(node, source, file_path, relations, module_name, full_name)

    def _extract_class(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        module_name: str,
        parent_class_name: str | None,
        decorators: list[dict] | None = None,
    ) -> str | None:
        """提取类实体。

        Args:
            node: class_definition 节点。
            source: 源码字节流。
            file_path: 文件路径。
            entities: 实体列表。
            relations: 关系列表。
            module_name: 模块名。
            parent_class_name: 父类名（用于嵌套类）。
            decorators: 装饰器列表（来自 decorated_definition，Phase 2）。

        Returns:
            类名，用于后续嵌套遍历。
        """
        # 获取类名
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        class_name = name_node.text.decode()

        start_line = node.start_point[0]
        end_line = node.end_point[0]

        source_text = node.text.decode("utf-8", errors="replace")
        docstring = self._extract_docstring(node, source)

        entity = CodeEntity(
            name=class_name,
            entity_type="class",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="python",
            source=source_text,
            docstring=docstring,
        )
        entities.append(entity)

        # 创建 contains 关系（从模块或父类）
        source_entity = parent_class_name if parent_class_name else module_name
        source_type = "class" if parent_class_name else "module"
        relation = ExtractedRelation(
            source_name=source_entity,
            source_type=source_type,
            target_name=class_name,
            target_type="class",
            relation_type="contains",
            file_path=file_path,
        )
        relations.append(relation)

        # 提取继承关系
        arg_list = node.child_by_field_name("superclasses")
        if arg_list is not None:
            for child in arg_list.children:
                if child.type == "identifier" or child.type == "attribute":
                    parent_name = child.text.decode()
                    # 处理 module.Class 形式，只取类名
                    if "." in parent_name:
                        parent_name = parent_name.split(".")[-1]
                    relation = ExtractedRelation(
                        source_name=class_name,
                        source_type="class",
                        target_name=parent_name,
                        target_type="class",
                        relation_type="extends",
                        file_path=file_path,
                    )
                    relations.append(relation)

        return class_name

    def _extract_import(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        module_name: str,
    ) -> None:
        """提取 import 语句。

        Args:
            node: import_statement 节点。
            source: 源码字节流。
            file_path: 文件路径。
            entities: 实体列表。
            relations: 关系列表。
            module_name: 模块名。
        """
        for child in node.children:
            if child.type == "dotted_name" or child.type == "aliased_import":
                if child.type == "aliased_import":
                    # 处理 import x as y
                    name_part = child.child_by_field_name("name")
                    if name_part:
                        import_name = name_part.text.decode()
                    else:
                        continue
                else:
                    import_name = child.text.decode()

                # 取最后一段（如 os.path -> path）
                last_segment = import_name.split(".")[-1]

                relation = ExtractedRelation(
                    source_name=module_name,
                    source_type="module",
                    target_name=last_segment,
                    target_type="module",
                    relation_type="imports",
                    file_path=file_path,
                )
                relations.append(relation)

    def _extract_import_from(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        module_name: str,
    ) -> None:
        """提取 from...import 语句。

        Args:
            node: import_from_statement 节点。
            source: 源码字节流。
            file_path: 文件路径。
            entities: 实体列表。
            relations: 关系列表。
            module_name: 模块名。
        """
        # 获取源模块名
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            return

        import_module = module_node.text.decode()
        # 取最后一段（如 ontoagent.domain.schema -> schema）
        last_segment = import_module.split(".")[-1]

        relation = ExtractedRelation(
            source_name=module_name,
            source_type="module",
            target_name=last_segment,
            target_type="module",
            relation_type="imports",
            file_path=file_path,
        )
        relations.append(relation)

        # 额外提取具名导入（from X import A, B）
        for child in node.children:
            if child.type == "identifier":
                name = child.text.decode()
                relations.append(
                    ExtractedRelation(
                        source_name=module_name,
                        source_type="module",
                        target_name=name,
                        target_type="module",
                        relation_type="imports",
                        file_path=file_path,
                    )
                )
            elif child.type == "dotted_name":
                # from X import Y.Z -> 提取 Y 和 Z
                name = child.text.decode()
                segments = name.split(".")
                for seg in segments:
                    relations.append(
                        ExtractedRelation(
                            source_name=module_name,
                            source_type="module",
                            target_name=seg,
                            target_type="module",
                            relation_type="imports",
                            file_path=file_path,
                        )
                    )
            elif child.type == "aliased_import":
                # from X import Y as Z -> 提取 Y
                name_part = child.child_by_field_name("name")
                if name_part:
                    name = name_part.text.decode()
                    relations.append(
                        ExtractedRelation(
                            source_name=module_name,
                            source_type="module",
                            target_name=name,
                            target_type="module",
                            relation_type="imports",
                            file_path=file_path,
                        )
                    )

    def _extract_calls(
        self,
        func_node,
        source: bytes,
        file_path: str,
        relations: list[ExtractedRelation],
        module_name: str,
        caller_name: str,
    ) -> None:
        """提取函数内的调用关系。

        Args:
            func_node: function_definition 节点。
            source: 源码字节流。
            file_path: 文件路径。
            relations: 关系列表（累积）。
            module_name: 模块名。
            caller_name: 调用者函数名。
        """
        # BFS 遍历函数体，找 call 节点
        queue = list(func_node.children)

        while queue:
            node = queue.pop(0)

            if node.type == "call":
                # 提取被调用函数名
                callee_name = self._extract_callee_name(node)
                # 过滤内置名和短名称
                if callee_name and callee_name not in _BUILTIN_NAMES and len(callee_name) >= 3:
                    relation = ExtractedRelation(
                        source_name=caller_name,
                        source_type="function",
                        target_name=callee_name,
                        target_type="function",
                        relation_type="calls",
                        file_path=file_path,
                    )
                    relations.append(relation)

            # 继续遍历子节点
            queue.extend(node.children)

    def _extract_callee_name(self, call_node) -> str | None:
        """从 call 节点提取被调用函数名。

        Args:
            call_node: call 类型节点。

        Returns:
            被调用函数名，处理 self.method / Class.method 形式。
        """
        # call 节点的第一个子节点通常是函数引用
        func_node = call_node.child_by_field_name("function")
        if func_node is None:
            # 如果没有 function 字段，取第一个子节点
            children = call_node.children
            if not children:
                return None
            func_node = children[0]

        # 根据 func_node 类型提取名称
        node_type = func_node.type

        if node_type == "identifier":
            return func_node.text.decode()
        elif node_type == "attribute":
            # self.method 或 Class.method -> 取最后一段
            text = func_node.text.decode()
            parts = text.split(".")
            return parts[-1] if parts else None
        elif node_type == "call":
            # 嵌套调用如 foo()()，递归提取
            return self._extract_callee_name(func_node)

        return None

    def _extract_decorators_from_node(self, node, source: bytes) -> list[dict]:
        """从 decorated_definition 节点提取装饰器列表。

        Args:
            node: decorated_definition 节点。
            source: 源码字节流。

        Returns:
            装饰器 dict 列表，每个 dict 包含 name, attr_path, args。
        """
        decorators: list[dict] = []
        for child in node.children:
            if child.type == "decorator":
                dec_info = self._extract_single_decorator(child, source)
                if dec_info:
                    decorators.append(dec_info)
        return decorators

    def _extract_single_decorator(self, decorator_node, source: bytes) -> dict | None:
        """从单个 decorator 节点提取装饰器信息。

        Args:
            decorator_node: decorator 类型节点。
            source: 源码字节流。

        Returns:
            dict with name, attr_path, args keys, or None.
        """
        # decorator 结构: children[0]='@', children[1]=call/identifier/attribute
        if len(decorator_node.children) < 2:
            return None

        target = decorator_node.children[1]
        target_type = target.type

        name = target.text.decode()
        attr_path: list[str] = []
        args: list[str] = []

        if target_type == "identifier":
            # @decorator_name
            name = target.text.decode()
            attr_path = []
        elif target_type == "attribute":
            # @app.route
            name = target.text.decode()
            # 遍历 attribute 的子节点中 type=="identifier" 的组成 attr_path
            for gc in target.children:
                if gc.type == "identifier":
                    attr_path.append(gc.text.decode())
        elif target_type == "call":
            # @app.route("/path")
            name = target.text.decode().split("(")[0]
            # 从 call 的 children[0] 获取 attribute/identifier
            if target.children:
                func_ref = target.children[0]
                if func_ref.type == "identifier":
                    name = func_ref.text.decode()
                    attr_path = []
                elif func_ref.type == "attribute":
                    name = func_ref.text.decode()
                    for gc in func_ref.children:
                        if gc.type == "identifier":
                            attr_path.append(gc.text.decode())
                # 提取调用参数
                args = self._extract_decorator_args(target, source)
        else:
            return None

        return {"name": name, "attr_path": tuple(attr_path), "args": args}

    def _extract_decorator_args(self, call_node, source: bytes) -> list[str]:
        """从 call 节点的 argument_list 中提取字符串字面量参数。

        Args:
            call_node: call 类型节点。
            source: 源码字节流。

        Returns:
            字符串字面量参数列表。
        """
        args: list[str] = []
        # 找到 argument_list 子节点
        arg_list_node = None
        for child in call_node.children:
            if child.type == "argument_list":
                arg_list_node = child
                break

        if arg_list_node is None:
            return args

        for child in arg_list_node.children:
            if child.type == "string":
                # 字符串字面量: "/path"
                text = child.text.decode()
                # 去掉引号
                text = text.strip()
                if len(text) >= 2 and (
                    (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'"))
                ):
                    text = text[1:-1]
                # 去三重引号
                if len(text) >= 6 and (
                    (text.startswith('"""') and text.endswith('"""'))
                    or (text.startswith("'''") and text.endswith("'''"))
                ):
                    text = text[3:-3]
                args.append(text)
            # 也支持其他类型，如 identifier/string/... 暂时只提取 string

        return args

    def _extract_docstring(self, node, source: bytes) -> str | None:
        """从函数或类节点提取 docstring。

        Args:
            node: function_definition 或 class_definition 节点。
            source: 源码字节流。

        Returns:
            提取的 docstring（去掉引号，截断到 500 字符），如果没有则返回 None。
        """
        # 找到 block 节点（tree-sitter Python 的 block 无 field name，需遍历 children）
        block_node = None
        for child in node.children:
            if child.type == "block":
                block_node = child
                break
        if block_node is None:
            return None

        # 在 block 内找第一个 expression_statement -> string
        for child in block_node.children:
            if child.type == "expression_statement":
                for grandchild in child.children:
                    if grandchild.type == "string":
                        # 获取字符串内容
                        doc_text = grandchild.text.decode("utf-8", errors="replace")
                        # 去掉引号（可能是 """...""" 或 '''...''' 或 "..." 或 '...'）
                        doc_text = doc_text.strip()
                        if (doc_text.startswith('"""') and doc_text.endswith('"""')) or (
                            doc_text.startswith("'''") and doc_text.endswith("'''")
                        ):
                            doc_text = doc_text[3:-3]
                        elif (doc_text.startswith('"') and doc_text.endswith('"')) or (
                            doc_text.startswith("'") and doc_text.endswith("'")
                        ):
                            doc_text = doc_text[1:-1]
                        # 去掉首尾空白
                        doc_text = doc_text.strip()
                        # 截断到 500 字符
                        if len(doc_text) > 500:
                            doc_text = doc_text[:500]
                        return doc_text if doc_text else None
        return None

    def _extract_parameters(self, node) -> str | None:
        """从函数节点提取参数列表。

        Args:
            node: function_definition 节点。

        Returns:
            JSON 字符串格式的参数列表，如 '["self", "x: int"]'，如果没有参数则返回 None。
        """
        params_node = node.child_by_field_name("parameters")
        if params_node is None:
            return None

        params: list[str] = []
        for child in params_node.children:
            if child.type == "identifier":
                # 简单参数名: self, x
                params.append(child.text.decode())
            elif child.type == "typed_parameter":
                # 带类型参数: x: int
                params.append(child.text.decode())
            elif child.type == "typed_default_parameter":
                # 带类型和默认值的参数: x: int = 1
                params.append(child.text.decode())
            elif child.type == "default_parameter":
                # 带默认值参数（无类型）: x = 1
                params.append(child.text.decode())
            elif child.type == "dictionary_pattern":
                # **kwargs
                params.append(child.text.decode())
            elif child.type == "tuple_pattern":
                # *args
                params.append(child.text.decode())
            elif child.type == "typed_dictionary_pattern":
                # **kwargs: dict
                params.append(child.text.decode())
            elif child.type == "typed_tuple_pattern":
                # *args: tuple
                params.append(child.text.decode())

        if not params:
            return None
        return json.dumps(params, ensure_ascii=False)
