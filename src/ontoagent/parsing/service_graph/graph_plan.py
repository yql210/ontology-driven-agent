from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .resolver import GraphEntity, ResolveResult, stable_id


@dataclass(frozen=True)
class GraphNode:
    id: str
    node_type: str
    props: Mapping[str, object]


@dataclass(frozen=True)
class GraphRelation:
    id: str
    relation_type: str
    source_id: str
    target_id: str
    props: Mapping[str, object]


@dataclass(frozen=True)
class GraphWritePlan:
    nodes: tuple[GraphNode, ...]
    relations: tuple[GraphRelation, ...]


class GraphPlanBuilder:
    def build(self, result: ResolveResult) -> GraphWritePlan:
        nodes: dict[str, GraphNode] = {}
        relations: list[GraphRelation] = []
        for entity in result.logical_entities:
            endpoint = self._endpoint_node(entity)
            nodes[endpoint.id] = endpoint
            service_id = stable_id(
                {
                    "kind": "service",
                    "repo_id": entity.repo_id,
                    "generation_id": entity.generation_id,
                    "protocol": entity.protocol,
                }
            )
            nodes[service_id] = GraphNode(
                service_id,
                "ServiceDefinition",
                {
                    "id": service_id,
                    "repo_id": entity.repo_id,
                    "source_revision": entity.source_revision,
                    "generation_id": entity.generation_id,
                    "protocol": entity.protocol,
                    "canonical_key": entity.canonical_key,
                    "role": entity.role,
                    "evidence_ids": entity.evidence_ids,
                },
            )
            relations.append(
                self._relation(
                    "PROVIDES_ENDPOINT" if entity.role == "provider" else "CONSUMES_ENDPOINT",
                    service_id,
                    endpoint.id,
                    entity,
                    {},
                )
            )
            for evidence_id in entity.evidence_ids:
                evidence = GraphNode(
                    evidence_id,
                    "Evidence",
                    {
                        "id": evidence_id,
                        "repo_id": entity.repo_id,
                        "source_revision": entity.source_revision,
                        "generation_id": entity.generation_id,
                        "protocol": entity.protocol,
                        "canonical_key": entity.canonical_key,
                        "role": entity.role,
                        "evidence_ids": (evidence_id,),
                    },
                )
                nodes[evidence_id] = evidence
                relations.append(self._relation("SUPPORTED_BY_EVIDENCE", endpoint.id, evidence_id, entity, {}))
        entities = {entity.id: entity for entity in result.logical_entities}
        for link in result.resolved_links:
            provider = entities[link.provider_fact_id]
            consumer = entities[link.consumer_fact_id]
            relations.append(
                self._relation(
                    "DEPENDS_ON",
                    consumer.id,
                    provider.id,
                    consumer,
                    {
                        "match_rule": link.match_rule,
                        "confidence": link.confidence,
                        "evidence_ids": link.evidence_ids,
                        "provider_repo_id": link.provider_repo_id,
                        "provider_generation_id": link.provider_generation_id,
                        "consumer_repo_id": link.consumer_repo_id,
                        "consumer_generation_id": link.consumer_generation_id,
                        "provider_source_revision": link.provider_source_revision,
                        "consumer_source_revision": link.consumer_source_revision,
                        "consumer_group": link.consumer_group,
                    },
                )
            )
        return GraphWritePlan(
            tuple(sorted(nodes.values(), key=lambda node: node.id)),
            tuple(sorted(relations, key=lambda relation: relation.id)),
        )

    @staticmethod
    def _endpoint_node(entity: GraphEntity) -> GraphNode:
        return GraphNode(
            entity.id,
            "Endpoint",
            {
                "id": entity.id,
                "repo_id": entity.repo_id,
                "source_revision": entity.source_revision,
                "generation_id": entity.generation_id,
                "protocol": entity.protocol,
                "canonical_key": entity.canonical_key,
                "role": entity.role,
                "evidence_ids": entity.evidence_ids,
            },
        )

    @staticmethod
    def _relation(
        relation_type: str, source_id: str, target_id: str, entity: GraphEntity, extra: Mapping[str, object]
    ) -> GraphRelation:
        props = {
            "source_revision": entity.source_revision,
            "generation_id": entity.generation_id,
            "canonical_key": entity.canonical_key,
            "match_rule": None,
            "confidence": None,
            "evidence_ids": entity.evidence_ids,
            **extra,
        }
        relation_id = stable_id({"type": relation_type, "source": source_id, "target": target_id, "props": props})
        return GraphRelation(relation_id, relation_type, source_id, target_id, props)
