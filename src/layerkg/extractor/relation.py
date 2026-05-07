from __future__ import annotations

from layerkg.parser.base import ExtractedRelation
from layerkg.schema import CodeEntity, Relation


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

        Args:
            all_entities: 所有已知实体列表。

        Returns:
            解析后的 Relation 列表。
        """
        name_to_id = self._build_name_map(all_entities)
        resolved: list[Relation] = []

        for rel in self._relations:
            source_id = name_to_id.get(rel.source_name)
            target_id = name_to_id.get(rel.target_name)
            if source_id and target_id:
                resolved.append(
                    Relation(
                        source_id=source_id,
                        target_id=target_id,
                        relation_type=rel.relation_type,
                    )
                )

        return resolved

    @staticmethod
    def _build_name_map(entities: list[CodeEntity]) -> dict[str, str]:
        """构建实体名称到 ID 的映射。

        Args:
            entities: 实体列表。

        Returns:
            名称到 ID 的字典。
        """
        return {e.name: e.id for e in entities}
