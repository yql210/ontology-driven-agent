"""LangChain Tool 封装 — Agent 可调用的工具"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

if TYPE_CHECKING:
    from ontoagent.execution.action_executor import ActionExecutor

import ontoagent.execution.functions.general  # noqa: F401
from ontoagent.agent._helpers import (
    get_aligner,
    get_chroma,
    get_clustering,
    get_impact_propagator,
    get_neo4j,
)
from ontoagent.pipeline.change_detector import ChangeType


@tool
def semantic_search(query: str, top_k: int = 5) -> str:
    """语义搜索：在代码库中搜索与 query 相关的代码片段。

    Args:
        query: 搜索关键词或自然语言描述
        top_k: 返回结果数量，建议 5-10

    Returns:
        匹配的代码片段列表（JSON），包含文件路径、函数名、相似度分数
    """
    try:
        chroma = get_chroma()
        results = chroma.search(query_text=query, n_results=top_k)
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps(
            {
                "error": f"语义搜索失败: {e!s}",
                "suggestion": "请使用 graph_query 执行 Cypher 查询替代，例如: MATCH (n:CodeEntity) WHERE n.name CONTAINS '关键词' RETURN n.name, n.file_path LIMIT 10",
            },
            ensure_ascii=False,
        )


@tool
def graph_query(cypher: str) -> str:
    """执行 Cypher 图查询，查询代码实体之间的关系。

    常用查询模式：
    - 函数调用关系：MATCH (a)-[:CALLS]->(b) WHERE a.name CONTAINS 'xxx' RETURN a.name, b.name
    - 模块依赖：MATCH (m:ModuleEntity)-[:CONTAINS]->(c) RETURN m.name, c.name
    - 概念关联：MATCH (c:ConceptEntity)<-[:DESCRIBES]-(e) RETURN c.name, e.name

    Args:
        cypher: Neo4j Cypher 查询语句

    Returns:
        查询结果的 JSON 格式字符串
    """
    from ontoagent.agent.tool_gateway import validate_graph_query

    allowed, reason = validate_graph_query(cypher)
    if not allowed:
        return json.dumps({"error": reason}, ensure_ascii=False)

    neo4j = get_neo4j()
    try:
        results = neo4j.query(cypher)
        if not results:
            return json.dumps(
                {
                    "info": "查询返回空结果。可能原因：1)查询的实体类型不存在 2)关键词不匹配 3)关系类型不匹配。请尝试修改查询条件或换用其他工具。",
                    "results": [],
                },
                ensure_ascii=False,
            )
        if len(results) > 100:
            return json.dumps(
                {
                    "warning": f"结果过多({len(results)}条)，已截断为前100条。建议加 LIMIT 子句缩小范围。",
                    "results": results[:100],
                },
                ensure_ascii=False,
                indent=2,
            )
        return json.dumps(results, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Cypher 查询错误: {e!s}\n请检查语法是否正确。"


@tool
def impact_analysis(entity_name: str, depth: int = 3) -> str:
    """影响分析：分析代码变更对系统的影响范围。

    基于图结构传播分析，找出受变更影响的所有实体（函数、类、模块等）。

    Args:
        entity_name: 要分析的实体名称（函数名、类名等）
        depth: 传播深度，默认 3 层

    Returns:
        影响分析结果（JSON），包含源实体、受影响实体数量及详细列表
    """
    try:
        neo4j = get_neo4j()
        propagator = get_impact_propagator()

        # 先通过 name 查找 id
        cypher = "MATCH (n {name: $name}) RETURN n.id AS id LIMIT 1"
        result = neo4j.query(cypher, {"name": entity_name})

        if not result:
            # 模糊匹配
            cypher = "MATCH (n) WHERE n.name CONTAINS $name RETURN n.id AS id, n.name AS name LIMIT 5"
            fuzzy_results = neo4j.query(cypher, {"name": entity_name})
            if fuzzy_results:
                return json.dumps(
                    {
                        "error": f"未找到精确匹配的实体 '{entity_name}'",
                        "suggestions": fuzzy_results,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            return json.dumps({"error": f"未找到包含 '{entity_name}' 的实体"}, ensure_ascii=False)

        entity_id = result[0]["id"]

        # 调用 ImpactPropagator
        impacts = propagator.compute_impact([entity_id], ChangeType.BODY)

        return json.dumps(
            {
                "source": entity_name,
                "source_id": entity_id,
                "total_count": len(impacts),
                "impacted_entities": [i.to_dict() for i in impacts[:50]],
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return json.dumps({"error": f"影响分析失败: {e!s}", "entity_id": entity_name}, ensure_ascii=False)


@tool
def get_context(entity_name: str) -> str:
    """获取实体上下文：节点详情、关系和相似实体。

    返回实体的完整上下文信息，包括属性、与其他实体的关系、以及语义相似的实体。

    Args:
        entity_name: 实体名称

    Returns:
        上下文信息（JSON），包含节点属性、双向关系、相似实体
    """
    neo4j = get_neo4j()
    chroma = get_chroma()

    # 查找实体 id
    cypher = "MATCH (n {name: $name}) RETURN n.id AS id LIMIT 1"
    result = neo4j.query(cypher, {"name": entity_name})

    if not result:
        return json.dumps({"error": f"未找到实体 '{entity_name}'"}, ensure_ascii=False)

    entity_id = result[0]["id"]

    # 获取节点详情
    node = neo4j.get_node(entity_id)
    if node is None:
        return json.dumps({"error": f"无法获取节点 {entity_id} 的详情"}, ensure_ascii=False)

    # 获取双向关系
    outgoing = neo4j.get_relations(source_id=entity_id)
    incoming = neo4j.get_relations(target_id=entity_id)
    relations = outgoing + incoming

    # 获取相似实体（降级处理）
    similar_entities = []
    try:
        similar_results = chroma.search(query_text=node.get("name", entity_name), n_results=5)
        if isinstance(similar_results, list):
            similar_entities = similar_results
        elif isinstance(similar_results, dict):
            similar_entities = similar_results.get("matches", [])
        else:
            similar_entities = []
    except Exception:
        similar_entities = []

    return json.dumps(
        {
            "node": node,
            "relations": relations,
            "similar_entities": similar_entities,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@tool
def list_concepts() -> str:
    """列出所有已注册的概念实体。

    返回知识图谱中定义的所有业务概念、设计模式、API契约等概念实体。

    Returns:
        概念列表（JSON），每项包含 name, id, aliases, entity_type
    """
    aligner = get_aligner()
    concepts = aligner.list_concepts()
    if not concepts:
        return json.dumps(
            {
                "info": "当前知识图谱中没有概念实体（ConceptEntity）。可能构建时跳过了语义提取阶段。请使用 graph_query 查询 CodeEntity 或其他实体类型。",
                "count": 0,
            },
            ensure_ascii=False,
        )
    return json.dumps(concepts, ensure_ascii=False, indent=2)


@tool
def get_module_tree() -> str:
    """获取模块层次结构树。

    返回基于聚类分析得出的代码模块划分，展示功能模块及其包含的实体。

    Returns:
        模块树（JSON），格式: {module_name: {entities, cohesion, entity_count}}
    """
    try:
        clustering = get_clustering()
        tree = clustering.get_module_tree()

        if not tree:
            return json.dumps(
                {
                    "info": "当前知识图谱中没有模块聚类数据。可能构建时跳过了聚类阶段。请使用 graph_query 查询 CodeEntity。",
                    "count": 0,
                },
                ensure_ascii=False,
            )

        neo4j = get_neo4j()
        enriched_tree = {}
        for module_name, info in list(tree.items())[:20]:
            entity_names = []
            for eid in info.get("entities", [])[:5]:
                try:
                    node = neo4j.get_node(eid)
                    if node and node.get("name"):
                        entity_names.append(node["name"])
                except Exception:
                    pass
            enriched_tree[module_name] = {
                "entity_count": info.get("entity_count", 0),
                "cohesion": round(info.get("cohesion", 0.0), 3),
                "entity_sample": entity_names,
            }

        if not enriched_tree:
            return json.dumps(
                {"info": "模块聚类数据为空。请使用 graph_query 查询 CodeEntity。", "count": 0},
                ensure_ascii=False,
            )

        return json.dumps(enriched_tree, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps(
            {"error": f"获取模块树失败: {e!s}", "suggestion": "请使用 graph_query 查询 CodeEntity 替代"},
            ensure_ascii=False,
        )


@tool
def detect_changes(since: str = "HEAD~1", *, repo_path: str = ".") -> str:
    """检测 Git 仓库中的代码变更。

    通过 git diff 命令获取指定范围内的文件变更列表。

    Args:
        since: Git 引用，如 HEAD~1, abc123，默认 HEAD~1
        repo_path: Git 仓库路径，默认当前目录

    Returns:
        变更列表（JSON），包含 since, total_changes, changed_files
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", since],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        return json.dumps(
            {
                "error": f"git diff 执行失败: {e}",
                "stderr": e.stderr,
            },
            ensure_ascii=False,
        )

    changed_files = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0]
            file_path = parts[1]
            changed_files.append({"status": status, "file": file_path})
        elif len(parts) == 1:
            # 可能是没有状态码的情况
            changed_files.append({"status": "unknown", "file": parts[0]})

    return json.dumps(
        {
            "since": since,
            "total_changes": len(changed_files),
            "changed_files": changed_files,
        },
        ensure_ascii=False,
        indent=2,
    )


@tool
def export_graph(limit: int = 100) -> str:
    """导出图结构数据。

    返回图数据库中的节点和边，用于可视化或分析。

    Args:
        limit: 导出数量限制，默认 100

    Returns:
        图数据（JSON），包含 nodes, edges, node_count, edge_count
    """
    try:
        neo4j = get_neo4j()

        # 查询节点
        nodes_cypher = "MATCH (n) RETURN n.id AS id, n.name AS name, labels(n) AS labels LIMIT $limit"
        nodes = neo4j.query(nodes_cypher, {"limit": limit})

        # 查询边
        edges_cypher = """
            MATCH (a)-[r]->(b)
            RETURN a.id AS source, b.id AS target, type(r) AS type, properties(r) AS properties
            LIMIT $limit
        """
        edges = neo4j.query(edges_cypher, {"limit": limit})

        return json.dumps(
            {
                "nodes": nodes,
                "edges": edges,
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": f"导出图谱失败: {e!s}"}, ensure_ascii=False)


@tool
def express_intent(
    intent_type: str = "",
    target: str = "",
    params: dict | None = None,
    approval_id: str = "",
    approved: bool = False,
) -> str:
    """执行操作意图，或在需要审批时返回审批单。

    两种调用模式：
    1. 正常模式：express_intent(intent_type="refactor", target="func_name")
       → 自动检查约束和审批策略 → 返回执行结果或审批单

    2. 审批回执模式：express_intent(approval_id="abc123", approved=true)
       → 验证令牌 → 执行之前挂起的操作

    Args:
        intent_type: 操作类型（正常模式必填）
        target: 目标实体名称
        params: 额外参数
        approval_id: 审批令牌（审批回执模式）
        approved: 是否批准（审批回执模式）

    Returns:
        JSON 字符串。正常执行完成返回 {"status": "completed", ...}
        需要审批返回 {"status": "approval_required", "approval_id": "...", "checks": [...]}
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        neo4j = get_neo4j()
        executor = _get_action_executor(neo4j)
        approval_gate = _get_approval_gate()

        skip_approval = False

        # --- 审批回执模式 ---
        if approval_id:
            if not approval_gate:
                return json.dumps({"status": "error", "error": "审批系统未启用"}, ensure_ascii=False)

            ctx = approval_gate.resolve(approval_id, approved)

            # 拒绝且令牌有效（ctx 非空）→ 返回 rejected
            if ctx is not None and not approved:
                return json.dumps(
                    {"status": "rejected", "message": f"操作 '{ctx.intent_type}' 已被拒绝"},
                    ensure_ascii=False,
                )

            # 令牌无效/过期/已使用
            if ctx is None:
                return json.dumps(
                    {"status": "error", "error": "审批令牌无效、已过期或已被使用"},
                    ensure_ascii=False,
                )

            # Resume execution with the stored context
            params = ctx.params
            intent_type = ctx.intent_type
            target = ctx.target
            skip_approval = True  # 跳过后续审批检查，直接执行

        # --- 正常模式 ---
        if not intent_type or not target:
            return json.dumps({"status": "error", "error": "intent_type 和 target 不能为空"}, ensure_ascii=False)

        # Resolve entity
        entity = executor._resolve_entity(target)
        if entity is None:
            return json.dumps({"status": "error", "error": f"未找到实体 '{target}'"}, ensure_ascii=False)

        # Get action config
        config = executor.intent_map.get(intent_type)
        if config is None:
            return json.dumps({"status": "error", "error": f"未知操作类型: {intent_type}"}, ensure_ascii=False)

        # --- Approval gate check ---
        if not skip_approval and approval_gate and executor._shape_registry is not None:
            from ontoagent.domain.approval import ApprovalContext, DecisionLevel

            approval_ctx = ApprovalContext(
                intent_type=intent_type,
                target=target,
                params=params or {},
                entity=entity,
                guard_checks=[],  # V5: shapes replace guard checks
                session_id="",
            )

            decision = approval_gate.check(
                approval_ctx,
                config=config,
                graph_store=neo4j,
                executor=executor,
            )

            if decision.level == DecisionLevel.DENIED:
                return json.dumps(
                    {
                        "status": "blocked",
                        "checks": [
                            {"policy": r.policy_name, "level": r.level.value, "reason": r.reason}
                            for r in decision.results
                        ],
                    },
                    ensure_ascii=False,
                )

            if decision.level == DecisionLevel.PENDING:
                return json.dumps(
                    {
                        "status": "approval_required",
                        "approval_id": decision.token,
                        "level": "action",
                        "checks": [],
                        "policies": [
                            {"policy": r.policy_name, "level": r.level.value, "reason": r.reason}
                            for r in decision.results
                        ],
                    },
                    ensure_ascii=False,
                )

        # --- Execute ---
        bypass_fn_approval = skip_approval  # If approval was already granted, bypass function-level approval too
        result = executor.execute(
            intent_type,
            {**(params or {}), "target": target},
            bypass_guard=skip_approval,
            bypass_function_approval=bypass_fn_approval,
        )
        result_dict = result.to_dict()

        # Check for function-level approval in results
        for r in result_dict.get("results", []):
            data = r.get("data", {})
            if data.get("approval_required"):
                return json.dumps(
                    {
                        "status": "approval_required",
                        "level": "function",
                        "approval_id": data.get("approval_token", ""),
                        "function_name": data.get("function_name", ""),
                        "checks": [],
                        "policies": [],
                    },
                    ensure_ascii=False,
                )

        return json.dumps(result_dict, ensure_ascii=False, default=str)

    except Exception as e:
        logger.exception("express_intent failed")
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@tool
def check_operation(intent_type: str, target: str) -> str:
    """预检查操作是否可以通过本体约束。不执行实际操作，只返回约束检查结果。

    在调用 express_intent 之前使用此工具，了解操作是否会被拦截。

    Args:
        intent_type: 意图类型（如 refactor, compliance_check）
        target: 目标实体名称

    Returns:
        JSON 字符串，包含 {pass: bool, checks: [...], block_reason: str|null}
        每个 check 包含 {shape_id: str, severity: ALLOW|WARN|BLOCK, reason: str}
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        graph_store = get_neo4j()
        executor = _get_action_executor(graph_store)

        if executor._shape_registry is None:
            return json.dumps(
                {"pass": True, "checks": [], "note": "约束系统未启用"},
                ensure_ascii=False,
            )

        # Resolve entity
        entity = executor._resolve_entity(target)
        if entity is None:
            return json.dumps(
                {"pass": False, "checks": [], "block_reason": f"未找到实体 '{target}'"},
                ensure_ascii=False,
            )

        # Get action config for this intent type
        config = executor.intent_map.get(intent_type)
        if config is None:
            return json.dumps(
                {"pass": False, "checks": [], "block_reason": f"未知意图类型: {intent_type}"},
                ensure_ascii=False,
            )

        # V5: Use ShapeEvaluator instead of guard pipeline
        block_reason, warnings = executor._check_with_shapes(entity, config)

        if block_reason:
            return json.dumps(
                {
                    "pass": False,
                    "checks": warnings,
                    "block_reason": block_reason,
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {"pass": True, "checks": warnings if warnings else []},
            ensure_ascii=False,
        )

    except Exception as e:
        logger.exception("check_operation failed")
        return json.dumps(
            {"pass": False, "checks": [], "block_reason": f"内部错误: {e!s}"},
            ensure_ascii=False,
        )


@tool
def explore_ontology() -> str:
    """浏览本体约束：返回所有已启用 Shape 的摘要列表。

    用于在执行操作前了解系统中有哪些约束规则。每条 Shape 返回 id/name/description。
    需要查看完整字段时使用 explain_constraint(shape_id)。

    Returns:
        JSON 字符串，格式: {count: int, shapes: [{id, name, description}, ...]}
        ShapeRegistry 未启用时返回 {info: ..., count: 0}
    """
    registry = _get_shape_registry()
    if registry is None:
        return json.dumps(
            {
                "info": "ShapeRegistry 未启用（feature flag 关闭 / shapes.yaml 缺失）。约束由旧 Guard Pipeline 强制执行。",
                "count": 0,
                "shapes": [],
            },
            ensure_ascii=False,
        )

    summaries = [{"id": s.id, "name": s.name, "description": s.description} for s in registry.all_shapes() if s.enabled]
    return json.dumps(
        {"count": len(summaries), "shapes": summaries},
        ensure_ascii=False,
        indent=2,
    )


@tool
def explain_constraint(shape_id: str) -> str:
    """查看单条约束 Shape 的完整信息。

    在 explore_ontology() 看到 Shape 摘要后，用此工具获取 target/path/constraint/
    severity/suggestion 等完整字段，理解为何某操作被拦截。

    Args:
        shape_id: Shape 全局唯一 id（来自 explore_ontology 返回的 id 字段）

    Returns:
        JSON 字符串，包含完整 Shape 信息。找不到时返回 {error: ...}
    """
    registry = _get_shape_registry()
    if registry is None:
        return json.dumps(
            {"error": "ShapeRegistry 未启用，无法解释 Shape 详情"},
            ensure_ascii=False,
        )

    shape = None
    for s in registry.all_shapes():
        if s.id == shape_id:
            shape = s
            break

    if shape is None:
        return json.dumps(
            {"error": f"未找到 Shape '{shape_id}'。请先调用 explore_ontology 查看可用 id。"},
            ensure_ascii=False,
        )

    target = shape.target
    constraint = shape.constraint
    path = shape.path
    return json.dumps(
        {
            "id": shape.id,
            "name": shape.name,
            "description": shape.description,
            "kind": shape.kind.value,
            "enabled": shape.enabled,
            "version": shape.version,
            "priority": shape.priority,
            "tags": list(shape.tags),
            "severity": shape.severity.value,
            "suggestion": shape.suggestion,
            "max_depth": shape.max_depth,
            "target": {
                "entry_type": target.entry_type,
                "operation": target.operation.value,
                "ontology_ref": target.ontology_ref,
                "field_filter": target.field_filter,
            },
            "path": {
                "raw": path.raw,
                "target_label": path.target_label,
                "max_depth": path.max_depth,
            },
            "constraint": {
                "field": constraint.field,
                "operator": constraint.operator,
                "value": constraint.value,
                "unless_field": constraint.unless_field,
                "unless_value": constraint.unless_value,
            },
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@tool
def suggest_alternatives(intent_type: str, target: str) -> str:
    """启发式推荐：找出与给定 intent 绑定到同一资源类型的其他 intent。

    当某个操作被约束拦截时，可用此工具找到该实体上可执行的其他操作。
    例如：refactor 被拦截时，可建议 document / analyze_impact 等同 bind_to 的 intent。

    Args:
        intent_type: 当前被拦截或关心的意图类型（如 refactor）
        target: 目标实体名称（保留参数，便于未来按实体精化推荐）

    Returns:
        JSON 字符串，格式:
        {source_intent: str, entry_type: str, count: int,
         alternatives: [{intent_type, trigger_hint, bind_to}, ...]}

        注意：此处的 resource_type（见下方 line ~749）属于 intent_router 层，
        与 ShapeTarget.entry_type 是不同层级的概念。为保持意图层 API 稳定，此处不改名。
    """
    intent_map = _get_intent_map()
    source = intent_map.get(intent_type)
    if source is None:
        return json.dumps(
            {"error": f"未知 intent_type: {intent_type!r}"},
            ensure_ascii=False,
        )

    bind_to = source.bind_to or ""
    alternatives = [
        {
            "intent_type": cfg.intent_type,
            "trigger_hint": cfg.trigger_hint,
            "bind_to": cfg.bind_to,
        }
        for cfg in intent_map.values()
        if cfg.intent_type != intent_type and (cfg.bind_to or "") == bind_to
    ]

    return json.dumps(
        {
            "source_intent": intent_type,
            "target": target,
            "resource_type": bind_to,
            "count": len(alternatives),
            "alternatives": alternatives,
        },
        ensure_ascii=False,
        indent=2,
    )


_action_executor: ActionExecutor | None = None
_function_runner: Any | None = None
_APPROVAL_GATE: object | None = None
_shape_registry: Any | None = None
_intent_map: dict[str, Any] | None = None


def _get_intent_map() -> dict[str, Any]:
    """获取全局 intent_type → ActionConfig 映射（lazy init）。

    从 ``pipeline/ontology_actions.yaml`` 加载，供 suggest_alternatives 等工具
    做资源类型启发式查询。"""
    global _intent_map
    if _intent_map is not None:
        return _intent_map

    from pathlib import Path

    from ontoagent.execution.intent_router import build_intent_map

    yaml_path = Path(__file__).parent.parent / "pipeline" / "ontology_actions.yaml"
    if not yaml_path.exists():
        _intent_map = {}
        return _intent_map

    try:
        _intent_map = build_intent_map(yaml_path)
    except Exception:  # 加载失败不阻塞其他工具
        _intent_map = {}
    return _intent_map


def _get_approval_gate() -> object:
    """获取或初始化 ApprovalGate 单例。

    ShapeBasedGuardPolicy 通过 executor._check_with_shapes() 实现约束检查，
    由 _get_action_executor 在创建 guard pipeline 后完成。

    审批策略配置从 config/approval_policy.yaml 读取。
    """
    global _APPROVAL_GATE
    if _APPROVAL_GATE is None:
        from pathlib import Path

        import yaml

        from ontoagent.execution.constraints import (
            ActionApprovalPolicy,
            ApprovalGate,
            FunctionDangerPolicy,
            ShapeBasedGuardPolicy,
        )
        from ontoagent.execution.functions.registry import _meta as function_meta

        # Load config from YAML
        config_path = Path(__file__).parent.parent / "config" / "approval_policy.yaml"
        config = {}
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}

        # ShapeBasedGuardPolicy config
        guard_config = config.get("guard_result", {})
        on_block = guard_config.get("on_block", "require_approval")
        on_warn = guard_config.get("on_warn", "require_approval")

        # FunctionDangerPolicy config
        fd_config = config.get("function_danger", {})
        auto_approve = set(fd_config.get("auto_approve", ["read"]))
        require_approval = set(fd_config.get("require_approval", ["read_sensitive", "write", "admin"]))

        # Build policies based on config
        policies = []
        enabled_policies = config.get("policies", ["guard_result", "action_approval", "function_danger"])
        for name in enabled_policies:
            if name == "guard_result":
                policies.append(
                    ShapeBasedGuardPolicy(
                        on_block=on_block,
                        on_warn=on_warn,
                    )
                )
            elif name == "action_approval":
                policies.append(ActionApprovalPolicy())
            elif name == "function_danger":
                fp = FunctionDangerPolicy(function_meta)
                fp.auto_approve_levels = auto_approve
                fp.require_approval_levels = require_approval
                policies.append(fp)

        _APPROVAL_GATE = ApprovalGate(policies)

        # Apply token configuration from YAML
        token_config = config.get("token", {})
        if token_config:
            _APPROVAL_GATE._ttl = token_config.get("ttl", 600)
            _APPROVAL_GATE._max_pending = token_config.get("max_pending", 10)

    return _APPROVAL_GATE


def _get_function_runner() -> Any:
    """获取全局 FunctionRunner 单例（lazy init）。"""
    global _function_runner
    if _function_runner is None:
        from ontoagent.execution.function_runner import FunctionRunner

        _function_runner = FunctionRunner(
            graph_store=None,  # will be injected by ActionExecutor
            approval_gate=_get_approval_gate(),
        )
    return _function_runner


def _get_shape_registry() -> Any:
    """获取全局 ShapeRegistry 单例（lazy init）。

    V4 Phase 1: 从 ``pipeline/shapes.yaml`` 加载约束 Shape。失败时返回 ``None``，
    调用方需 fallback 到旧的 Guard Pipeline。

    Feature flag: 环境变量 ``ONTOAGENT_ENABLE_SHAPES``（默认 ``"true"``）。置为
    ``"false"`` / ``"0"`` / ``"off"`` / ``"no"`` 可禁用 ShapeRegistry，仅用旧 Guard Pipeline。
    """
    global _shape_registry
    if _shape_registry is not None:
        return _shape_registry

    import logging
    import os
    from pathlib import Path

    log = logging.getLogger(__name__)

    flag = os.getenv("ONTOAGENT_ENABLE_SHAPES", "true").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        log.info("ShapeRegistry disabled by ONTOAGENT_ENABLE_SHAPES=%r", flag)
        return None

    try:
        from ontoagent.domain.schema import ONTOLOGY_ENTITY_LABELS
        from ontoagent.execution.shape_registry import ShapeRegistry

        shapes_yaml = Path(__file__).parent.parent / "pipeline" / "shapes.yaml"
        if not shapes_yaml.exists():
            log.warning("shapes.yaml not found at %s; ShapeRegistry disabled", shapes_yaml)
            return None

        registry = ShapeRegistry(valid_labels=set(ONTOLOGY_ENTITY_LABELS))
        registry.load_from_yaml(shapes_yaml)
        _shape_registry = registry
        log.info("ShapeRegistry enabled with %d shapes", len(registry))
    except Exception as exc:  # 启动期降级，不能阻塞 Agent
        log.warning("ShapeRegistry init failed (%s); falling back to Guard Pipeline", exc)
        _shape_registry = None

    return _shape_registry


def _get_action_executor(graph_store: object) -> ActionExecutor:
    """获取或初始化 ActionExecutor 单例。

    同时初始化 ShapeRegistry 并注入 ActionExecutor。ShapeRegistry 不可用
    时（shapes.yaml 缺失 / 加载失败）降级为无约束模式。
    """
    global _action_executor
    if _action_executor is None:

        from ontoagent.execution.action_executor import ActionExecutor

        # V5 Phase 5: Guard Pipeline 退役，Shape 成为唯一约束入口
        shape_registry = _get_shape_registry()

        _action_executor = ActionExecutor(
            graph_store,
            function_runner=_get_function_runner(),
            shape_registry=shape_registry,
        )
    return _action_executor


ALL_TOOLS = [
    semantic_search,
    graph_query,
    impact_analysis,
    get_context,
    list_concepts,
    get_module_tree,
    detect_changes,
    export_graph,
    express_intent,
    check_operation,
    explore_ontology,
    explain_constraint,
    suggest_alternatives,
]
