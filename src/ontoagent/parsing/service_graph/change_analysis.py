"""Pure, receipt-confirmed comparison of two service graph generations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from .graph_plan import GraphNode, GraphRelation, GraphWritePlan
from .graph_writer import WriteReceipt


class ServiceGraphChangeAnalysisStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class ServiceGraphChangeAnalysisBlockReason(StrEnum):
    MALFORMED_REQUEST = "malformed_request"
    MALFORMED_GRAPH = "malformed_graph"
    MALFORMED_RECEIPT = "malformed_receipt"
    UNCONFIRMED_READBACK = "unconfirmed_readback"
    RECEIPT_COUNT_MISMATCH = "receipt_count_mismatch"
    RECEIPT_READBACK_MISMATCH = "receipt_readback_mismatch"
    NODE_PROVENANCE_MISMATCH = "node_provenance_mismatch"
    RELATION_PROVENANCE_MISMATCH = "relation_provenance_mismatch"
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    REPO_MISMATCH = "repo_mismatch"
    GENERATION_MISMATCH = "generation_mismatch"


@dataclass(frozen=True)
class ServiceEndpointContract:
    """A repository-scoped endpoint contract identity."""

    repo_id: str
    generation_id: str
    protocol: str
    canonical_key: str
    role: str

    def __post_init__(self) -> None:
        if not all(_is_nonblank(value) for value in self.__dict__.values()):
            raise ValueError("endpoint contract fields must be nonblank")

    def to_dict(self) -> dict[str, str]:
        return {
            "repo_id": self.repo_id,
            "generation_id": self.generation_id,
            "protocol": self.protocol,
            "canonical_key": self.canonical_key,
            "role": self.role,
        }


@dataclass(frozen=True)
class ServiceEndpointFact:
    """A contract together with the observed source fact provenance."""

    endpoint_id: str
    contract: ServiceEndpointContract
    source_revision: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _is_nonblank(self.endpoint_id) or type(self.contract) is not ServiceEndpointContract:
            raise ValueError("endpoint fact identity is invalid")
        if not _is_nonblank(self.source_revision) or _evidence_ids(self.evidence_ids) is None:
            raise ValueError("endpoint fact provenance is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "endpoint_id": self.endpoint_id,
            "contract": self.contract.to_dict(),
            "source_revision": self.source_revision,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class EndpointAddition:
    contract: ServiceEndpointContract

    def to_dict(self) -> dict[str, object]:
        return {"contract": self.contract.to_dict()}


@dataclass(frozen=True)
class EndpointDeletion:
    contract: ServiceEndpointContract

    def to_dict(self) -> dict[str, object]:
        return {"contract": self.contract.to_dict()}


@dataclass(frozen=True)
class EndpointContractChange:
    endpoint_id: str
    before: ServiceEndpointContract
    after: ServiceEndpointContract

    def __post_init__(self) -> None:
        if not _is_nonblank(self.endpoint_id) or type(self.before) is not ServiceEndpointContract:
            raise ValueError("contract change identity is invalid")
        if type(self.after) is not ServiceEndpointContract:
            raise ValueError("contract change must have an after contract")

    def to_dict(self) -> dict[str, object]:
        return {"endpoint_id": self.endpoint_id, "before": self.before.to_dict(), "after": self.after.to_dict()}


@dataclass(frozen=True)
class EndpointFactRevision:
    before: ServiceEndpointFact
    after: ServiceEndpointFact

    def __post_init__(self) -> None:
        if type(self.before) is not ServiceEndpointFact or type(self.after) is not ServiceEndpointFact:
            raise ValueError("fact revision requires endpoint facts")

    def to_dict(self) -> dict[str, object]:
        return {"before": self.before.to_dict(), "after": self.after.to_dict()}


@dataclass(frozen=True)
class DirectServiceGraphImpact:
    changed_endpoint: ServiceEndpointContract
    impacted_endpoint: ServiceEndpointContract
    relation_id: str

    def __post_init__(self) -> None:
        if (
            type(self.changed_endpoint) is not ServiceEndpointContract
            or type(self.impacted_endpoint) is not ServiceEndpointContract
        ):
            raise ValueError("direct impact requires endpoint contracts")
        if not _is_nonblank(self.relation_id):
            raise ValueError("direct impact relation_id must be nonblank")

    def to_dict(self) -> dict[str, object]:
        return {
            "changed_endpoint": self.changed_endpoint.to_dict(),
            "impacted_endpoint": self.impacted_endpoint.to_dict(),
            "relation_id": self.relation_id,
        }


@dataclass(frozen=True)
class ServiceGraphChangeAnalysisResult:
    status: ServiceGraphChangeAnalysisStatus
    repo_id: str
    from_generation: str | None
    to_generation: str | None
    reasons: tuple[ServiceGraphChangeAnalysisBlockReason, ...]
    endpoint_additions: tuple[EndpointAddition, ...]
    endpoint_deletions: tuple[EndpointDeletion, ...]
    contract_changes: tuple[EndpointContractChange, ...]
    fact_revisions: tuple[EndpointFactRevision, ...]
    direct_impacts: tuple[DirectServiceGraphImpact, ...]

    def __post_init__(self) -> None:
        if type(self.status) is not ServiceGraphChangeAnalysisStatus or not _is_nonblank(self.repo_id):
            raise ValueError("analysis status and repo_id are invalid")
        collections = (
            (self.reasons, ServiceGraphChangeAnalysisBlockReason),
            (self.endpoint_additions, EndpointAddition),
            (self.endpoint_deletions, EndpointDeletion),
            (self.contract_changes, EndpointContractChange),
            (self.fact_revisions, EndpointFactRevision),
            (self.direct_impacts, DirectServiceGraphImpact),
        )
        if any(
            type(items) is not tuple or any(type(item) is not item_type for item in items)
            for items, item_type in collections
        ):
            raise ValueError("analysis collections must be immutable DTO tuples")
        if self.status is ServiceGraphChangeAnalysisStatus.READY:
            if not _is_nonblank(self.from_generation) or not _is_nonblank(self.to_generation) or self.reasons:
                raise ValueError("ready analysis requires both generations and no reasons")
        elif (
            self.from_generation is not None
            or self.to_generation is not None
            or not self.reasons
            or any(
                (
                    self.endpoint_additions,
                    self.endpoint_deletions,
                    self.contract_changes,
                    self.fact_revisions,
                    self.direct_impacts,
                )
            )
        ):
            raise ValueError("blocked analysis requires reasons and no generations")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "repo_id": self.repo_id,
            "from_generation": self.from_generation,
            "to_generation": self.to_generation,
            "reasons": [reason.value for reason in self.reasons],
            "endpoint_additions": [item.to_dict() for item in self.endpoint_additions],
            "endpoint_deletions": [item.to_dict() for item in self.endpoint_deletions],
            "contract_changes": [item.to_dict() for item in self.contract_changes],
            "fact_revisions": [item.to_dict() for item in self.fact_revisions],
            "direct_impacts": [item.to_dict() for item in self.direct_impacts],
        }


class ServiceGraphChangeAnalysis:
    """Compare two explicit repository generations without storage or source control access."""

    def __init__(self, from_plan: object, from_receipt: object, to_plan: object, to_receipt: object) -> None:
        self._from_plan = from_plan
        self._from_receipt = from_receipt
        self._to_plan = to_plan
        self._to_receipt = to_receipt

    def analyze(
        self, repo_id: object, from_generation: object, to_generation: object
    ) -> ServiceGraphChangeAnalysisResult:
        if not all(_is_nonblank(value) for value in (repo_id, from_generation, to_generation)):
            return _blocked(ServiceGraphChangeAnalysisBlockReason.MALFORMED_REQUEST)
        request = (repo_id.strip(), from_generation.strip(), to_generation.strip())
        for plan, receipt, generation in (
            (self._from_plan, self._from_receipt, request[1]),
            (self._to_plan, self._to_receipt, request[2]),
        ):
            reason = _validate_source(plan, receipt)
            if reason is not None:
                return _blocked(reason)
            assert isinstance(plan, GraphWritePlan)
            identity_reason = _identity_failure(plan, request[0], generation)
            if identity_reason is not None:
                return _blocked(identity_reason)

        from_nodes = _endpoint_nodes(self._from_plan, request[0], request[1])
        to_nodes = _endpoint_nodes(self._to_plan, request[0], request[2])
        common_ids = from_nodes.keys() & to_nodes.keys()
        changed_ids = {
            node_id for node_id in common_ids if _contract(from_nodes[node_id]) != _contract(to_nodes[node_id])
        }
        from_remaining = {node_id: node for node_id, node in from_nodes.items() if node_id not in common_ids}
        to_remaining = {node_id: node for node_id, node in to_nodes.items() if node_id not in common_ids}
        from_by_contract = {_contract_key(node): node for node in from_remaining.values()}
        to_by_contract = {_contract_key(node): node for node in to_remaining.values()}
        shared_contracts = from_by_contract.keys() & to_by_contract.keys()
        additions = tuple(
            EndpointAddition(_contract(to_by_contract[key])) for key in sorted(to_by_contract.keys() - shared_contracts)
        )
        deletions = tuple(
            EndpointDeletion(_contract(from_by_contract[key]))
            for key in sorted(from_by_contract.keys() - shared_contracts)
        )
        revisions = tuple(
            EndpointFactRevision(_fact(from_by_contract[key]), _fact(to_by_contract[key]))
            for key in sorted(shared_contracts)
            if _fact_provenance(from_by_contract[key]) != _fact_provenance(to_by_contract[key])
        )
        contract_changes = tuple(
            EndpointContractChange(node_id, _contract(from_nodes[node_id]), _contract(to_nodes[node_id]))
            for node_id in sorted(changed_ids)
        )
        impact_nodes = [from_nodes[node_id] for node_id in changed_ids]
        impact_nodes.extend(from_by_contract[key] for key in from_by_contract.keys() - shared_contracts)
        impact_nodes.extend(to_by_contract[key] for key in to_by_contract.keys() - shared_contracts)
        impacts = _direct_impacts(
            tuple(impact_nodes),
            (self._from_plan, self._to_plan),
        )
        return ServiceGraphChangeAnalysisResult(
            ServiceGraphChangeAnalysisStatus.READY,
            request[0],
            request[1],
            request[2],
            (),
            additions,
            deletions,
            contract_changes,
            revisions,
            impacts,
        )


_NODE_TYPES = frozenset({"ServiceDefinition", "Endpoint", "Evidence"})
_RELATION_TYPES = frozenset({"PROVIDES_ENDPOINT", "CONSUMES_ENDPOINT", "DEPENDS_ON", "SUPPORTED_BY_EVIDENCE"})


def _validate_source(plan: object, receipt: object) -> ServiceGraphChangeAnalysisBlockReason | None:
    if type(plan) is not GraphWritePlan or type(receipt) is not WriteReceipt:
        return ServiceGraphChangeAnalysisBlockReason.MALFORMED_GRAPH
    if (
        type(receipt.confirmed) is not bool
        or type(receipt.node_count) is not int
        or type(receipt.relation_count) is not int
        or type(receipt.readback) is not GraphWritePlan
        or not _is_nonblank(receipt.graph_namespace)
    ):
        return ServiceGraphChangeAnalysisBlockReason.MALFORMED_RECEIPT
    if not receipt.confirmed:
        return ServiceGraphChangeAnalysisBlockReason.UNCONFIRMED_READBACK
    if receipt.node_count != len(plan.nodes) or receipt.relation_count != len(plan.relations):
        return ServiceGraphChangeAnalysisBlockReason.RECEIPT_COUNT_MISMATCH
    if receipt.readback != plan:
        return ServiceGraphChangeAnalysisBlockReason.RECEIPT_READBACK_MISMATCH
    return _validate_plan(plan)


def _validate_plan(plan: GraphWritePlan) -> ServiceGraphChangeAnalysisBlockReason | None:
    if type(plan.nodes) is not tuple or type(plan.relations) is not tuple:
        return ServiceGraphChangeAnalysisBlockReason.MALFORMED_GRAPH
    if any(type(node) is not GraphNode or not isinstance(node.props, Mapping) for node in plan.nodes) or any(
        type(relation) is not GraphRelation or not isinstance(relation.props, Mapping) for relation in plan.relations
    ):
        return ServiceGraphChangeAnalysisBlockReason.MALFORMED_GRAPH
    nodes = {node.id: node for node in plan.nodes}
    relations = {relation.id: relation for relation in plan.relations}
    if len(nodes) != len(plan.nodes) or len(relations) != len(plan.relations):
        return ServiceGraphChangeAnalysisBlockReason.MALFORMED_GRAPH
    for node in nodes.values():
        if node.node_type not in _NODE_TYPES or not _is_nonblank(node.id):
            return ServiceGraphChangeAnalysisBlockReason.MALFORMED_GRAPH
        if not _node_has_valid_provenance(node):
            return ServiceGraphChangeAnalysisBlockReason.NODE_PROVENANCE_MISMATCH
    for relation in relations.values():
        if relation.relation_type not in _RELATION_TYPES or not _is_nonblank(relation.id):
            return ServiceGraphChangeAnalysisBlockReason.MALFORMED_GRAPH
        if relation.source_id not in nodes or relation.target_id not in nodes:
            return ServiceGraphChangeAnalysisBlockReason.MALFORMED_GRAPH
        if not _relation_has_valid_provenance(relation, nodes):
            return ServiceGraphChangeAnalysisBlockReason.RELATION_PROVENANCE_MISMATCH
    evidence_ids = {node.id for node in nodes.values() if node.node_type == "Evidence"}
    if any(
        evidence_id not in evidence_ids
        for item in (*nodes.values(), *relations.values())
        for evidence_id in item.props["evidence_ids"]
    ):
        return ServiceGraphChangeAnalysisBlockReason.EVIDENCE_NOT_FOUND
    return None


def _node_has_valid_provenance(node: GraphNode) -> bool:
    props = node.props
    return (
        props.get("id") == node.id
        and all(
            _is_nonblank(props.get(key))
            for key in ("repo_id", "generation_id", "source_revision", "protocol", "canonical_key", "role")
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
    if relation.relation_type != "DEPENDS_ON":
        return True
    target = nodes[relation.target_id]
    keys = (
        "provider_repo_id",
        "provider_generation_id",
        "provider_source_revision",
        "consumer_repo_id",
        "consumer_generation_id",
        "consumer_source_revision",
    )
    return (
        all(_is_nonblank(props.get(key)) for key in keys)
        and (props["consumer_repo_id"], props["consumer_generation_id"], props["consumer_source_revision"])
        == (source.props["repo_id"], source.props["generation_id"], source.props["source_revision"])
        and (props["provider_repo_id"], props["provider_generation_id"], props["provider_source_revision"])
        == (target.props["repo_id"], target.props["generation_id"], target.props["source_revision"])
    )


def _identity_failure(
    plan: GraphWritePlan, repo_id: str, generation_id: str
) -> ServiceGraphChangeAnalysisBlockReason | None:
    repo_nodes = [node for node in plan.nodes if node.props["repo_id"] == repo_id]
    if not repo_nodes:
        return ServiceGraphChangeAnalysisBlockReason.REPO_MISMATCH
    if not any(node.props["generation_id"] == generation_id for node in repo_nodes):
        return ServiceGraphChangeAnalysisBlockReason.GENERATION_MISMATCH
    return None


def _endpoint_nodes(plan: object, repo_id: str, generation_id: str) -> dict[str, GraphNode]:
    assert isinstance(plan, GraphWritePlan)
    return {
        node.id: node
        for node in plan.nodes
        if node.node_type == "Endpoint"
        and (node.props["repo_id"], node.props["generation_id"]) == (repo_id, generation_id)
    }


def _contract(node: GraphNode) -> ServiceEndpointContract:
    return ServiceEndpointContract(
        node.props["repo_id"],
        node.props["generation_id"],
        node.props["protocol"],
        node.props["canonical_key"],
        node.props["role"],
    )  # type: ignore[arg-type]


def _fact(node: GraphNode) -> ServiceEndpointFact:
    return ServiceEndpointFact(node.id, _contract(node), node.props["source_revision"], node.props["evidence_ids"])  # type: ignore[arg-type]


def _contract_key(node: GraphNode) -> tuple[str, str, str, str]:
    contract = _contract(node)
    return (contract.repo_id, contract.protocol, contract.canonical_key, contract.role)


def _fact_provenance(node: GraphNode) -> tuple[str, tuple[str, ...]]:
    return node.props["source_revision"], node.props["evidence_ids"]  # type: ignore[return-value]


def _direct_impacts(
    changed_nodes: tuple[GraphNode, ...], plans: tuple[object, object]
) -> tuple[DirectServiceGraphImpact, ...]:
    impacts: dict[tuple[str, str], DirectServiceGraphImpact] = {}
    for plan in plans:
        assert isinstance(plan, GraphWritePlan)
        nodes = {node.id: node for node in plan.nodes}
        for relation in plan.relations:
            if relation.relation_type != "DEPENDS_ON":
                continue
            for changed in changed_nodes:
                if changed.id not in {relation.source_id, relation.target_id}:
                    continue
                counterpart_id = relation.target_id if relation.source_id == changed.id else relation.source_id
                counterpart = nodes[counterpart_id]
                if counterpart.node_type != "Endpoint" or counterpart.props["repo_id"] == changed.props["repo_id"]:
                    continue
                impact = DirectServiceGraphImpact(_contract(changed), _contract(counterpart), relation.id)
                impacts[(impact.changed_endpoint.repo_id + impact.changed_endpoint.canonical_key, relation.id)] = impact
    return tuple(
        sorted(
            impacts.values(),
            key=lambda item: (item.changed_endpoint.repo_id, item.changed_endpoint.canonical_key, item.relation_id),
        )
    )


def _blocked(reason: ServiceGraphChangeAnalysisBlockReason) -> ServiceGraphChangeAnalysisResult:
    return ServiceGraphChangeAnalysisResult(
        ServiceGraphChangeAnalysisStatus.BLOCKED, "unknown", None, None, (reason,), (), (), (), (), ()
    )


def _evidence_ids(value: object) -> tuple[str, ...] | None:
    if type(value) is not tuple or not value or not all(_is_nonblank(item) for item in value):
        return None
    return value


def _is_json_value(value: object) -> bool:
    if value is None or type(value) in {str, int, float, bool}:
        return True
    if isinstance(value, Mapping):
        return all(type(key) is str and _is_json_value(item) for key, item in value.items())
    return isinstance(value, (tuple, list)) and all(_is_json_value(item) for item in value)


def _is_nonblank(value: object) -> bool:
    return type(value) is str and bool(value.strip())
