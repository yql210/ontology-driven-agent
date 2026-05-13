from __future__ import annotations

import json
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
        if not file_path.exists():
            return ParseResult(file_path=str(file_path), error=f"File not found: {file_path}")

        try:
            source_bytes = file_path.read_bytes()
            return self.parse_source(source_bytes, str(file_path))
        except OSError as e:
            return ParseResult(file_path=str(file_path), error=f"Failed to read file: {e}")

    def parse_source(self, source: bytes, file_path: str = "<string>") -> ParseResult:
        entities: list[CodeEntity] = []
        relations: list[ExtractedRelation] = []

        # 计算文件行数
        if source:
            newline_count = source.count(b"\n")
            end_line = newline_count - 1 if source.endswith(b"\n") else newline_count
        else:
            end_line = 0

        source_text = source.decode("utf-8", errors="replace")

        # 创建 file 实体（Java 用 file 而非 module）
        file_name = Path(file_path).name
        file_entity = CodeEntity(
            name=file_name,
            entity_type="file",
            file_path=file_path,
            start_line=0,
            end_line=end_line,
            language="java",
            source=source_text[:500] if source_text else None,
        )
        entities.append(file_entity)

        try:
            tree = self._parser.parse(source)
            root_node = tree.root_node

            # 预扫描 package 声明
            package_name = self._extract_package_first_pass(root_node, source, file_path, entities, relations)

            # 递归遍历 AST，提取实体
            self._walk(
                root_node,
                source,
                file_path,
                entities,
                relations,
                package_name,
                parent_class_name=None,
            )

        except Exception:
            # 语法错误时返回已有实体（至少有 file）
            pass

        return ParseResult(
            file_path=file_path,
            entities=entities,
            relations=relations,
            language=self.language,
        )

    def _extract_package_first_pass(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
    ) -> str | None:
        """预扫描提取 package 声明。"""
        if node is None:
            return None

        if node.type == "package_declaration":
            return self._extract_package(node, source, file_path, entities, relations)

        for child in node.children:
            result = self._extract_package_first_pass(child, source, file_path, entities, relations)
            if result:
                return result

        return None

    def _walk(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        package_name: str | None,
        parent_class_name: str | None = None,
    ) -> None:
        if node is None:
            return

        node_type = node.type

        # package_declaration - 已经在预扫描中处理，跳过
        if node_type == "package_declaration":
            return

        # class_declaration
        if node_type == "class_declaration":
            class_name = self._extract_class(
                node, source, file_path, entities, relations, package_name, parent_class_name
            )
            # 递归遍历 class body
            for child in node.children:
                self._walk(child, source, file_path, entities, relations, package_name, parent_class_name=class_name)

        # interface_declaration
        elif node_type == "interface_declaration":
            interface_name = self._extract_interface(
                node, source, file_path, entities, relations, package_name, parent_class_name
            )
            # 递归遍历 interface body
            for child in node.children:
                self._walk(
                    child, source, file_path, entities, relations, package_name, parent_class_name=interface_name
                )

        # enum_declaration
        elif node_type == "enum_declaration":
            enum_name = self._extract_enum(
                node, source, file_path, entities, relations, package_name, parent_class_name
            )
            # 递归遍历 enum body
            for child in node.children:
                self._walk(child, source, file_path, entities, relations, package_name, parent_class_name=enum_name)

        # record_declaration
        elif node_type == "record_declaration":
            record_name = self._extract_record(
                node, source, file_path, entities, relations, package_name, parent_class_name
            )
            # 递归遍历 record body
            for child in node.children:
                self._walk(child, source, file_path, entities, relations, package_name, parent_class_name=record_name)

        # import_declaration - Day 2 跳过
        elif node_type == "import_declaration":
            pass  # 不处理

        else:
            # 递归子节点
            for child in node.children:
                self._walk(child, source, file_path, entities, relations, package_name, parent_class_name)

    def _extract_package(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
    ) -> str | None:
        """提取 package 声明作为 module 实体。"""
        # 找到 package 名称
        for child in node.children:
            if child.type == "scoped_identifier" or child.type == "identifier":
                package_name = child.text.decode("utf-8", errors="replace")
                start_line = node.start_point[0]
                end_line = node.end_point[0]

                entity = CodeEntity(
                    name=package_name,
                    entity_type="module",
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    language="java",
                    source=node.text.decode("utf-8", errors="replace"),
                )
                entities.append(entity)
                return package_name
        return None

    def _extract_class(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        package_name: str | None,
        parent_class_name: str | None,
    ) -> str | None:
        """提取 class 实体。"""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        class_name = name_node.text.decode("utf-8", errors="replace")
        start_line = node.start_point[0]
        end_line = node.end_point[0]

        source_text = node.text.decode("utf-8", errors="replace")
        docstring = self._get_javadoc(node, source)

        entity = CodeEntity(
            name=class_name,
            entity_type="class",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="java",
            source=source_text[:500],
            docstring=docstring,
        )
        entities.append(entity)

        # 创建 contains 关系
        source_entity = parent_class_name if parent_class_name else package_name
        source_type = "class" if parent_class_name else "module"

        if source_entity:
            relation = ExtractedRelation(
                source_name=source_entity,
                source_type=source_type,
                target_name=class_name,
                target_type="class",
                relation_type="contains",
                file_path=file_path,
            )
            relations.append(relation)

        # 遍历 class body 提取 method/constructor/field
        self._extract_class_body_members(node, source, file_path, entities, relations, class_name)

        return class_name

    def _extract_interface(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        package_name: str | None,
        parent_class_name: str | None,
    ) -> str | None:
        """提取 interface 实体。"""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        interface_name = name_node.text.decode("utf-8", errors="replace")
        start_line = node.start_point[0]
        end_line = node.end_point[0]

        source_text = node.text.decode("utf-8", errors="replace")
        docstring = self._get_javadoc(node, source)

        entity = CodeEntity(
            name=interface_name,
            entity_type="interface",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="java",
            source=source_text[:500],
            docstring=docstring,
        )
        entities.append(entity)

        # 创建 contains 关系
        source_entity = parent_class_name if parent_class_name else package_name
        source_type = "class" if parent_class_name else "module"

        if source_entity:
            relation = ExtractedRelation(
                source_name=source_entity,
                source_type=source_type,
                target_name=interface_name,
                target_type="interface",
                relation_type="contains",
                file_path=file_path,
            )
            relations.append(relation)

        # 遍历 interface body 提取 method
        self._extract_class_body_members(node, source, file_path, entities, relations, interface_name)

        return interface_name

    def _extract_enum(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        package_name: str | None,
        parent_class_name: str | None,
    ) -> str | None:
        """提取 enum 实体。"""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        enum_name = name_node.text.decode("utf-8", errors="replace")
        start_line = node.start_point[0]
        end_line = node.end_point[0]

        source_text = node.text.decode("utf-8", errors="replace")
        docstring = self._get_javadoc(node, source)

        entity = CodeEntity(
            name=enum_name,
            entity_type="enum",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="java",
            source=source_text[:500],
            docstring=docstring,
        )
        entities.append(entity)

        # 创建 contains 关系
        source_entity = parent_class_name if parent_class_name else package_name
        source_type = "class" if parent_class_name else "module"

        if source_entity:
            relation = ExtractedRelation(
                source_name=source_entity,
                source_type=source_type,
                target_name=enum_name,
                target_type="enum",
                relation_type="contains",
                file_path=file_path,
            )
            relations.append(relation)

        # 遍历 enum body 提取 method/constructor/field
        self._extract_class_body_members(node, source, file_path, entities, relations, enum_name)

        return enum_name

    def _extract_record(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        package_name: str | None,
        parent_class_name: str | None,
    ) -> str | None:
        """提取 record 实体。"""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None

        record_name = name_node.text.decode("utf-8", errors="replace")
        start_line = node.start_point[0]
        end_line = node.end_point[0]

        source_text = node.text.decode("utf-8", errors="replace")
        docstring = self._get_javadoc(node, source)

        entity = CodeEntity(
            name=record_name,
            entity_type="record",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="java",
            source=source_text[:500],
            docstring=docstring,
        )
        entities.append(entity)

        # 创建 contains 关系
        source_entity = parent_class_name if parent_class_name else package_name
        source_type = "class" if parent_class_name else "module"

        if source_entity:
            relation = ExtractedRelation(
                source_name=source_entity,
                source_type=source_type,
                target_name=record_name,
                target_type="record",
                relation_type="contains",
                file_path=file_path,
            )
            relations.append(relation)

        # 遍历 record body 提取 method/constructor
        self._extract_class_body_members(node, source, file_path, entities, relations, record_name)

        return record_name

    def _extract_class_body_members(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        class_name: str,
    ) -> None:
        """从 class/interface/enum/record body 提取成员。"""
        # 找到 body 节点
        body_node = None
        for child in node.children:
            if (
                child.type == "class_body"
                or child.type == "interface_body"
                or child.type == "enum_body"
                or child.type == "record_body"
            ):
                body_node = child
                break

        if body_node is None:
            return

        # 遍历 body 内容
        for child in body_node.children:
            child_type = child.type

            if child_type == "method_declaration":
                self._extract_method(child, source, file_path, entities, relations, class_name)
            elif child_type == "constructor_declaration":
                self._extract_constructor(child, source, file_path, entities, relations, class_name)
            elif child_type == "field_declaration":
                self._extract_field(child, source, file_path, entities, relations, class_name)
            elif child_type == "class_declaration":
                # 内部类
                self._extract_class(child, source, file_path, entities, relations, None, parent_class_name=class_name)
            elif child_type == "interface_declaration":
                # 内部接口
                self._extract_interface(
                    child, source, file_path, entities, relations, None, parent_class_name=class_name
                )
            elif child_type == "enum_declaration":
                # 内部 enum
                self._extract_enum(child, source, file_path, entities, relations, None, parent_class_name=class_name)
            elif child_type == "record_declaration":
                # 内部 record
                self._extract_record(child, source, file_path, entities, relations, None, parent_class_name=class_name)
            elif child_type == "enum_body_declarations":
                # enum 中的额外声明（方法、构造器等）
                self._extract_enum_body_declarations(child, source, file_path, entities, relations, class_name)

    def _extract_method(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        class_name: str,
    ) -> None:
        """提取 method 实体。"""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return

        method_name = name_node.text.decode("utf-8", errors="replace")
        full_name = f"{class_name}.{method_name}"

        start_line = node.start_point[0]
        end_line = node.end_point[0]

        source_text = node.text.decode("utf-8", errors="replace")
        docstring = self._get_javadoc(node, source)
        parameters = self._extract_parameters(node)

        entity = CodeEntity(
            name=full_name,
            entity_type="function",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="java",
            source=source_text[:500],
            docstring=docstring,
            parameters=parameters,
        )
        entities.append(entity)

        # 创建 contains 关系
        relation = ExtractedRelation(
            source_name=class_name,
            source_type="class",
            target_name=full_name,
            target_type="function",
            relation_type="contains",
            file_path=file_path,
        )
        relations.append(relation)

    def _extract_constructor(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        class_name: str,
    ) -> None:
        """提取 constructor 实体。"""
        # constructor_declaration 没有 name 字段，用类名
        full_name = f"{class_name}.<init>"

        start_line = node.start_point[0]
        end_line = node.end_point[0]

        source_text = node.text.decode("utf-8", errors="replace")
        docstring = self._get_javadoc(node, source)
        parameters = self._extract_parameters(node)

        entity = CodeEntity(
            name=full_name,
            entity_type="function",
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            language="java",
            source=source_text[:500],
            docstring=docstring,
            parameters=parameters,
        )
        entities.append(entity)

        # 创建 contains 关系
        relation = ExtractedRelation(
            source_name=class_name,
            source_type="class",
            target_name=full_name,
            target_type="function",
            relation_type="contains",
            file_path=file_path,
        )
        relations.append(relation)

    def _extract_field(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        class_name: str,
    ) -> None:
        """提取 field 实体。"""
        # field_declaration 结构: modifiers type variable_declarator (, variable_declarator)* ;
        # 每个 variable_declarator 产生一个 field 实体
        start_line = node.start_point[0]
        end_line = node.end_point[0]

        # 遍历 variable_declarator
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue

                field_name = name_node.text.decode("utf-8", errors="replace")

                entity = CodeEntity(
                    name=field_name,
                    entity_type="field",
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    language="java",
                    source=child.text.decode("utf-8", errors="replace"),
                )
                entities.append(entity)

                # 创建 contains 关系
                relation = ExtractedRelation(
                    source_name=class_name,
                    source_type="class",
                    target_name=field_name,
                    target_type="field",
                    relation_type="contains",
                    file_path=file_path,
                )
                relations.append(relation)

    def _extract_enum_body_declarations(
        self,
        node,
        source: bytes,
        file_path: str,
        entities: list[CodeEntity],
        relations: list[ExtractedRelation],
        class_name: str,
    ) -> None:
        """从 enum_body_declarations 提取成员（方法、构造器等）。"""
        for child in node.children:
            child_type = child.type

            if child_type == "method_declaration":
                self._extract_method(child, source, file_path, entities, relations, class_name)
            elif child_type == "constructor_declaration":
                self._extract_constructor(child, source, file_path, entities, relations, class_name)
            elif child_type == "field_declaration":
                self._extract_field(child, source, file_path, entities, relations, class_name)

    def _extract_parameters(self, node) -> str | None:
        """从方法或构造器节点提取参数列表。"""
        params_node = node.child_by_field_name("parameters")
        if params_node is None:
            return None

        params: list[str] = []
        for child in params_node.children:
            if child.type == "formal_parameter":
                # 获取参数类型和名称
                param_text = child.text.decode("utf-8", errors="replace")
                params.append(param_text)
            elif child.type == "spread_parameter":
                # 可变参数 String... args
                param_text = child.text.decode("utf-8", errors="replace")
                params.append(param_text)
            elif child.type == "variable_arbitrary_parameter":
                # 另一种可变参数形式
                param_text = child.text.decode("utf-8", errors="replace")
                params.append(param_text)

        if not params:
            return None
        return json.dumps(params, ensure_ascii=False)

    def _get_javadoc(self, node, source: bytes) -> str | None:
        """检查节点前一个兄弟节点是否是 Javadoc 注释。"""
        # 获取节点在父节点中的索引
        parent = node.parent
        if parent is None:
            return None

        node_index = -1
        for i, child in enumerate(parent.children):
            if child == node:
                node_index = i
                break

        if node_index <= 0:
            return None

        # 检查前一个兄弟节点
        prev_sibling = parent.children[node_index - 1]

        # 检查是否是 block_comment 且以 /** 开头
        if prev_sibling.type == "block_comment":
            comment_text = prev_sibling.text.decode("utf-8", errors="replace")
            if comment_text.startswith("/**"):
                # 去掉 /** 和 */ 以及每行的 * 前缀
                content = comment_text[2:-2]  # 去掉 /** 和 */
                lines = content.split("\n")
                cleaned_lines = []
                for line in lines:
                    line = line.strip()
                    if line.startswith("*"):
                        line = line[1:].strip()
                    cleaned_lines.append(line)
                cleaned = "\n".join(cleaned_lines).strip()
                # 截断到 500 字符
                if len(cleaned) > 500:
                    cleaned = cleaned[:500]
                return cleaned if cleaned else None

        return None
