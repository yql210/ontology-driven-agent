"""Planner — decompose business goals into sub-goals and match capabilities.

Phase 3.1: Rule-based decomposition + CapabilityFinder integration.
Future: LLM-assisted decomposition (Phase 3.1 stretch).
"""

from __future__ import annotations

from ontoagent.execution.planner.data_types import PlanDAG, PlanNode, SubGoal

# Domain-specific sub-goal templates for common business goals
# (rule-based decomposition — LLM version will replace this)
_DOMAIN_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "订单": [
        ("创建订单并校验库存", "order"),
        ("处理用户支付", "payment"),
        ("生成电子发票", "payment"),
        ("物流发货", "logistics"),
        ("发送订单确认通知", "notification"),
    ],
    "支付": [
        ("验证支付方式", "payment"),
        ("执行扣款操作", "payment"),
        ("记录交易日志", "analytics"),
    ],
    "付款": [
        ("验证支付方式", "payment"),
        ("执行扣款操作", "payment"),
        ("记录交易日志", "analytics"),
    ],
    "发货": [
        ("查询库存状态", "inventory"),
        ("生成运单", "logistics"),
        ("通知用户发货进度", "notification"),
    ],
    "下单": [
        ("创建订单", "order"),
        ("校验库存", "inventory"),
        ("处理支付", "payment"),
    ],
    "用户": [
        ("注册新用户", "user"),
        ("验证用户身份", "user"),
        ("设置用户偏好", "user"),
    ],
    "商品": [
        ("搜索商品列表", "product"),
        ("获取商品详情", "product"),
        ("推荐相关商品", "product"),
    ],
    "退款": [
        ("验证退款条件", "order"),
        ("执行原路退款", "payment"),
        ("更新库存", "inventory"),
        ("通知用户退款结果", "notification"),
    ],
    "报表": [
        ("聚合业务数据", "analytics"),
        ("生成可视化图表", "analytics"),
        ("导出报表文件", "content"),
    ],
    "内容": [
        ("上传媒体资源", "content"),
        ("内容合规审核", "content"),
        ("发布到目标渠道", "content"),
    ],
    "履约": [
        ("创建订单并校验库存", "order"),
        ("处理用户支付", "payment"),
        ("生成电子发票", "payment"),
        ("物流发货", "logistics"),
        ("发送订单确认通知", "notification"),
    ],
}


class Planner:
    """Decompose business goals and find matching capabilities.

    Usage:
        finder = CapabilityFinder(chroma_store)
        planner = Planner(finder)
        plan = planner.plan("完成订单履约")
        # plan.nodes → list[PlanNode] with matched capabilities
    """

    def __init__(
        self,
        finder,
        default_top_k: int = 3,
        fallback_top_k: int = 10,
    ) -> None:
        """Initialize Planner.

        Args:
            finder: CapabilityFinder instance for semantic search.
            default_top_k: Default number of capability matches per sub-goal.
            fallback_top_k: Broader search when top_k matches are insufficient.
        """
        self._finder = finder
        self._default_top_k = default_top_k
        self._fallback_top_k = fallback_top_k

    def decompose(self, goal: str) -> list[SubGoal]:
        """Decompose a business goal into ordered sub-goals.

        Uses rule-based template matching; will be replaced by LLM
        decomposition in a future iteration.

        Args:
            goal: Natural language business goal.

        Returns:
            Ordered list of SubGoal objects.
        """
        # Find ALL matching template keywords, pick longest; tie-break by later occurrence
        matches = [(len(k), goal.find(k), k) for k in _DOMAIN_TEMPLATES if k in goal]
        if matches:
            best_keyword = max(matches, key=lambda x: (x[0], x[1]))[2]
            return [
                SubGoal(description=desc, domain=dom)
                for desc, dom in _DOMAIN_TEMPLATES[best_keyword]
            ]

        # Fallback: single sub-goal matching the goal itself
        return [SubGoal(description=goal)]

    def plan(self, goal: str, top_k: int | None = None) -> PlanDAG:
        """Full planning: decompose goal → match capabilities → build DAG.

        Implements V5 Phase 3.3 three-level fallback:
        1. Default top_k search (expand search space internally)
        2. Unresolved nodes returned as-is for caller to handle
           (caller can prompt user for clarification)
        3. Partial DAG returned with unresolved_dependencies flagged

        Args:
            goal: Business goal.
            top_k: Number of capability matches per sub-goal.

        Returns:
            PlanDAG with nodes and edges. Check dag.is_complete for status.
        """
        if top_k is None:
            top_k = self._default_top_k

        sub_goals = self.decompose(goal)
        nodes: list[PlanNode] = []
        node_order: dict[str, int] = {}

        for idx, sg in enumerate(sub_goals):
            # Try default top_k first
            matches = self._finder.find(sg.description, top_k=top_k, domain=sg.domain)

            # If no match and we have fallback, try broader search
            if not matches and self._fallback_top_k > top_k:
                matches = self._finder.find(sg.description, top_k=self._fallback_top_k, domain=sg.domain)

            cap_id = matches[0].id if matches else None
            dependencies: list[str] = []

            # Link to previous node in the sequence (linear dependency)
            if idx > 0 and nodes:
                dependencies.append(nodes[-1].id)

            node = PlanNode(sub_goal=sg, capability_id=cap_id, dependencies=dependencies)
            nodes.append(node)
            node_order[node.id] = idx

        # Build edges: sequential (node_i → node_{i+1}) for now
        # Will be enhanced by Composer using PRODUCES/CONSUMES
        edges: list[tuple[str, str]] = []
        for i in range(len(nodes) - 1):
            if nodes[i].capability_id and nodes[i + 1].capability_id:
                edges.append((nodes[i].id, nodes[i + 1].id))

        return PlanDAG(goal=goal, nodes=nodes, edges=edges)
