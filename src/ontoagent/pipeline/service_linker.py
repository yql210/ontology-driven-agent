from __future__ import annotations

from ontoagent.domain.schema import Relation, ServiceEntity
from ontoagent.parsing.parser.base import ExtractedRelation


def build_service_relations(
    extracted_relations: list[ExtractedRelation],
) -> tuple[list[ServiceEntity], list[Relation]]:
    """聚合 calls_service 关系，按 target_name（服务名）去重，创建 ServiceEntity 和 calls_service Relation。

    每个唯一服务名创建一个 ServiceEntity（name=服务名, status="running"）。
    """
    services: dict[str, ServiceEntity] = {}
    relations: list[Relation] = []

    for er in extracted_relations:
        if er.relation_type == "calls_service":
            svc_name = er.target_name
            if svc_name not in services:
                services[svc_name] = ServiceEntity(
                    name=svc_name,
                    version="unknown",
                    status="running",
                )
            # 为每个 calls_service 关系生成一个 Relation（source_name → ServiceEntity）
            svc = services[svc_name]
            relations.append(
                Relation(
                    source_id=er.source_name,
                    target_id=svc.id,
                    relation_type="calls_service",
                )
            )

    return list(services.values()), relations
