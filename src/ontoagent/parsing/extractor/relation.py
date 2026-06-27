from __future__ import annotations

from ontoagent.domain.schema import CodeEntity, Relation
from ontoagent.parsing.parser.base import ExtractedRelation


def _build_file_index(entities: list[CodeEntity]) -> dict[str, dict[str, str]]:
    """构建文件路径内的实体索引，用于同文件优先匹配。

    Args:
        entities: 实体列表。

    Returns:
        {file_path: {entity_name: entity_id}} 的嵌套字典。
    """
    index: dict[str, dict[str, str]] = {}
    for e in entities:
        if e.file_path:
            file_map = index.setdefault(e.file_path, {})
            file_map[e.name] = e.id
    return index


class RelationExtractor:
    """关系提取器，聚合多个文件的解析结果并转换为 ID 级关系。

    职责：
    1. 聚合多个文件的 ExtractedRelation
    2. 将名称级关系转换为 ID 级关系（schema.Relation）
    3. 过滤无效关系（源/目标实体不在已知集合中）
    """

    def __init__(self) -> None:
        """初始化提取器。"""
        self._relations: list[ExtractedRelation] = []

    def add_parse_result(self, entities: list[CodeEntity], relations: list[ExtractedRelation]) -> None:
        """添加一个文件的解析结果。

        Args:
            entities: 该文件提取到的实体列表。
            relations: 该文件提取到的关系列表。
        """
        self._relations.extend(relations)

    def resolve(self, all_entities: list[CodeEntity]) -> list[Relation]:
        """将名称级关系转换为 ID 级关系。

        过滤掉源或目标实体不在 all_entities 中的无效关系。
        同名实体优先匹配同文件（针对 imports/contains/calls 关系）。

        Args:
            all_entities: 所有已知实体列表。

        Returns:
            解析后的 Relation 列表。
        """
        name_to_ids = self._build_name_map(all_entities)
        file_index = _build_file_index(all_entities)
        resolved: list[Relation] = []

        for rel in self._relations:
            source_id, target_id = self._resolve_relation(rel, name_to_ids, file_index)
            if source_id and target_id:
                resolved.append(
                    Relation(
                        source_id=source_id,
                        target_id=target_id,
                        relation_type=rel.relation_type,
                    )
                )

        return resolved

    def resolve_with_unresolved(self, all_entities: list[CodeEntity]) -> tuple[list[Relation], list[ExtractedRelation]]:
        """将名称级关系转换为 ID 级关系，同时返回未解析的外部导入。

        Args:
            all_entities: 所有已知实体列表。

        Returns:
            (已解析的 Relation 列表, 未解析的 ExtractedRelation 列表) 元组。
        """
        name_to_ids = self._build_name_map(all_entities)
        file_index = _build_file_index(all_entities)
        resolved: list[Relation] = []
        unresolved: list[ExtractedRelation] = []

        for rel in self._relations:
            source_id, target_id = self._resolve_relation(rel, name_to_ids, file_index)
            if source_id and target_id:
                resolved.append(
                    Relation(
                        source_id=source_id,
                        target_id=target_id,
                        relation_type=rel.relation_type,
                    )
                )
            elif rel.relation_type == "imports":
                # 外部 import 归入 unresolved
                unresolved.append(rel)

        return resolved, unresolved

    def _resolve_relation(
        self,
        rel: ExtractedRelation,
        name_to_ids: dict[str, list[str]],
        file_index: dict[str, dict[str, str]],
    ) -> tuple[str | None, str | None]:
        """解析单个关系的源和目标 ID。

        策略：
        1. imports/contains/calls: 优先匹配同文件实体
        2. 其他关系: 使用全局名称映射

        Args:
            rel: 待解析的关系。
            name_to_ids: 全局名称到 ID 列表的映射。
            file_index: 文件路径内的实体索引。

        Returns:
            (source_id, target_id) 元组，任一为 None 表示解析失败。
        """
        # 优先使用同文件匹配（适用于 imports/contains/calls）
        if rel.relation_type in ("imports", "contains", "calls") and rel.file_path:
            file_map = file_index.get(rel.file_path, {})
            # 源实体同文件匹配
            if rel.source_name in file_map:
                source_id = file_map[rel.source_name]
            else:
                source_ids = name_to_ids.get(rel.source_name, [])
                source_id = source_ids[0] if source_ids else None
            # 目标实体同文件匹配
            if rel.target_name in file_map:
                target_id = file_map[rel.target_name]
            else:
                target_ids = name_to_ids.get(rel.target_name, [])
                target_id = target_ids[0] if target_ids else None
            return source_id, target_id

        # 其他关系：全局匹配
        source_ids = name_to_ids.get(rel.source_name, [])
        target_ids = name_to_ids.get(rel.target_name, [])
        return (source_ids[0] if source_ids else None, target_ids[0] if target_ids else None)

    @staticmethod
    def _build_name_map(entities: list[CodeEntity]) -> dict[str, list[str]]:
        """构建实体名称到 ID 的多值映射。

        Args:
            entities: 实体列表。

        Returns:
            名称到 ID 列表的字典（同名实体映射到多个 ID）。
        """
        mapping: dict[str, list[str]] = {}
        for e in entities:
            mapping.setdefault(e.name, []).append(e.id)
        return mapping
