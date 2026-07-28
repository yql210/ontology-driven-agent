"""Graph API router — 图谱数据查询。

后端兼容：自动检测 GraphStore 类型，Neo4j 用 ``labels()``，NebulaGraph 用 ``tags()``。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["graph"])


def _is_nebula(store: object) -> bool:
    """检测 store 是否为 NebulaGraphStore。"""
    return type(store).__name__ == "NebulaGraphStore"


def _label_expr(store: object, var: str, idx: int = 0) -> str:
    """返回获取节点 label 的表达式（后端兼容）。

    - Neo4j: ``labels(n)[0]``
    - NebulaGraph: ``tags(n)[0]``
    """
    fn = "tags" if _is_nebula(store) else "labels"
    return f"{fn}({var})[{idx}]"


def _has_label_check(store: object, var: str) -> str:
    """返回 ``WHERE`` 子句中的「该节点有 label」条件（后端兼容）。

    - Neo4j: ``size(labels(n)) > 0``
    - NebulaGraph: ``size(tags(n)) > 0``
    """
    fn = "tags" if _is_nebula(store) else "labels"
    return f"size({fn}({var})) > 0"


# ===== Stats =====
@router.get("/graph/stats")
def graph_stats(request: Request):
    store = request.app.state.graph_store
    # 节点统计（后端兼容 labels() / tags()）
    label_fn = _label_expr(request.app.state.graph_store, "n")
    has_label = _has_label_check(request.app.state.graph_store, "n")
    node_records = store.query(f"MATCH (n) WHERE {has_label} RETURN {label_fn} AS label, count(*) AS count")
    by_type = {r["label"]: r["count"] for r in node_records if r["label"]}
    total_nodes = sum(by_type.values())
    # 边统计
    edge_record = store.query("MATCH ()-[r]->() RETURN count(*) AS cnt")
    total_edges = edge_record[0]["cnt"] if edge_record else 0
    return {"node_count": total_nodes, "edge_count": total_edges, "by_type": by_type}


# ===== Graph Data =====
@router.get("/graph")
def get_graph(
    request: Request,
    center: str | None = None,
    depth: int = 2,
    limit: int = 200,
    type: str | None = None,
):
    store = request.app.state.graph_store
    # 参数校验
    depth = max(1, min(depth, 3))
    limit = max(1, min(limit, 500))
    allowed_types: list[str] = type.split(",") if type else []

    if center:
        # 中心展开模式（两步查询：先取邻居节点，再取边）
        label_neighbor = _label_expr(store, "neighbor")
        has_label_neighbor = _has_label_check(store, "neighbor")
        label_n = _label_expr(store, "n")
        has_label_n = _has_label_check(store, "n")

        # Step 1: 获取中心节点 + limit 个邻居
        # NebulaGraph 也支持 MATCH path = ...-[*1..N]-，但 labels() 需替换为 tags()
        neighbor_records = store.query(
            f"MATCH path = (center {{name: $name}})-[*1..{depth}]-(neighbor) "
            f"WHERE {has_label_neighbor} "
            "WITH DISTINCT neighbor "
            "LIMIT $limit "
            "RETURN neighbor.id AS id, neighbor.name AS name, "
            f"  {label_neighbor} AS label, "
            "  neighbor.entity_type AS entity_type",
            {"name": center, "limit": limit},
        )
        # 也获取中心节点本身
        center_node = store.query(
            f"MATCH (n {{name: $name}}) WHERE {has_label_n} "
            f"RETURN n.id AS id, n.name AS name, {label_n} AS label, "
            "n.entity_type AS entity_type",
            {"name": center},
        )
        # 合并节点并去重
        nodes_map: dict[str, dict] = {}
        for r in center_node:
            nodes_map[r["id"]] = {
                "id": r["id"],
                "name": r["name"],
                "neo4jLabel": r["label"],
                "entity_type": r.get("entity_type"),
            }
        for r in neighbor_records:
            if r["id"] not in nodes_map:
                nodes_map[r["id"]] = {
                    "id": r["id"],
                    "name": r["name"],
                    "neo4jLabel": r["label"],
                    "entity_type": r.get("entity_type"),
                }
        # Step 2: 获取这些节点之间的边
        all_ids = list(nodes_map.keys())
        edges: list[dict] = []
        if all_ids:
            # startNode()/endNode() → NebulaGraph 用 src()/dst()
            src_fn = "src" if _is_nebula(store) else "startNode"
            dst_fn = "dst" if _is_nebula(store) else "endNode"
            edge_records = store.query(
                "MATCH (a)-[r]->(b) "
                "WHERE a.id IN $ids AND b.id IN $ids "
                f"RETURN {src_fn}(r).id AS source, {dst_fn}(r).id AS target, type(r) AS type",
                {"ids": all_ids},
            )
            edges = [{"source": r["source"], "target": r["target"], "type": r["type"]} for r in edge_records]
        # 类型筛选
        if allowed_types:
            nodes_map = {k: v for k, v in nodes_map.items() if v["neo4jLabel"] in allowed_types}
            valid_ids = set(nodes_map.keys())
            edges = [e for e in edges if e["source"] in valid_ids and e["target"] in valid_ids]
        return {"nodes": list(nodes_map.values()), "edges": edges}
    else:
        # 全图模式（两步查询）
        label_fn = _label_expr(store, "n")
        has_label = _has_label_check(store, "n")
        src_fn = "src" if _is_nebula(store) else "startNode"
        dst_fn = "dst" if _is_nebula(store) else "endNode"

        node_records = store.query(
            f"MATCH (n) WHERE {has_label} "
            "AND ($types = [] OR labels(n)[0] IN $types) "
            f"RETURN n.id AS id, n.name AS name, {label_fn} AS label, "
            "n.entity_type AS entity_type "
            "LIMIT $limit",
            {"types": allowed_types, "limit": limit},
        )
        node_ids = [r["id"] for r in node_records]
        if not node_ids:
            return {"nodes": [], "edges": []}
        edge_records = store.query(
            "MATCH (a)-[r]->(b) "
            "WHERE a.id IN $ids AND b.id IN $ids "
            f"RETURN {src_fn}(r).id AS source, {dst_fn}(r).id AS target, type(r) AS type",
            {"ids": node_ids},
        )
        nodes = [
            {"id": r["id"], "name": r["name"], "neo4jLabel": r["label"], "entity_type": r.get("entity_type")}
            for r in node_records
        ]
        edges = [{"source": r["source"], "target": r["target"], "type": r["type"]} for r in edge_records]
        return {"nodes": nodes, "edges": edges}


# ===== Node Detail =====
@router.get("/graph/node/{node_id}")
def get_node_detail(node_id: str, request: Request):
    store = request.app.state.graph_store
    # 获取节点属性
    node_rec = store.query(
        "MATCH (n {id: $id}) RETURN n.id AS id, n.name AS name, labels(n)[0] AS label, properties(n) AS props",
        {"id": node_id},
    )
    if not node_rec:
        raise HTTPException(status_code=404, detail="Node not found")
    n = node_rec[0]
    # 获取 outgoing
    outgoing = store.query(
        "MATCH (n {id: $id})-[r]->(target) RETURN target.id AS target_id, target.name AS target_name, type(r) AS type",
        {"id": node_id},
    )
    # 获取 incoming
    incoming = store.query(
        "MATCH (source)-[r]->(n {id: $id}) RETURN source.id AS source_id, source.name AS source_name, type(r) AS type",
        {"id": node_id},
    )
    return {
        "id": n["id"],
        "name": n["name"],
        "neo4jLabel": n["label"],
        "properties": {k: v for k, v in n["props"].items() if k not in ("id", "name")} if n["props"] else {},
        "relations": {
            "incoming": [
                {"source_id": r["source_id"], "source_name": r["source_name"], "type": r["type"]} for r in incoming
            ],
            "outgoing": [
                {"target_id": r["target_id"], "target_name": r["target_name"], "type": r["type"]} for r in outgoing
            ],
        },
    }


# ===== Delete Node (Task 4.5) =====
@router.delete("/graph/node/{node_id}")
def delete_node(node_id: str, request: Request):
    store = request.app.state.graph_store
    node = store.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    store.delete_node(node_id)
    return {"status": "deleted", "id": node_id}
