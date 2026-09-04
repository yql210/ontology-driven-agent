"""Deterministic, receipt-confirmed queries over a service graph write plan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .graph_plan import GraphNode, GraphRelation, GraphWritePlan
from .graph_writer import WriteReceipt


class ServiceGraphQueryStatus(StrEnum):
    """Whether a service graph query was safely evaluated."""

    READY = "ready"
    BLOCKED = "blocked"


class ServiceGraphQueryBlockReason(StrEnum):
    """Stable, fail-closed reasons for an unavailable service graph query."""

    MALFORMED_REQUEST = "malformed_request"
    MALFORMED_RECEIPT = "malformed_receipt"
    UNCONFIRMED_READBACK = "unconfirmed_readback"
    RECEIPT_COUNT_MISMATCH = "receipt_count_mismatch"
    RECEIPT_READBACK_MISMATCH = "receipt_readback_mismatch"
    MALFORMED_GRAPH = "malformed_graph"
    NODE_PROVENANCE_MISMATCH = "node_provenance_mismatch"
    RELATION_PROVENANCE_MISMATCH = "relation_provenance_mismatch"
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    REPO_MISMATCH = "repo_mismatch"
    GENERATION_MISMATCH = "generation_mismatch"
    NODE_NOT_FOUND = "node_not_found"
    ENTITY_OR_RELATION_NOT_FOUND = "entity_or_relation_not_found"
    MISSING_ACTIVE = "missing_active"
    MALFORMED_RECORD = "malformed_record"
    NAMESPACE_MISMATCH = "namespace_mismatch"
    NOT_READY = "not_ready"
    UNCONFIRMED_RECEIPT = "unconfirmed_receipt"


@dataclass(frozen=True)
class ServiceGraphNodeResult:
    """An immutable, JSON-safe graph node returned by a service graph query."""

    id: str
    node_type: str
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        if not _is_nonblank(self.id) or not _is_nonblank(self.node_type):
            raise ValueError("node result identity must be nonblank")
        object.__setattr__(self, "properties", _freeze_mapping(self.properties))

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "node_type": self.node_type, "properties": _json_value(self.properties)}


@dataclass(frozen=True)
class ServiceGraphRelationResult:
    """An immutable, JSON-safe graph relation returned by a service graph query."""

    id: str
    relation_type: str
    source_id: str
    target_id: str
    properties: Mapping[str, object]

    def __post_init__(self) -> None:
        if not all(_is_nonblank(value) for value in (self.id, self.relation_type, self.source_id, self.target_id)):
            raise ValueError("relation result identity must be nonblank")
        object.__setattr__(self, "properties", _freeze_mapping(self.properties))

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "relation_type": self.relation_type,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "properties": _json_value(self.properties),
        }


@dataclass(frozen=True)
class ServiceGraphQueryResult:
    """A generation-bound query envelope which distinguishes empty from blocked results."""

    status: ServiceGraphQueryStatus
    repo_id: str
    generation_id: str | None
    reasons: tuple[ServiceGraphQueryBlockReason, ...]
    nodes: tuple[ServiceGraphNodeResult, ...]
    relations: tuple[ServiceGraphRelationResult, ...]

    def __post_init__(self) -> None:
        if type(self.status) is not ServiceGraphQueryStatus or not _is_nonblank(self.repo_id):
            raise ValueError("query result status and repo_id are invalid")
        if self.generation_id is not None and not _is_nonblank(self.generation_id):
            raise ValueError("generation_id must be nonblank or None")
        if type(self.reasons) is not tuple or any(
            type(reason) is not ServiceGraphQueryBlockReason for reason in self.reasons
        ):
            raise ValueError("reasons must be a tuple of service graph query block reasons")
        if type(self.nodes) is not tuple or any(type(node) is not ServiceGraphNodeResult for node in self.nodes):
            raise ValueError("nodes must be a tuple of service graph node results")
        if type(self.relations) is not tuple or any(
            type(relation) is not ServiceGraphRelationResult for relation in self.relations
        ):
            raise ValueError("relations must be a tuple of service graph relation results")
        if self.status is ServiceGraphQueryStatus.READY:
            if self.generation_id is None or self.reasons:
                raise ValueError("ready query results require a generation and no reasons")
        elif self.generation_id is not None or not self.reasons or self.nodes or self.relations:
            raise ValueError("blocked query results require reasons and no graph results")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "repo_id": self.repo_id,
            "generation_id": self.generation_id,
            "reasons": [reason.value for reason in self.reasons],
            "nodes": [node.to_dict() for node in self.nodes],
            "relations": [relation.to_dict() for relation in self.relations],
        }


class ServiceGraphQuery:
    """Query an explicit, exact-readback-confirmed :class:`GraphWritePlan` without backend access."""

    def __init__(self, plan: object, receipt: object) -> None:
        self._plan = plan
        self._receipt = receipt

    def service_directory(self, repo_id: object, generation_id: object) -> ServiceGraphQueryResult:
        """List service definitions in one explicit repository generation."""
        request, blocked = self._authorize(repo_id, generation_id)
        if blocked is not None:
            return blocked
        assert request is not None
        nodes = tuple(
            _node_result(node)
            for node in self._nodes().values()
            if node.node_type == "ServiceDefinition" and _matches_identity(node, *request)
        )
        return _ready(*request, nodes=nodes)

    def find_endpoint_providers(
        self, repo_id: object, generation_id: object, endpoint_key: object
    ) -> ServiceGraphQueryResult:
        """Find providers for an endpoint key within the confirmed generation graph."""
        return self._find_endpoints(repo_id, generation_id, endpoint_key, "provider")

    def find_endpoint_provider(
        self, repo_id: object, generation_id: object, endpoint_key: object
    ) -> ServiceGraphQueryResult:
        """Compatibility spelling for provider lookup; multiple providers may be returned."""
        return self.find_endpoint_providers(repo_id, generation_id, endpoint_key)

    def find_endpoint_consumers(
        self, repo_id: object, generation_id: object, endpoint_key: object
    ) -> ServiceGraphQueryResult:
        """Find consumers for an endpoint key within the confirmed generation graph."""
        return self._find_endpoints(repo_id, generation_id, endpoint_key, "consumer")

    def find_service_dependencies(
        self, repo_id: object, generation_id: object, service_id: object
    ) -> ServiceGraphQueryResult:
        """Return outbound dependencies for one service or endpoint in its explicit generation."""
        request, blocked = self._authorize(repo_id, generation_id)
        if blocked is not None:
            return blocked
        assert request is not None
        if not _is_nonblank(service_id):
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_REQUEST)
        nodes = self._nodes()
        target = nodes.get(service_id)
        if target is None:
            return _blocked(ServiceGraphQueryBlockReason.NODE_NOT_FOUND)
        if not _matches_identity(target, *request):
            return _blocked(_identity_mismatch_reason(target, *request))
        endpoint_ids = {target.id}
        if target.node_type == "ServiceDefinition":
            endpoint_ids = {
                relation.target_id
                for relation in self._relations().values()
                if relation.source_id == target.id
                and relation.relation_type in {"PROVIDES_ENDPOINT", "CONSUMES_ENDPOINT"}
            }
        if target.node_type not in {"ServiceDefinition", "Endpoint"}:
            return _blocked(ServiceGraphQueryBlockReason.NODE_NOT_FOUND)
        relations = tuple(
            relation
            for relation in self._relations().values()
            if relation.relation_type == "DEPENDS_ON" and relation.source_id in endpoint_ids
        )
        result_nodes = {
            node_id: nodes[node_id] for relation in relations for node_id in (relation.source_id, relation.target_id)
        }
        return _ready(
            *request,
            nodes=tuple(_node_result(node) for node in result_nodes.values()),
            relations=tuple(_relation_result(relation) for relation in relations),
        )

    def get_evidence(
        self, repo_id: object, generation_id: object, entity_or_relation_id: object
    ) -> ServiceGraphQueryResult:
        """Return evidence nodes directly referenced by one entity or relation ID."""
        request, blocked = self._authorize(repo_id, generation_id)
        if blocked is not None:
            return blocked
        assert request is not None
        if not _is_nonblank(entity_or_relation_id):
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_REQUEST)
        nodes = self._nodes()
        item: GraphNode | GraphRelation | None = nodes.get(entity_or_relation_id) or self._relations().get(
            entity_or_relation_id
        )
        if item is None:
            return _blocked(ServiceGraphQueryBlockReason.ENTITY_OR_RELATION_NOT_FOUND)
        owner = item if isinstance(item, GraphNode) else nodes[item.source_id]
        if not _matches_identity(owner, *request):
            return _blocked(_identity_mismatch_reason(owner, *request))
        evidence_ids = item.props["evidence_ids"]
        return _ready(*request, nodes=tuple(_node_result(nodes[evidence_id]) for evidence_id in evidence_ids))

    def _find_endpoints(
        self, repo_id: object, generation_id: object, endpoint_key: object, role: str
    ) -> ServiceGraphQueryResult:
        request, blocked = self._authorize(repo_id, generation_id)
        if blocked is not None:
            return blocked
        assert request is not None
        if not _is_nonblank(endpoint_key):
            return _blocked(ServiceGraphQueryBlockReason.MALFORMED_REQUEST)
        nodes = tuple(
            _node_result(node)
            for node in self._nodes().values()
            if node.node_type == "Endpoint"
            and node.props["generation_id"] == request[1]
            and node.props["canonical_key"] == endpoint_key
            and node.props["role"] == role
        )
        return _ready(*request, nodes=nodes)

    def _authorize(
        self, repo_id: object, generation_id: object
    ) -> tuple[tuple[str, str] | None, ServiceGraphQueryResult | None]:
        if not _is_nonblank(repo_id) or not _is_nonblank(generation_id):
            return None, _blocked(ServiceGraphQueryBlockReason.MALFORMED_REQUEST)
        validation = _validate_source(self._plan, self._receipt)
        if validation is not None:
            return None, _blocked(validation)
        request = (repo_id.strip(), generation_id.strip())
        nodes = self._nodes().values()
        if not any(node.props["repo_id"] == request[0] for node in nodes):
            return None, _blocked(ServiceGraphQueryBlockReason.REPO_MISMATCH)
        if not any(_matches_identity(node, *request) for node in nodes):
            return None, _blocked(ServiceGraphQueryBlockReason.GENERATION_MISMATCH)
        return request, None

    def _nodes(self) -> dict[str, GraphNode]:
        assert isinstance(self._plan, GraphWritePlan)
        return {node.id: node for node in self._plan.nodes}

    def _relations(self) -> dict[str, GraphRelation]:
        assert isinstance(self._plan, GraphWritePlan)
        return {relation.id: relation for relation in self._plan.relations}


_NODE_TYPES = frozenset({"ServiceDefinition", "Endpoint", "Evidence"})
_RELATION_TYPES = frozenset({"PROVIDES_ENDPOINT", "CONSUMES_ENDPOINT", "DEPENDS_ON", "SUPPORTED_BY_EVIDENCE"})


def _validate_source(plan: object, receipt: object) -> ServiceGraphQueryBlockReason | None:
    if type(plan) is not GraphWritePlan or type(receipt) is not WriteReceipt:
        return ServiceGraphQueryBlockReason.MALFORMED_GRAPH
    if (
        type(receipt.confirmed) is not bool
        or type(receipt.node_count) is not int
        or type(receipt.relation_count) is not int
        or type(receipt.readback) is not GraphWritePlan
        or not _is_nonblank(receipt.graph_namespace)
    ):
        return ServiceGraphQueryBlockReason.MALFORMED_RECEIPT
    if not receipt.confirmed:
        return ServiceGraphQueryBlockReason.UNCONFIRMED_READBACK
    if receipt.node_count != len(plan.nodes) or receipt.relation_count != len(plan.relations):
        return ServiceGraphQueryBlockReason.RECEIPT_COUNT_MISMATCH
    if receipt.readback != plan:
        return ServiceGraphQueryBlockReason.RECEIPT_READBACK_MISMATCH
    return _validate_plan(plan)


def _validate_plan(plan: GraphWritePlan) -> ServiceGraphQueryBlockReason | None:
    if type(plan.nodes) is not tuple or type(plan.relations) is not tuple:
        return ServiceGraphQueryBlockReason.MALFORMED_GRAPH
    if any(type(node) is not GraphNode or not isinstance(node.props, Mapping) for node in plan.nodes) or any(
        type(relation) is not GraphRelation or not isinstance(relation.props, Mapping) for relation in plan.relations
    ):
        return ServiceGraphQueryBlockReason.MALFORMED_GRAPH
    nodes = {node.id: node for node in plan.nodes}
    relations = {relation.id: relation for relation in plan.relations}
    if len(nodes) != len(plan.nodes) or len(relations) != len(plan.relations):
        return ServiceGraphQueryBlockReason.MALFORMED_GRAPH
    for node in nodes.values():
        if node.node_type not in _NODE_TYPES or not _is_nonblank(node.id):
            return ServiceGraphQueryBlockReason.MALFORMED_GRAPH
        if not _node_has_valid_provenance(node):
            return ServiceGraphQueryBlockReason.NODE_PROVENANCE_MISMATCH
    for relation in relations.values():
        if relation.relation_type not in _RELATION_TYPES or not _is_nonblank(relation.id):
            return ServiceGraphQueryBlockReason.MALFORMED_GRAPH
        if relation.source_id not in nodes or relation.target_id not in nodes:
            return ServiceGraphQueryBlockReason.MALFORMED_GRAPH
        if not _relation_has_valid_provenance(relation, nodes):
            return ServiceGraphQueryBlockReason.RELATION_PROVENANCE_MISMATCH
    evidence_ids = {node.id for node in nodes.values() if node.node_type == "Evidence"}
    if any(
        evidence_id not in evidence_ids
        for item in (*nodes.values(), *relations.values())
        for evidence_id in item.props["evidence_ids"]
    ):
        return ServiceGraphQueryBlockReason.EVIDENCE_NOT_FOUND
    return None


def _node_has_valid_provenance(node: GraphNode) -> bool:
    props = node.props
    return (
        props.get("id") == node.id
        and all(
            _is_nonblank(props.get(key)) for key in ("repo_id", "generation_id", "source_revision", "canonical_key")
        )
        and _evidence_ids(props.get("evidence_ids")) is not None
        and _is_json_value(props)
    )


def _relation_has_valid_provenance(relation: GraphRelation, nodes: Mapping[str, GraphNode]) -> bool:
    props = relation.props
    source = nodes[relation.source_id]
    if not (
        all(_is_nonblank(props.get(key)) for key in ("generation_id", "source_revision", "canonical_key"))
        and _evidence_ids(props.get("evidence_ids")) is not None
        and _is_json_value(props)
        and (props["generation_id"], props["source_revision"])
        == (source.props["generation_id"], source.props["source_revision"])
    ):
        return False
    if relation.relation_type == "DEPENDS_ON":
        target = nodes[relation.target_id]
        side_keys = (
            "provider_repo_id",
            "provider_generation_id",
            "provider_source_revision",
            "consumer_repo_id",
            "consumer_generation_id",
            "consumer_source_revision",
        )
        if not all(_is_nonblank(props.get(key)) for key in side_keys):
            return False
        return (
            props["consumer_repo_id"],
            props["consumer_generation_id"],
            props["consumer_source_revision"],
        ) == (source.props["repo_id"], source.props["generation_id"], source.props["source_revision"]) and (
            props["provider_repo_id"],
            props["provider_generation_id"],
            props["provider_source_revision"],
        ) == (target.props["repo_id"], target.props["generation_id"], target.props["source_revision"])
    return True


def _node_result(node: GraphNode) -> ServiceGraphNodeResult:
    return ServiceGraphNodeResult(node.id, node.node_type, node.props)


def _relation_result(relation: GraphRelation) -> ServiceGraphRelationResult:
    return ServiceGraphRelationResult(
        relation.id, relation.relation_type, relation.source_id, relation.target_id, relation.props
    )


def _ready(
    repo_id: str,
    generation_id: str,
    *,
    nodes: tuple[ServiceGraphNodeResult, ...] = (),
    relations: tuple[ServiceGraphRelationResult, ...] = (),
) -> ServiceGraphQueryResult:
    return ServiceGraphQueryResult(ServiceGraphQueryStatus.READY, repo_id, generation_id, (), nodes, relations)


def _blocked(reason: ServiceGraphQueryBlockReason) -> ServiceGraphQueryResult:
    return ServiceGraphQueryResult(ServiceGraphQueryStatus.BLOCKED, "unknown", None, (reason,), (), ())


def _matches_identity(node: GraphNode, repo_id: str, generation_id: str) -> bool:
    return (node.props["repo_id"], node.props["generation_id"]) == (repo_id, generation_id)


def _identity_mismatch_reason(node: GraphNode, repo_id: str, generation_id: str) -> ServiceGraphQueryBlockReason:
    return (
        ServiceGraphQueryBlockReason.REPO_MISMATCH
        if node.props["repo_id"] != repo_id
        else ServiceGraphQueryBlockReason.GENERATION_MISMATCH
    )


def _evidence_ids(value: object) -> tuple[str, ...] | None:
    if type(value) is not tuple or not value or not all(_is_nonblank(item) for item in value):
        return None
    return value


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not _is_json_value(value):
        raise ValueError("properties must be JSON-safe mappings")
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _is_json_value(value: object) -> bool:
    if value is None or type(value) in {str, int, float, bool}:
        return True
    if isinstance(value, Mapping):
        return all(type(key) is str and _is_json_value(item) for key, item in value.items())
    return isinstance(value, (tuple, list)) and all(_is_json_value(item) for item in value)


def _is_nonblank(value: object) -> bool:
    return type(value) is str and bool(value.strip())
