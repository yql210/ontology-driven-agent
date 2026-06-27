from __future__ import annotations

import json

from ontoagent.domain.schema import CodeEntity
from ontoagent.parsing.parser.base import ExtractedRelation
from ontoagent.pipeline.topic_linker import build_topic_relations


def test_build_topic_relations_from_extracted() -> None:
    """extracted_relations 中 publishes_to → 创建 ConceptEntity 和 Relation。"""
    extracted_relations = [
        ExtractedRelation(
            source_name="publish_order",
            source_type="function",
            target_name="order.created",
            target_type="topic",
            relation_type="publishes_to",
            file_path="producer.py",
        ),
    ]
    code_entities: list[CodeEntity] = []

    topics, relations = build_topic_relations(code_entities, extracted_relations)

    assert len(topics) == 1
    assert topics[0].name == "order.created"
    assert topics[0].entity_type == "message_topic"
    assert topics[0].description == "消息主题: order.created"

    assert len(relations) == 1
    assert relations[0].relation_type == "publishes_to"
    assert relations[0].source_id == "publish_order"


def test_build_topic_relations_from_mq_consumer() -> None:
    """code_entities 中 mq_consumer 提取 topic → 创建 ConceptEntity 和 consumed_by Relation。"""
    code_entities = [
        CodeEntity(
            name="handle_order_created",
            entity_type="function",
            entry_category="mq_consumer",
            entry_metadata=json.dumps({"topic": "order.created", "queue": "order-queue"}),
        ),
    ]
    extracted_relations: list[ExtractedRelation] = []

    topics, relations = build_topic_relations(code_entities, extracted_relations)

    assert len(topics) == 1
    assert topics[0].name == "order.created"
    assert topics[0].entity_type == "message_topic"

    assert len(relations) == 1
    assert relations[0].relation_type == "consumed_by"
    assert relations[0].source_id == code_entities[0].id
