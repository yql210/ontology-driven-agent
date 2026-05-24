from __future__ import annotations

import json
import logging
from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from layerkg.parser.base import BaseParser, ExtractedRelation, ParseResult
from layerkg.schema import CodeEntity

JAVA_LANG = Language(tsjava.language())
_logger = logging.getLogger(__name__)

# JDK 常用类型和方法，用于过滤 calls/imports 关系
_JDK_COMMON_TYPES = {
    "String",
    "Integer",
    "Long",
    "Double",
    "Float",
    "Boolean",
    "Byte",
    "Short",
    "Character",
    "Object",
    "Class",
    "List",
    "Map",
    "Set",
    "ArrayList",
    "HashMap",
    "HashSet",
    "System",
    "Math",
    "Arrays",
    "Collections",
    "Exception",
    "RuntimeException",
    "Thread",
    "Override",
    "Deprecated",
    "SuppressWarnings",
    "println",
    "print",
    "format",
    "valueOf",
    "toString",
    "equals",
    "hashCode",
    "compareTo",
    "getClass",
    "intValue",
    "longValue",
    "doubleValue",
    "floatValue",
    "booleanValue",
    "byteValue",
    "shortValue",
    "charValue",
    "size",
    "isEmpty",
    "get",
    "set",
    "add",
    "remove",
    "clear",
    "sort",
    "close",
    "put",
    "keySet",
    "values",
    "entrySet",
    "iterator",
    "next",
    "hasNext",
    "stream",
    "map",
    "filter",
    "collect",
    "of",
    "builder",
    "build",
    "orElse",
    "isPresent",
    "getName",
    "setName",
    "getValue",
    "setValue",
}


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

        except Exception as e:
            # 语法错误时返回已有实体（至少有 file）
            _logger.warning("Parse failed for %s: %s", file_path, e)

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

        # annotation_type_declaration (@interface)
        elif node_type == "annotation_type_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                anno_name = name_node.text.decode("utf-8", errors="replace")
                entities.append(CodeEntity(
                    name=anno_name,
                    entity_type="interface",
                    file_path=file_path,
                    start_line=node.start_point[0],
                    end_line=node.end_point[0],
                    language="java",
                    source=node.text.decode("utf-8", errors="replace")[:500],
                ))
                source_entity = parent_class_name if parent_class_name else package_name
                source_type = "class" if parent_class_name else "module"
                if source_entity:
                    relations.append(ExtractedRelation(
                        source_name=source_entity,
                        source_type=source_type,
                        target_name=anno_name,
                        target_type="interface",
                        relation_type="contains",
                        file_path=file_path,
                    ))

        # import_declaration
        elif node_type == "import_declaration":
            self._extract_import(node, source, file_path, relations, package_name)

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

        # 提取 extends 关系
        for child in node.children:
            if child.type == "superclass":
                type_id = child.child_by_field_name("name")
                if type_id is None:
                    # 查找 type_identifier（直接或泛型）
                    for sub in child.children:
                        if sub.type == "type_identifier":
                            type_id = sub
                            break
                        elif sub.type == "generic_type":
                            # 泛型情况：extends Base<T>
                            for gen_child in sub.children:
                                if gen_child.type == "type_identifier":
                                    type_id = gen_child
                                    break
                            break
                if type_id:
                    parent_name = type_id.text.decode("utf-8", errors="replace")
                    if parent_name not in _JDK_COMMON_TYPES:
                        relations.append(
                            ExtractedRelation(
                                source_name=class_name,
                                source_type="class",
                                target_name=parent_name,
                                target_type="class",
                                relation_type="extends",
                                file_path=file_path,
                            )
                        )
                break

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
                            if iface_name not in _JDK_COMMON_TYPES:
                                relations.append(
                                    ExtractedRelation(
                                        source_name=class_name,
                                        source_type="class",
                                        target_name=iface_name,
                                        target_type="interface",
                                        relation_type="implements",
                                        file_path=file_path,
                                    )
                                )
                break

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

        # 提取 extends 关系（interface 可以 extends 多个 interface）
        for child in node.children:
            if child.type == "extends_interfaces":
                type_list = None
                for sub in child.children:
                    if sub.type == "type_list":
                        type_list = sub
                        break
                if type_list:
                    for t in type_list.children:
                        if t.type == "type_identifier":
                            parent_name = t.text.decode("utf-8", errors="replace")
                            if parent_name not in _JDK_COMMON_TYPES:
                                relations.append(
                                    ExtractedRelation(
                                        source_name=interface_name,
                                        source_type="interface",
                                        target_name=parent_name,
                                        target_type="interface",
                                        relation_type="extends",
                                        file_path=file_path,
                                    )
                                )
                break

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
                            if iface_name not in _JDK_COMMON_TYPES:
                                relations.append(
                                    ExtractedRelation(
                                        source_name=enum_name,
                                        source_type="enum",
                                        target_name=iface_name,
                                        target_type="interface",
                                        relation_type="implements",
                                        file_path=file_path,
                                    )
                                )
                break

        # 提取 enum 常量（enum_constant 是 enum_body 的直接子节点）
        for child in node.children:
            if child.type == "enum_body":
                for body_child in child.children:
                    if body_child.type == "enum_constant":
                        # enum_constant 的 name 是 identifier 子节点
                        name_node = None
                        for sub in body_child.children:
                            if sub.type == "identifier":
                                name_node = sub
                                break
                        if name_node:
                            const_name = name_node.text.decode("utf-8", errors="replace")
                            entities.append(CodeEntity(
                                name=const_name,
                                entity_type="field",
                                file_path=file_path,
                                start_line=body_child.start_point[0],
                                end_line=body_child.end_point[0],
                                language="java",
                                source=body_child.text.decode("utf-8", errors="replace")[:500],
                            ))
                            relations.append(ExtractedRelation(
                                source_name=enum_name,
                                source_type="enum",
                                target_name=const_name,
                                target_type="field",
                                relation_type="contains",
                                file_path=file_path,
                            ))
                break

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
                            if iface_name not in _JDK_COMMON_TYPES:
                                relations.append(
                                    ExtractedRelation(
                                        source_name=record_name,
                                        source_type="record",
                                        target_name=iface_name,
                                        target_type="interface",
                                        relation_type="implements",
                                        file_path=file_path,
                                    )
                                )
                break

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
        param_types = self._extract_parameter_types(node)
        if param_types:
            full_name = f"{class_name}.{method_name}({','.join(param_types)})"
        else:
            full_name = f"{class_name}.{method_name}()"

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

        # 提取方法体内的 calls 关系
        self._extract_calls(node, source, file_path, relations, full_name)

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
        param_types = self._extract_parameter_types(node)
        full_name = f"{class_name}.<init>({','.join(param_types)})" if param_types else f"{class_name}.<init>()"

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

        # 提取构造器内的 calls 关系
        self._extract_calls(node, source, file_path, relations, full_name)

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

    def _extract_parameter_types(self, node) -> list[str]:
        """从方法/构造器声明中提取参数类型名列表。"""
        types: list[str] = []
        for child in node.children:
            if child.type == "formal_parameters":
                for param in child.children:
                    if param.type in ("formal_parameter", "spread_parameter", "variable_arbitrary_parameter"):
                        for p_child in param.children:
                            if p_child.type in (
                                "type_identifier",
                                "generic_type",
                                "array_type",
                                "scoped_type_identifier",
                                "integral_type",
                                "floating_point_type",
                                "boolean_type",
                                "void_type",
                            ):
                                type_name = p_child.text.decode("utf-8", errors="replace")
                                types.append(type_name)
                                break
                break
        return types

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

    def _extract_import(
        self,
        node,
        source: bytes,
        file_path: str,
        relations: list[ExtractedRelation],
        package_name: str | None,
    ) -> None:
        """提取 import 关系。"""
        # 找 scoped_identifier 或 identifier
        import_name = None
        is_wildcard = False

        # 检查是否有 asterisk (wildcard import)
        for child in node.children:
            if child.type == "asterisk":
                is_wildcard = True

        # 提取导入名称
        for child in node.children:
            if child.type in ("scoped_identifier", "identifier"):
                import_name = child.text.decode("utf-8", errors="replace")
                break

        if not import_name:
            return

        # 对于 wildcard import (java.io.*)，跳过
        if is_wildcard:
            return

        # 取最后一段作为 target_name (java.util.List -> List)
        target_name = import_name.split(".")[-1] if "." in import_name else import_name

        # 过滤 JDK 常用类型
        if target_name in _JDK_COMMON_TYPES:
            return

        # source_name 用 package_name 或 file_name
        if package_name:
            source_name = package_name
            source_type = "module"
        else:
            source_name = Path(file_path).name
            source_type = "file"

        relations.append(
            ExtractedRelation(
                source_name=source_name,
                source_type=source_type,
                target_name=target_name,
                target_type="class",
                relation_type="imports",
                file_path=file_path,
            )
        )

    def _extract_callee_name(self, node) -> str | None:
        """从 method_invocation 提取被调用方法名。"""
        # method_invocation 的子节点中，最后一个 identifier 是方法名
        # 例如: items.size → "size", helper → "helper", this.doSomething → "doSomething"
        identifiers = [c for c in node.children if c.type == "identifier"]
        if identifiers:
            return identifiers[-1].text.decode("utf-8", errors="replace")
        return None

    def _extract_calls(
        self,
        method_node,
        source: bytes,
        file_path: str,
        relations: list[ExtractedRelation],
        caller_name: str,
    ) -> None:
        """BFS 遍历方法体，提取 method_invocation 和 object_creation_expression。"""
        queue = list(method_node.children)
        while queue:
            node = queue.pop(0)
            if node.type == "method_invocation":
                callee = self._extract_callee_name(node)
                if callee and len(callee) >= 2 and callee not in _JDK_COMMON_TYPES:
                    relations.append(
                        ExtractedRelation(
                            source_name=caller_name,
                            source_type="function",
                            target_name=callee,
                            target_type="function",
                            relation_type="calls",
                            file_path=file_path,
                        )
                    )
            elif node.type == "object_creation_expression":
                # new Bar() → calls Bar
                for child in node.children:
                    if child.type == "type_identifier":
                        callee = child.text.decode("utf-8", errors="replace")
                        if len(callee) >= 2 and callee not in _JDK_COMMON_TYPES:
                            relations.append(
                                ExtractedRelation(
                                    source_name=caller_name,
                                    source_type="function",
                                    target_name=callee,
                                    target_type="function",
                                    relation_type="calls",
                                    file_path=file_path,
                                )
                            )
                        break
            queue.extend(node.children)
