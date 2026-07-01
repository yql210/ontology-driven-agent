"""CapabilityReasoner — transitive inference on capability relations.

Phase 2 minimal slice: PRODUCES/CONSUMES transitive inference.
If A PRODUCES X and B CONSUMES X, derive A→B dataflow dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from ontoagent.domain.schema import CapabilityEntity

__all__ = ["CapabilityReasoner", "DataflowDependency"]


@dataclass
class DataflowDependency:
    """A dataflow edge from one capability to another via a specific data type.

    Attributes:
        source: Source capability ID.
        target: Target capability ID.
        via: The data type that connects them (PRODUCES/CONSUMES key).
        source_name: Human-readable source name.
        target_name: Human-readable target name.
    """

    source: str
    target: str
    via: str
    source_name: str = ""
    target_name: str = ""


class CapabilityReasoner:
    """Reasoner for capability relationships.

    Currently supports:
    - PRODUCES/CONSUMES transitive inference (Phase 2 DoD).
    - Future: COMPOSES_INTO decomposition, pre/post condition checking.

    Usage:
        reasoner = CapabilityReasoner()
        deps = reasoner.infer_dataflow(capabilities, relations)
        # deps: list[DataflowDependency]
    """

    def infer_dataflow(
        self,
        capabilities: list[CapabilityEntity | dict],
        relations: list[tuple[str, str, str]],
    ) -> list[DataflowDependency]:
        """Derive dataflow dependencies from PRODUCES/CONSUMES relations.

        Algorithm:
        1. Build name lookup from capabilities.
        2. Index produces: {data_type → [capability_id]}
        3. For each consumes relation, match to a produces and create edge.

        Args:
            capabilities: CapabilityEntity objects or dicts with id/name keys.
            relations: List of (source_id, relation_type, data_type) tuples.
                       relation_type is "produces" or "consumes".

        Returns:
            Deduplicated list of DataflowDependency.
        """
        # Build name lookup: {cap_id → name}
        name_lookup: dict[str, str] = {}
        for cap in capabilities:
            if isinstance(cap, CapabilityEntity):
                name_lookup[cap.id] = cap.name
            elif isinstance(cap, dict):
                name_lookup[str(cap.get("id", ""))] = str(cap.get("name", ""))
            else:
                name_lookup[str(cap.id)] = str(cap.name)  # type: ignore[union-attr]

        # Index produces: {data_type → set[producer_id]}
        produces: dict[str, set[str]] = {}
        for source_id, rel_type, data_type in relations:
            if rel_type == "produces":
                produces.setdefault(data_type, set()).add(source_id)

        # Match consumers to producers
        deps: list[DataflowDependency] = []
        seen: set[tuple[str, str, str]] = set()
        for source_id, rel_type, data_type in relations:
            if rel_type != "consumes":
                continue
            if data_type not in produces:
                continue
            for producer_id in produces[data_type]:
                key = (producer_id, source_id, data_type)
                if key in seen:
                    continue
                seen.add(key)
                deps.append(
                    DataflowDependency(
                        source=producer_id,
                        target=source_id,
                        via=data_type,
                        source_name=name_lookup.get(producer_id, ""),
                        target_name=name_lookup.get(source_id, ""),
                    )
                )

        return deps
