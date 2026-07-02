修改 src/ontoagent/execution/dag_orchestrator.py:

1. NodeResult 新增字段: shape_results: list[dict] = field(default_factory=list)
2. ExecutionResult 新增字段: approval_nodes: list[str] = field(default_factory=list)
3. DAGOrchestrator.__init__ 新增参数: shape_evaluator: Any | None = None, 存为 self._shape_evaluator
4. 新增 _check_node_shapes(node, entity) 方法: 如果 shape_evaluator 为 None 或 entity 为 None, 返回(True, [], None)。否则调用 shape_evaluator.evaluate(entity, node.get("operations",[])), 检查 triggered 结果中是否有 Severity.BLOCK → 返回(False, shape_dicts, block_reason)。ESCALATE → 不阻断但记录。
5. execute() 方法: 在调用 cap_fn 之前, 调用 _check_node_shapes(nd, nd.get("entity"))。如果 BLOCK → NodeResult(status="blocked", shape_results=..., error=...) 并 failed=True。
6. 添加 import: from ontoagent.domain.shapes import Severity (在顶部)
7. nodes 字典现可接受 entity 和 operations 键

先写测试 RED → 实现 GREEN → 确保现有 10 个 DAG 测试仍通过 → 不要 commit。
