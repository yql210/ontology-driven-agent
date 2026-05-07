from __future__ import annotations

from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from layerkg.parser.base import BaseParser, ExtractedRelation, ParseResult
from layerkg.schema import CodeEntity

PY_LANG = Language(tspython.language())


class PythonParser(BaseParser):
    """Python 源码解析器，使用 tree-sitter 提取实体和关系。"""

    def __init__(self) -> None:
        """初始化解析器。"""
        self._parser = Parser(PY_LANG)

    @property
    def language(self) -> str:
        """返回解析器语言名称。"""
        return "python"

    def parse_file(self, file_path: Path) -> ParseResult:
        """解析单个文件。

        Args:
            file_path: 源文件路径。

        Returns:
            ParseResult 包含提取的实体和关系。
        """
        if not file_path.exists():
            return ParseResult(file_path=str(file_path), error=f"File not found: {file_path}")

        try:
            source_bytes = file_path.read_bytes()
            return self.parse_source(source_bytes, str(file_path))
        except OSError as e:
            return ParseResult(file_path=str(file_path), error=f"Failed to read file: {e}")

    def parse_source(self, source: bytes, file_path: str = "<string>") -> ParseResult:
        """解析源码字节流。

        Args:
            source: 源码字节流。
            file_path: 虚拟文件路径。

        Returns:
            ParseResult 包含提取的实体和关系。
        """
        entities: list[CodeEntity] = []
        relations: list[ExtractedRelation] = []

        # 创建 module 实体
        module_name = Path(file_path).stem
        # 计算 end_line（0-indexed）：最后一行行号
        # 换行符数 = n
        # - 如果以 \n 结尾：有 n 行，end_line = n - 1
        # - 如果不以 \n 结尾：有 n + 1 行，end_line = n
        if source:
            newline_count = source.count(b"\n")
            end_line = newline_count - 1 if source.endswith(b"\n") else newline_count
        else:
            end_line = 0

        module_entity = CodeEntity(
            name=module_name,
            entity_type="module",
            file_path=file_path,
            start_line=0,
            end_line=end_line,
            language="python",
        )
        entities.append(module_entity)

        try:
            tree = self._parser.parse(source)
            root_node = tree.root_node

            # 递归遍历 AST
            self._walk(
                root_node,
                source,
                file_path,
                entities,
                relations,
                module_name,
                parent_class_name=None,
            )

        except Exception:
            # 语法错误时返回已有实体（至少有 module）
            pass

        return ParseResult(
            file_path=file_path,
            entities=entities,
            relations=relations,
            language=self.language,
        )

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

        # 函数定义
        if node_type == "function_definition":
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

        entity = CodeEntity(
            name=full_name,
            entity_type="function",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="python",
        )
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

    def _extract_class(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        module_name: str,
        parent_class_name: str | None,
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

        entity = CodeEntity(
            name=class_name,
            entity_type="class",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="python",
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

                # 只取顶层模块名（如 os.path -> os）
                top_module = import_name.split(".")[0]

                relation = ExtractedRelation(
                    source_name=module_name,
                    source_type="module",
                    target_name=top_module,
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
        # 只取顶层模块名
        top_module = import_module.split(".")[0]

        relation = ExtractedRelation(
            source_name=module_name,
            source_type="module",
            target_name=top_module,
            target_type="module",
            relation_type="imports",
            file_path=file_path,
        )
        relations.append(relation)
