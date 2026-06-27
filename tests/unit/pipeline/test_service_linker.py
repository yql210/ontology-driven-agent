from __future__ import annotations

from ontoagent.parsing.parser.base import ExtractedRelation
from ontoagent.pipeline.service_linker import build_service_relations


def test_build_service_relations_dedup() -> None:
    """多个 calls_service 指向同一服务名 → 去重，只创建一个 ServiceEntity，但生成多个 Relation。"""
    extracted_relations = [
        ExtractedRelation(
            source_name="func_a",
            source_type="function",
            target_name="payment-service",
            target_type="service",
            relation_type="calls_service",
            file_path="a.py",
        ),
        ExtractedRelation(
            source_name="func_b",
            source_type="function",
            target_name="payment-service",
            target_type="service",
            relation_type="calls_service",
            file_path="b.py",
        ),
        ExtractedRelation(
            source_name="func_c",
            source_type="function",
            target_name="order-service",
            target_type="service",
            relation_type="calls_service",
            file_path="c.py",
        ),
    ]

    services, relations = build_service_relations(extracted_relations)

    # 两个唯一服务
    assert len(services) == 2
    service_names = {s.name for s in services}
    assert service_names == {"payment-service", "order-service"}
    for s in services:
        assert s.status == "running"
        assert s.version == "unknown"

    # 三个关系（每个 extracted_relation 一条）
    assert len(relations) == 3
    for r in relations:
        assert r.relation_type == "calls_service"


def test_build_service_relations_ignores_other_types() -> None:
    """非 calls_service 类型的关系被忽略。"""
    extracted_relations = [
        ExtractedRelation(
            source_name="func_a",
            source_type="function",
            target_name="func_b",
            target_type="function",
            relation_type="calls",
            file_path="a.py",
        ),
        ExtractedRelation(
            source_name="func_x",
            source_type="function",
            target_name="auth-service",
            target_type="service",
            relation_type="calls_service",
            file_path="x.py",
        ),
    ]

    services, relations = build_service_relations(extracted_relations)

    assert len(services) == 1
    assert services[0].name == "auth-service"
    assert len(relations) == 1
