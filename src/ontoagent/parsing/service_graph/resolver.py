from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import DetectorFacts, HttpEndpoint, MessageEndpoint, RpcEndpoint

type Endpoint = HttpEndpoint | RpcEndpoint | MessageEndpoint
RESOLVER_REASONS = frozenset(
    {"NO_PROVIDER_MATCH", "NO_CONSUMER_MATCH", "MISSING_GENERATION", "CROSS_REPO_IDENTITY_CONFLICT"}
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class FactBatch:
    repo_id: str
    source_revision: str
    generation_id: str | None
    branch: str
    facts: tuple[DetectorFacts, ...]

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip() for value in (self.repo_id, self.source_revision, self.branch)
        ):
            raise ValueError("batch identity fields must be nonblank")
        if self.generation_id is not None and (
            not isinstance(self.generation_id, str) or not self.generation_id.strip()
        ):
            raise ValueError("generation_id must be nonblank when provided")
        if any(fact.repo_id != self.repo_id or fact.source_revision != self.source_revision for fact in self.facts):
            raise ValueError("fact batch mismatch")


@dataclass(frozen=True)
class GraphEntity:
    id: str
    repo_id: str
    source_revision: str
    generation_id: str
    protocol: str
    canonical_key: str
    role: str
    evidence_ids: tuple[str, ...]
    node_type: str = "Endpoint"


@dataclass(frozen=True)
class ResolvedLink:
    provider_repo_id: str
    provider_source_revision: str
    provider_generation_id: str
    consumer_repo_id: str
    consumer_source_revision: str
    consumer_generation_id: str
    protocol: str
    canonical_key: str
    match_key: str
    provider_fact_id: str
    consumer_fact_id: str
    evidence_ids: tuple[str, ...]
    confidence: float
    match_rule: str
    consumer_group: str | None = None


@dataclass(frozen=True)
class UnresolvedEndpoint:
    repo_id: str
    source_revision: str
    generation_id: str | None
    protocol: str
    role: str
    canonical_key: str
    fact_id: str
    evidence_ids: tuple[str, ...]
    reason_code: str
    candidate_keys: tuple[str, ...]


@dataclass(frozen=True)
class ResolveResult:
    resolved_links: tuple[ResolvedLink, ...]
    unresolved: tuple[UnresolvedEndpoint, ...]
    logical_entities: tuple[GraphEntity, ...]


def _protocol(endpoint: Endpoint) -> str:
    if isinstance(endpoint, HttpEndpoint):
        return "HTTP"
    if isinstance(endpoint, RpcEndpoint):
        return "DUBBO"
    return "MQ"


def _role(endpoint: Endpoint) -> str:
    return "provider" if endpoint.role in {"provider", "producer"} else "consumer"


def _match_key(endpoint: Endpoint) -> str:
    if isinstance(endpoint, HttpEndpoint):
        alias = endpoint.service_name.split(":", 1)[0] if endpoint.role == "provider" else endpoint.service_name
        return f"HTTP|{endpoint.method}|{endpoint.normalized_path}|{alias}"
    if isinstance(endpoint, RpcEndpoint):
        return endpoint.canonical_key
    return f"MQ|{endpoint.broker}|{endpoint.topic_or_queue}"


def _rule(protocol: str) -> str:
    return {"HTTP": "exact_http_match_key", "DUBBO": "exact_dubbo_canonical_key", "MQ": "exact_mq_destination_key"}[
        protocol
    ]


class ServiceGraphResolver:
    def resolve(self, batches: tuple[FactBatch, ...]) -> ResolveResult:
        records: list[tuple[FactBatch, DetectorFacts, Endpoint]] = []
        for batch in batches:
            for facts in batch.facts:
                records.extend(
                    (batch, facts, endpoint)
                    for endpoint in (*facts.http_endpoints, *facts.rpc_endpoints, *facts.message_endpoints)
                )
        records.sort(
            key=lambda record: (record[0].repo_id, _protocol(record[2]), _role(record[2]), record[2].canonical_key)
        )
        entities = tuple(
            self._entity(batch, facts, endpoint) for batch, facts, endpoint in records if batch.generation_id
        )
        providers: dict[tuple[str, str], list[tuple[FactBatch, DetectorFacts, Endpoint]]] = {}
        consumers: dict[tuple[str, str], list[tuple[FactBatch, DetectorFacts, Endpoint]]] = {}
        for record in records:
            if not record[0].generation_id:
                continue
            target = providers if _role(record[2]) == "provider" else consumers
            target.setdefault((_protocol(record[2]), _match_key(record[2])), []).append(record)
        links: list[ResolvedLink] = []
        unresolved: list[UnresolvedEndpoint] = []
        for batch, facts, endpoint in records:
            fact_id = self._fact_id(batch, facts, endpoint)
            if not batch.generation_id:
                unresolved.append(self._unresolved(batch, endpoint, fact_id, "MISSING_GENERATION", ()))
                continue
            key = (_protocol(endpoint), _match_key(endpoint))
            if _role(endpoint) == "provider":
                if not consumers.get(key):
                    unresolved.append(self._unresolved(batch, endpoint, fact_id, "NO_CONSUMER_MATCH", ()))
                continue
            candidates = providers.get(key, [])
            if not candidates:
                unresolved.append(self._unresolved(batch, endpoint, fact_id, "NO_PROVIDER_MATCH", ()))
                continue
            for provider_batch, provider_facts, provider in candidates:
                provider_fact_id = self._fact_id(provider_batch, provider_facts, provider)
                evidence_ids = tuple(sorted({provider.evidence_id, endpoint.evidence_id}))
                links.append(
                    ResolvedLink(
                        provider_batch.repo_id,
                        provider_batch.source_revision,
                        provider_batch.generation_id or "",
                        batch.repo_id,
                        batch.source_revision,
                        batch.generation_id,
                        _protocol(endpoint),
                        endpoint.canonical_key,
                        _match_key(endpoint),
                        provider_fact_id,
                        fact_id,
                        evidence_ids,
                        1.0,
                        _rule(_protocol(endpoint)),
                        endpoint.consumer_group if isinstance(endpoint, MessageEndpoint) else None,
                    )
                )
        return ResolveResult(
            tuple(
                sorted(
                    links,
                    key=lambda item: (item.protocol, item.match_key, item.provider_fact_id, item.consumer_fact_id),
                )
            ),
            tuple(sorted(unresolved, key=lambda item: (item.repo_id, item.protocol, item.canonical_key))),
            tuple(sorted(entities, key=lambda item: item.id)),
        )

    def _fact_id(self, batch: FactBatch, facts: DetectorFacts, endpoint: Endpoint) -> str:
        logical_id = stable_id(
            {"repo_id": batch.repo_id, "protocol": _protocol(endpoint), "canonical_key": endpoint.canonical_key}
        )
        return stable_id(
            {
                "logical_id": logical_id,
                "branch": batch.branch,
                "commit": batch.source_revision,
                "detector_id": facts.detector_id,
                "detector_version": facts.detector_version,
                "generation_id": batch.generation_id,
            }
        )

    def _entity(self, batch: FactBatch, facts: DetectorFacts, endpoint: Endpoint) -> GraphEntity:
        return GraphEntity(
            self._fact_id(batch, facts, endpoint),
            batch.repo_id,
            batch.source_revision,
            batch.generation_id or "",
            _protocol(endpoint),
            endpoint.canonical_key,
            _role(endpoint),
            (endpoint.evidence_id,),
        )

    def _unresolved(
        self, batch: FactBatch, endpoint: Endpoint, fact_id: str, reason: str, candidates: tuple[str, ...]
    ) -> UnresolvedEndpoint:
        return UnresolvedEndpoint(
            batch.repo_id,
            batch.source_revision,
            batch.generation_id,
            _protocol(endpoint),
            _role(endpoint),
            endpoint.canonical_key,
            fact_id,
            (endpoint.evidence_id,),
            reason,
            candidates,
        )
