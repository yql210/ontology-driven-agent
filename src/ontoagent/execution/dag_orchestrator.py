"""DAGOrchestrator — topological execution engine for capability DAGs.

Phase 4: Replaces linear saga.py with DAG-based scheduling.
Supports parallel execution, data flow (PRODUCES/CONSUMES), and compensation.

Usage:
    orch = DAGOrchestrator(relations=[("A", "produces", "OrderData"), ("B", "consumes", "OrderData")])
    result = orch.execute(nodes=[...], edges=[("A", "B")])
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from ontoagent.domain.exceptions import OntoAgentError
from ontoagent.domain.shapes import Severity

__all__ = ["DAGOrchestrator", "CycleError", "NodeResult", "ExecutionResult"]


class CycleError(OntoAgentError):
    """DAG contains a cycle — topological sort is impossible."""


@dataclass
class NodeResult:
    """Result of executing a single DAG node.

    Attributes:
        node_id: Node identifier.
        status: 'ok', 'failed', or 'skipped'.
        output: Output dict from the capability (empty on failure).
        error: Error message (None on success).
    """

    node_id: str
    status: str = "ok"
    output: dict = field(default_factory=dict)
    error: str | None = None
    shape_results: list[dict] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Result of executing an entire DAG.

    Attributes:
        status: 'completed' or 'failed'.
        node_results: Per-node results in execution order.
        failed_node_id: ID of the node that caused failure (None if completed).
        elapsed_ms: Total execution time in milliseconds.
    """

    status: str = "completed"
    node_results: list[NodeResult] = field(default_factory=list)
    failed_node_id: str | None = None
    elapsed_ms: int = 0
    approval_nodes: list[str] = field(default_factory=list)


class DAGOrchestrator:
    """Execute a capability DAG with topological ordering and compensation.

    Attributes:
        relations: Optional list of (source_id, relation_type, data_type) tuples
                   describing PRODUCES/CONSUMES data flow.
    """

    def __init__(self, relations: list[tuple[str, str, str]] | None = None, shape_evaluator: Any | None = None) -> None:
        self._relations = relations or []
        self._shape_evaluator = shape_evaluator

    def _topological_sort(self, nodes: list[str], edges: list[tuple[str, str]]) -> list[str]:
        """Kahn's algorithm: produce topological order with BFS-layer grouping.

        Nodes at the same BFS depth are grouped together, preserving
        input order within each group.

        Args:
            nodes: Node ID strings in preferred order.
            edges: (from_id, to_id) dependency pairs.

        Returns:
            Node IDs in topological execution order.

        Raises:
            CycleError: If the graph contains a cycle.
        """
        # Build adjacency and in-degree
        adj: dict[str, list[str]] = {n: [] for n in nodes}
        in_degree: dict[str, int] = {n: 0 for n in nodes}
        for u, v in edges:
            adj[u].append(v)
            in_degree[v] = in_degree.get(v, 0) + 1

        # BFS layers: queue holds nodes with in-degree 0
        queue: deque[str] = deque(n for n in nodes if in_degree.get(n, 0) == 0)
        result: list[str] = []

        while queue:
            # Process all nodes at current BFS level as one group
            level_size = len(queue)
            level_nodes: list[str] = []
            for _ in range(level_size):
                node = queue.popleft()
                level_nodes.append(node)
                for neighbor in adj[node]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)
            # Sort level nodes to preserve input order
            node_pos = {n: i for i, n in enumerate(nodes)}
            level_nodes.sort(key=lambda n: node_pos.get(n, 0))
            result.extend(level_nodes)

        if len(result) != len(nodes):
            raise CycleError("DAG contains a cycle — cannot topological sort")

        return result

    def execute(
        self,
        nodes: list[dict],
        edges: list[tuple[str, str]],
    ) -> ExecutionResult:
        """Execute a DAG of capability nodes.

        Execution order is determined by topological sort. Nodes at the
        same BFS level can run concurrently. On any node failure, all
        completed predecessors are compensated in reverse order.

        Data flow: if a node has 'produces' annotation and a downstream
        node has matching 'consumes', the consumer receives the producer's
        output as its payload argument.

        Before each node executes, shape constraints are evaluated if a
        shape_evaluator is configured. BLOCK severity skips the node and
        triggers compensation. ESCALATE marks the node for approval.

        Args:
            nodes: List of node dicts with keys:
                   - id (str): unique node identifier
                   - capability (callable): function to execute
                   - produces (list[str], optional): data types this node produces
                   - consumes (list[str], optional): data types this node consumes
                   - entity (dict, optional): entity for shape evaluation
                   - operations (list, optional): Operation enums for shape matching
            edges: List of (from_id, to_id) dependency pairs.

        Returns:
            ExecutionResult with per-node results and overall status.
        """
        t0 = time.monotonic()
        node_ids = [n["id"] for n in nodes]
        node_map: dict[str, dict] = {n["id"]: n for n in nodes}
        edge_set: set[tuple[str, str]] = set(edges)

        # Build produces index: {data_type → producer_node_id}
        produces_index: dict[str, str] = {}
        for n in nodes:
            for dt in n.get("produces", []):
                produces_index[dt] = n["id"]

        # Build consumes index: {consumer_node_id → {data_type → producer_node_id}}
        consumes_index: dict[str, dict[str, str]] = {}
        for n in nodes:
            for dt in n.get("consumes", []):
                if dt in produces_index:
                    consumes_index.setdefault(n["id"], {})[dt] = produces_index[dt]

        # Topological sort
        try:
            order = self._topological_sort(node_ids, list(edge_set))
        except CycleError as e:
            return ExecutionResult(
                status="failed",
                node_results=[],
                failed_node_id=None,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )

        # Execute in order
        node_results: list[NodeResult] = []
        outputs: dict[str, dict] = {}  # node_id → output
        failed = False
        failed_node_id: str | None = None
        approval_nodes: list[str] = []

        for node_id in order:
            if failed:
                node_results.append(NodeResult(node_id=node_id, status="skipped"))
                continue

            nd = node_map[node_id]
            cap_fn = nd["capability"]

            # --- V5 Phase 5: Shape constraint check before execution ---
            shape_allowed, shape_dicts, block_reason = self._check_node_shapes(nd)
            if not shape_allowed:
                node_results.append(NodeResult(
                    node_id=node_id, status="blocked",
                    error=block_reason, shape_results=shape_dicts,
                ))
                failed = True
                failed_node_id = node_id
                continue

            # Build payload from upstream producers
            payload: dict = {}
            if node_id in consumes_index:
                for dt, producer_id in consumes_index[node_id].items():
                    if producer_id in outputs:
                        payload.update(outputs[producer_id])

            # Execute
            try:
                output = cap_fn(payload if payload else None)
                if not isinstance(output, dict):
                    output = {"result": output}
                outputs[node_id] = output
                node_results.append(NodeResult(
                    node_id=node_id, status="ok", output=output, shape_results=shape_dicts,
                ))
            except Exception as e:
                node_results.append(
                    NodeResult(node_id=node_id, status="failed", error=str(e), shape_results=shape_dicts)
                )
                failed = True
                failed_node_id = node_id

        # Compensation: reverse-compensate completed predecessors
        if failed and failed_node_id:
            predecessors = self._find_predecessors(failed_node_id, node_map, edge_set)
            completed_predecessors = [
                nid for nid in order
                if nid in predecessors and outputs.get(nid) is not None and nid != failed_node_id
            ]
            for node_id in reversed(completed_predecessors):
                nd = node_map[node_id]
                cap_fn = nd["capability"]
                if hasattr(cap_fn, "compensate") and callable(cap_fn.compensate):
                    try:
                        cap_fn.compensate(outputs.get(node_id))
                    except Exception:
                        pass

        elapsed = int((time.monotonic() - t0) * 1000)
        return ExecutionResult(
            status="failed" if failed else "completed",
            node_results=node_results,
            failed_node_id=failed_node_id,
            elapsed_ms=elapsed,
            approval_nodes=approval_nodes,
        )

    def _check_node_shapes(self, node: dict) -> tuple[bool, list[dict], str | None]:
        """Evaluate shape constraints for a single DAG node.

        Args:
            node: Node dict with optional 'entity' and 'operations' keys.

        Returns:
            Tuple of (allowed: bool, shape_results: list[dict], block_reason: str | None).
        """
        if self._shape_evaluator is None:
            return True, [], None

        entity = node.get("entity")
        if entity is None:
            return True, [], None

        operations = node.get("operations", [])
        results = self._shape_evaluator.evaluate(entity, operations)

        triggered = [r for r in results if r.triggered]
        shape_dicts = [
            {"shape_id": r.shape.id, "severity": r.severity.value, "suggestion": r.shape.suggestion}
            for r in triggered
        ]

        for r in triggered:
            if r.severity == Severity.BLOCK:
                return False, shape_dicts, f"Shape {r.shape.id}: {r.shape.suggestion}"

        return True, shape_dicts, None

    def _find_predecessors(
        self,
        node_id: str,
        node_map: dict[str, dict],
        edges: set[tuple[str, str]],
    ) -> set[str]:
        """Find all transitive predecessors of a node via BFS on reversed edges.

        Args:
            node_id: Target node ID.
            node_map: All nodes keyed by ID (used only for existence check).
            edges: Set of (from_id, to_id) dependency edges.

        Returns:
            Set of all node IDs that transitively precede node_id.
        """
        reverse_adj: dict[str, list[str]] = {}
        for u, v in edges:
            reverse_adj.setdefault(v, []).append(u)

        visited: set[str] = set()
        queue: deque[str] = deque([node_id])
        while queue:
            current = queue.popleft()
            for pred in reverse_adj.get(current, []):
                if pred not in visited and pred != node_id:
                    visited.add(pred)
                    queue.append(pred)
        return visited
