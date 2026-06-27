from __future__ import annotations

import subprocess

from fastmcp import FastMCP

from layerkg.config import LayerKGConfig
from layerkg.pipeline.aligner import ConceptAligner
from layerkg.pipeline.builder import LayerKGBuilder
from layerkg.pipeline.module_clustering import ModuleClustering
from layerkg.store.chroma_store import ChromaStore
from layerkg.store.neo4j_store import Neo4jGraphStore

mcp = FastMCP("LayerKG", instructions="LayerKG Knowledge Graph MCP Server")

# 组件懒加载缓存
_components: dict = {}


def _get_config() -> LayerKGConfig:
    """获取或创建配置实例。

    Returns:
        LayerKGConfig 实例。
    """
    if "config" not in _components:
        _components["config"] = LayerKGConfig.from_env()
    return _components["config"]  # type: ignore[return-value]


def _get_neo4j() -> Neo4jGraphStore:
    """获取或创建 Neo4j 实例。

    Returns:
        Neo4jGraphStore 实例。
    """
    if "neo4j" not in _components:
        config = _get_config()
        _components["neo4j"] = Neo4jGraphStore(config.neo4j_uri, config.neo4j_user, config.neo4j_password)
    return _components["neo4j"]  # type: ignore[return-value]


def _get_chroma() -> ChromaStore:
    """获取或创建 ChromaDB 实例。

    Returns:
        ChromaStore 实例。
    """
    if "chroma" not in _components:
        config = _get_config()
        _components["chroma"] = ChromaStore(
            persist_dir=config.chroma_persist_dir,
            ollama_url=config.ollama_base_url,
            embedding_model=config.embedding_model,
        )
    return _components["chroma"]  # type: ignore[return-value]


def _get_builder() -> LayerKGBuilder:
    """获取或创建 LayerKGBuilder 实例。

    Returns:
        LayerKGBuilder 实例。
    """
    if "builder" not in _components:
        _components["builder"] = LayerKGBuilder(_get_config())
    return _components["builder"]  # type: ignore[return-value]


def _get_aligner() -> ConceptAligner:
    """获取或创建 ConceptAligner 实例。

    Returns:
        ConceptAligner 实例。
    """
    if "aligner" not in _components:
        _components["aligner"] = ConceptAligner(_get_chroma(), neo4j_store=_get_neo4j())
    return _components["aligner"]  # type: ignore[return-value]


def _get_clustering() -> ModuleClustering:
    """获取或创建 ModuleClustering 实例。

    Returns:
        ModuleClustering 实例。
    """
    if "clustering" not in _components:
        _components["clustering"] = ModuleClustering(_get_neo4j())
    return _components["clustering"]  # type: ignore[return-value]


def _reset_components() -> None:
    """重置组件缓存（测试辅助）。"""
    for key in list(_components.keys()):
        comp = _components.pop(key)
        if hasattr(comp, "close"):
            comp.close()


@mcp.tool
def semantic_search(query: str, k: int = 10, entity_type: str | None = None) -> list[dict]:
    """语义检索代码/文档实体。

    Args:
        query: 搜索查询字符串。
        k: 返回结果数量（默认 10）。
        entity_type: 实体类型过滤（可选，如 "function", "class"）。

    Returns:
        匹配实体列表 [{id, text, metadata, distance}]。
    """
    builder = _get_builder()
    return builder.query(query, n_results=k, entity_type=entity_type)


@mcp.tool
def graph_query(cypher: str) -> list[dict]:
    """执行 Cypher 查询 Neo4j 图数据库。

    Args:
        cypher: Cypher 查询语句。

    Returns:
        查询结果列表。
    """
    neo4j = _get_neo4j()
    return neo4j.query(cypher)


@mcp.tool
def impact_analysis(entity_id: str, depth: int = 3) -> dict:
    """分析代码实体的变更影响范围。

    Args:
        entity_id: 实体 ID。
        depth: BFS 搜索深度（默认 3）。

    Returns:
        {entity, impacted_entities: [{id, name, type, distance}], total_count}
    """
    neo4j = _get_neo4j()

    # Cypher 不支持参数化路径长度，需要在 Python 中拼接
    cypher = f"""
    MATCH (n {{id: $entity_id}})-[r*1..{depth}]-(m)
    RETURN n.id AS entity, m.id AS id, m.name AS name,
           m.entityType AS type, head(r) AS rel
    """

    results = neo4j.query(cypher, {"entity_id": entity_id})

    impacted = []
    for row in results:
        impacted.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "type": row.get("type"),
            }
        )

    return {
        "entity": entity_id,
        "impacted_entities": impacted,
        "total_count": len(impacted),
    }


@mcp.tool
def get_context(entity_id: str) -> dict:
    """获取实体的 360° 上下文（代码、文档、概念关联）。

    Args:
        entity_id: 实体 ID。

    Returns:
        {node, relations, similar_entities}
    """
    neo4j = _get_neo4j()
    chroma = _get_chroma()

    # 获取节点
    node = neo4j.get_node(entity_id)

    # 获取关系（incoming + outgoing）
    relations = neo4j.get_relations(source_id=entity_id) + neo4j.get_relations(target_id=entity_id)

    # 获取相似实体
    similar = []
    if node:
        # 使用节点的 text 或 name 进行搜索
        text = node.get("text") or node.get("name", "")
        if text:
            similar = chroma.search(query_text=text, n_results=5)

    return {
        "node": node,
        "relations": relations,
        "similar": similar,
    }


@mcp.tool
def list_concepts() -> list[dict]:
    """列出所有已注册的业务概念。

    Returns:
        概念列表 [{name, id, aliases, entity_type}]。
    """
    aligner = _get_aligner()
    return aligner.list_concepts()


@mcp.tool
def get_module_tree() -> dict:
    """获取代码模块层次结构树。

    Returns:
        {module_name: {entities, cohesion, entity_count}}
    """
    clustering = _get_clustering()
    return clustering.get_module_tree()


@mcp.tool
def detect_changes(since: str = "HEAD~1", repo_path: str = ".") -> dict:
    """检测代码仓库变更。

    Args:
        since: Git 引用（如 HEAD~1, commit hash）。
        repo_path: 仓库路径。

    Returns:
        {changed_files, added, modified, deleted}

    Raises:
        RuntimeError: 当 git 命令失败时。
    """
    result = subprocess.run(
        ["git", "diff", "--name-status", since],
        cwd=repo_path,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        msg = f"Git command failed: {result.stderr.decode()}"
        raise RuntimeError(msg)

    output = result.stdout.decode()
    added = []
    modified = []
    deleted = []

    for line in output.splitlines():
        if not line.strip():
            continue
        status, path = line.strip().split("\t", 1)
        if status == "A":
            added.append(path)
        elif status == "M":
            modified.append(path)
        elif status == "D":
            deleted.append(path)

    return {
        "changed_files": len(added) + len(modified) + len(deleted),
        "added": added,
        "modified": modified,
        "deleted": deleted,
    }


@mcp.tool
def export_graph(format: str = "json") -> dict:
    """导出知识图谱数据。

    Args:
        format: 导出格式（"json" | "dot" | "cytoscape"）。

    Returns:
        JSON 格式: {nodes: [{id, label, properties}], edges: [{source, target, type, properties}]}
        DOT 格式: {content: "digraph {...}"}
        Cytoscape 格式: {elements: {nodes: [...], edges: [...]}}
    """
    neo4j = _get_neo4j()

    # 查询所有节点
    node_cypher = "MATCH (n) RETURN n.id AS id, n.name AS name, labels(n) AS labels"
    node_results = neo4j.query(node_cypher)

    nodes = []
    for row in node_results:
        node_id = row.get("id")
        if node_id:
            nodes.append(
                {
                    "id": node_id,
                    "label": row.get("name", node_id),
                    "labels": row.get("labels", []),
                }
            )

    # 查询所有关系
    rel_cypher = "MATCH ()-[r]->() RETURN startNode(r).id AS source, endNode(r).id AS target, type(r) AS rel_type, properties(r) AS properties"
    rel_results = neo4j.query(rel_cypher)

    edges = []
    for row in rel_results:
        source = row.get("source")
        target = row.get("target")
        if source and target:
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "type": row.get("rel_type"),
                    "properties": row.get("properties", {}),
                }
            )

    if format == "json":
        return {"nodes": nodes, "edges": edges}
    elif format == "dot":
        return {"content": _to_dot(nodes, edges)}
    elif format == "cytoscape":
        return {"elements": _to_cytoscape(nodes, edges)}
    else:
        msg = f"Unsupported format: {format}"
        raise ValueError(msg)


def _to_dot(nodes: list[dict], edges: list[dict]) -> str:
    """转换为 Graphviz DOT 格式字符串。"""
    lines = ["digraph graph {"]
    for node in nodes:
        label = node.get("label", node["id"])
        lines.append(f'  "{node["id"]}" [label="{label}"];')
    for edge in edges:
        lines.append(f'  "{edge["source"]}" -> "{edge["target"]}" [label="{edge.get("type", "")}"];')
    lines.append("}")
    return "\n".join(lines)


def _to_cytoscape(nodes: list[dict], edges: list[dict]) -> dict:
    """转换为 Cytoscape.js 格式。"""
    return {
        "nodes": [{"data": {"id": n["id"], "label": n.get("label", n["id"]), **n.get("labels", {})}} for n in nodes],
        "edges": [
            {
                "data": {
                    "source": e["source"],
                    "target": e["target"],
                    "label": e.get("type", ""),
                    **e.get("properties", {}),
                }
            }
            for e in edges
        ],
    }
