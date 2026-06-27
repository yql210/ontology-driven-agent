from __future__ import annotations

import json

from ontoagent.domain.schema import CodeEntity, ConceptEntity, Relation
from ontoagent.parsing.parser.base import ExtractedRelation


def build_topic_relations(
    code_entities: list[CodeEntity],
    extracted_relations: list[ExtractedRelation],
) -> tuple[list[ConceptEntity], list[Relation]]:
    """从 extracted_relations 和 code_entities 构建消息主题 ConceptEntity 和关系。

    - extracted_relations 中 publishes_to → 创建 ConceptEntity(type=message_topic)
    - code_entities 中 entry_category=mq_consumer → 从 entry_metadata 提取 topic → consumed_by
    - 按 (name, type) 去重 ConceptEntity
    """
    topics: dict[tuple[str, str], ConceptEntity] = {}
    relations: list[Relation] = []

    # 从 extracted_relations 提取 publishes_to
    for er in extracted_relations:
        if er.relation_type == "publishes_to":
            t_name = er.target_name
            key = (t_name, "message_topic")
            if key not in topics:
                topics[key] = ConceptEntity(
                    name=t_name,
                    entity_type="message_topic",
                    description=f"消息主题: {t_name}",
                )
            # 生成 publishes_to Relation
            topic = topics[key]
            relations.append(
                Relation(
                    source_id=er.source_name,
                    target_id=topic.id,
                    relation_type="publishes_to",
                )
            )

    # 从 code_entities 提取 mq_consumer → consumed_by
    for ce in code_entities:
        if ce.entry_category == "mq_consumer" and ce.entry_metadata:
            try:
                meta = json.loads(ce.entry_metadata)
                t_name = meta.get("topic")
                if t_name:
                    key = (t_name, "message_topic")
                    if key not in topics:
                        topics[key] = ConceptEntity(
                            name=t_name,
                            entity_type="message_topic",
                            description=f"消息主题: {t_name}",
                        )
                    topic = topics[key]
                    relations.append(
                        Relation(
                            source_id=ce.id,
                            target_id=topic.id,
                            relation_type="consumed_by",
                        )
                    )
            except json.JSONDecodeError:
                pass

    return list(topics.values()), relations
