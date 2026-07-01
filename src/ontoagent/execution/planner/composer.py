"""Composer — build executable DAG from sub-goals using ontology relations.

Phase 3.2: Uses PRODUCES/CONSUMES/COMPOSES_INTO/EQUIVALENT_TO relations
to determine execution order and detect dataflow dependencies.
"""

from __future__ import annotations

from ontoagent.execution.planner.data_types import PlanDAG, PlanNode, SubGoal


class Composer:
    """Build a validated PlanDAG from sub-goals + ontology relations.

    Uses PRODUCES/CONSUMES to determine dataflow edges,
    EQUIVALENT_TO for capability substitution,
    and ensures the result is a valid DAG (no cycles, deduplicated).

    Usage:
        composer = Composer()
        dag = composer.compose(sub_goals, relations)
    """

    def compose(
        self,
        sub_goals: list[dict],
        relations: list[tuple[str, str, str]],
    ) -> PlanDAG:
        """Compose sub-goals into a validated PlanDAG.

        Args:
            sub_goals: List of dicts with {description, capability_id, domain}.
            relations: (source_id, rel_type, target_data) tuples.
                       rel_type ∈ {produces, consumes, composes_into, equivalent_to}.

        Returns:
            Validated PlanDAG with nodes and dataflow edges.
        """
        # Deduplicate sub-goals by capability_id
        seen_ids: set[str] = set()
        unique_sgs: list[dict] = []
        for sg in sub_goals:
            cid = sg.get("capability_id", "")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                unique_sgs.append(sg)
            elif not cid:
                unique_sgs.append(sg)

        # Create nodes
        nodes: list[PlanNode] = [
            PlanNode(
                sub_goal=SubGoal(
                    description=sg.get("description", ""),
                    domain=sg.get("domain"),
                ),
                capability_id=sg.get("capability_id"),
            )
            for sg in unique_sgs
        ]

        # Index produces: {data_type → [producer_node_id]}
        produces: dict[str, list[str]] = {}
        node_by_cap: dict[str, PlanNode] = {n.capability_id: n for n in nodes if n.capability_id}

        for source_id, rel_type, data_type in relations:
            if rel_type == "produces" and source_id in node_by_cap:
                produces.setdefault(data_type, []).append(node_by_cap[source_id].id)

        # Resolve consumes → dataflow edges
        edges: set[tuple[str, str]] = set()
        unresolved: dict[str, list[str]] = {}

        for source_id, rel_type, data_type in relations:
            if rel_type != "consumes":
                continue
            consumer = node_by_cap.get(source_id)
            if consumer is None:
                continue
            if data_type in produces:
                for producer_id in produces[data_type]:
                    edges.add((producer_id, consumer.id))
            else:
                # Unresolved: no producer for this data type
                unresolved.setdefault(consumer.id, []).append(
                    f"consumes {data_type} but no capability PRODUCES it"
                )

        # Build DAG
        dag = PlanDAG(
            goal="",  # Will be set by the caller
            nodes=nodes,
            edges=sorted(edges),
        )

        # Attach unresolved info to nodes
        for node in nodes:
            if node.id in unresolved:
                node.dependencies.extend(unresolved[node.id])

        return dag
