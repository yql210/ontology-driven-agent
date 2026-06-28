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
def express_intent(intent_type: str, target: str, params: dict | None = None) -> str:
    """当你识别到用户有操作意图时调用此工具。可用操作类型会在系统提示中列出。

    Args:
        intent_type: 操作类型（如 refactor, document, analyze_impact）
        target: 目标实体名称
        params: 可选参数（dict）

    Returns:
        执行结果的 JSON 格式字符串
    """
    try:
        neo4j = get_neo4j()
        executor = _get_action_executor(neo4j)
        result = executor.execute(intent_type, {"target": target, **(params or {})})
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"操作执行失败: {e!s}"}, ensure_ascii=False)


_action_executor: ActionExecutor | None = None
_function_runner: Any | None = None


def _get_function_runner() -> Any:
    """获取全局 FunctionRunner 单例（lazy init）。"""
    global _function_runner
    if _function_runner is None:
        from ontoagent.execution.function_runner import FunctionRunner

        _function_runner = FunctionRunner()
    return _function_runner


def _get_action_executor(graph_store: object) -> ActionExecutor:
    """获取或初始化 ActionExecutor 单例。

    Uses OntologyConstraintLoader for three-layer constraint loading:
    Layer 1: auto-derive value_mapping from ONTOLOGY_CONSTRAINT_REGISTRY
    Layer 2: apply overrides from constraint_overrides.yaml
    """
    global _action_executor
    if _action_executor is None:
        import logging
        from pathlib import Path

        from ontoagent.domain.schema import ONTOLOGY_CONSTRAINT_REGISTRY
        from ontoagent.execution.action_executor import ActionExecutor
        from ontoagent.execution.constraints import (
            ActionGuardPipeline,
            ConstraintEngine,
            ConstraintPropagator,
            EntityExistsGuard,
            EntityPropertyGuard,
            OntologyPropagationGuard,
            OntologyTraversalGuard,
            WhitelistGuard,
        )
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        constraints_yaml = Path(__file__).parent.parent / "pipeline" / "constraints.yaml"
        overrides_yaml = Path(__file__).parent.parent / "config" / "constraint_overrides.yaml"

        loader = OntologyConstraintLoader(registry=ONTOLOGY_CONSTRAINT_REGISTRY)
        traversals, prop_rules, warnings, allow_set = loader.load_all(
            constraints_yaml=constraints_yaml,
            overrides_yaml=overrides_yaml,
        )

        # Log startup warnings/notices from the loader
        log = logging.getLogger(__name__)
        for w in warnings:
            log.warning(w)

        engine = ConstraintEngine(graph_store, traversals)
        propagator = ConstraintPropagator(graph_store)
        guard_pipeline = ActionGuardPipeline(
            [
                WhitelistGuard(allow_set),
                EntityExistsGuard(),
                EntityPropertyGuard(),
                OntologyTraversalGuard(engine),
                OntologyPropagationGuard(propagator, rules=prop_rules),
            ]
        )

        _action_executor = ActionExecutor(
            graph_store,
            function_runner=_get_function_runner(),
            guard_pipeline=guard_pipeline,
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
]
