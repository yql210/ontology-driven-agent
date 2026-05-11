# Phase 4 Day 3 实施计划

> 基于 phase4-day3-design.md v2 方案

## 任务总览

| # | 任务 | 文件 | 批次 |
|---|------|------|------|
| 1 | 安装 cytoscape 前端依赖 | frontend/package.json | B1 |
| 2 | 新增 TS 类型定义 | frontend/src/api/types.ts | B1 |
| 3 | 新增图谱 API 封装 | frontend/src/api/graph.ts | B1 |
| 4 | 后端 graph router | src/layerkg/web/router/graph.py | B1 |
| 5 | 修改 app.py：lifespan + graph_router | src/layerkg/web/app.py | B1 |
| 6 | 新增图谱 Pinia store | frontend/src/stores/graph.ts | B2 |
| 7 | 新增 NodeDetail 组件 | frontend/src/components/NodeDetail.vue | B2 |
| 8 | 新增 GraphView 页面 | frontend/src/views/GraphView.vue | B2 |
| 9 | 修改 router：新增 /graph 路由 | frontend/src/router/index.ts | B2 |
| 10 | 修改 App.vue：导航栏 | frontend/src/App.vue | B2 |
| 11 | 后端单元测试 | tests/unit/test_web.py | B3 |
| 12 | 前端 build 验证 + 全量测试 | — | B3 |

## Batch 1：后端 API + 前端基础

### Task 1: 安装 cytoscape
```bash
cd frontend && npm install cytoscape && npm install -D @types/cytoscape
```

### Task 2: 新增 TS 类型（扩展 frontend/src/api/types.ts）
在文件末尾添加：
```typescript
// ========= Graph Types =========

export interface GraphNode {
  id: string
  name: string
  neo4jLabel: string
  entity_type?: string
}

export interface GraphEdge {
  source: string
  target: string
  type: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface GraphStats {
  node_count: number
  edge_count: number
  by_type: Record<string, number>
}

export interface NodeDetail {
  id: string
  name: string
  neo4jLabel: string
  properties: Record<string, unknown>
  relations: {
    incoming: Array<{ source_id: string; source_name: string; type: string }>
    outgoing: Array<{ target_id: string; target_name: string; type: string }>
  }
}
```

### Task 3: 新增 frontend/src/api/graph.ts
```typescript
import type { GraphData, GraphStats, NodeDetail } from './types'

const API_BASE = '/api'

export async function fetchGraph(params?: {
  center?: string
  depth?: number
  limit?: number
  type?: string
}): Promise<GraphData> {
  const sp = new URLSearchParams()
  if (params?.center) sp.set('center', params.center)
  if (params?.depth) sp.set('depth', String(params.depth))
  if (params?.limit) sp.set('limit', String(params.limit))
  if (params?.type) sp.set('type', params.type)
  const qs = sp.toString()
  const url = `${API_BASE}/graph${qs ? '?' + qs : ''}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Failed to fetch graph: ${res.status}`)
  return res.json()
}

export async function fetchGraphStats(): Promise<GraphStats> {
  const res = await fetch(`${API_BASE}/graph/stats`)
  if (!res.ok) throw new Error(`Failed to fetch stats: ${res.status}`)
  return res.json()
}

export async function fetchNodeDetail(nodeId: string): Promise<NodeDetail> {
  const res = await fetch(`${API_BASE}/graph/node/${encodeURIComponent(nodeId)}`)
  if (!res.ok) throw new Error(`Failed to fetch node: ${res.status}`)
  return res.json()
}
```

### Task 4: 新增 src/layerkg/web/router/graph.py

后端图谱路由，3 个端点：

```python
"""Graph API router — 图谱数据查询。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["graph"])

# ===== Stats =====
@router.get("/graph/stats")
def graph_stats(request: Request):
    store = request.app.state.graph_store
    # 节点统计
    node_records = store.query(
        "MATCH (n) WHERE size(labels(n)) > 0 "
        "RETURN labels(n)[0] AS label, count(*) AS count"
    )
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
        # Step 1: 获取中心节点 + limit 个邻居
        neighbor_records = store.query(
            "MATCH path = (center {name: $name})-[*1..$depth]-(neighbor) "
            "WHERE size(labels(neighbor)) > 0 "
            "WITH DISTINCT neighbor "
            "LIMIT $limit "
            "RETURN neighbor.id AS id, neighbor.name AS name, "
            "  labels(neighbor)[0] AS label, "
            "  neighbor.entity_type AS entity_type",
            {"name": center, "depth": depth, "limit": limit}
        )
        # 也获取中心节点本身
        center_node = store.query(
            "MATCH (n {name: $name}) WHERE size(labels(n)) > 0 "
            "RETURN n.id AS id, n.name AS name, labels(n)[0] AS label, "
            "n.entity_type AS entity_type",
            {"name": center}
        )
        # 合并节点并去重
        nodes_map: dict[str, dict] = {}
        for r in center_node:
            nodes_map[r["id"]] = {
                "id": r["id"], "name": r["name"],
                "neo4jLabel": r["label"],
                "entity_type": r.get("entity_type"),
            }
        for r in neighbor_records:
            if r["id"] not in nodes_map:
                nodes_map[r["id"]] = {
                    "id": r["id"], "name": r["name"],
                    "neo4jLabel": r["label"],
                    "entity_type": r.get("entity_type"),
                }
        # Step 2: 获取这些节点之间的边
        all_ids = list(nodes_map.keys())
        edges: list[dict] = []
        if all_ids:
            edge_records = store.query(
                "MATCH (a)-[r]->(b) "
                "WHERE a.id IN $ids AND b.id IN $ids "
                "RETURN startNode(r).id AS source, endNode(r).id AS target, type(r) AS type",
                {"ids": all_ids}
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
        node_records = store.query(
            "MATCH (n) WHERE size(labels(n)) > 0 "
            "AND ($types = [] OR labels(n)[0] IN $types) "
            "RETURN n.id AS id, n.name AS name, labels(n)[0] AS label, "
            "n.entity_type AS entity_type "
            "LIMIT $limit",
            {"types": allowed_types, "limit": limit}
        )
        node_ids = [r["id"] for r in node_records]
        if not node_ids:
            return {"nodes": [], "edges": []}
        edge_records = store.query(
            "MATCH (a)-[r]->(b) "
            "WHERE a.id IN $ids AND b.id IN $ids "
            "RETURN startNode(r).id AS source, endNode(r).id AS target, type(r) AS type",
            {"ids": node_ids}
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
        "MATCH (n {id: $id}) RETURN n.id AS id, n.name AS name, "
        "labels(n)[0] AS label, properties(n) AS props",
        {"id": node_id}
    )
    if not node_rec:
        raise HTTPException(status_code=404, detail="Node not found")
    n = node_rec[0]
    # 获取 outgoing
    outgoing = store.query(
        "MATCH (n {id: $id})-[r]->(target) "
        "RETURN target.id AS target_id, target.name AS target_name, type(r) AS type",
        {"id": node_id}
    )
    # 获取 incoming
    incoming = store.query(
        "MATCH (source)-[r]->(n {id: $id}) "
        "RETURN source.id AS source_id, source.name AS source_name, type(r) AS type",
        {"id": node_id}
    )
    return {
        "id": n["id"],
        "name": n["name"],
        "neo4jLabel": n["label"],
        "properties": {k: v for k, v in n["props"].items() if k not in ("id", "name")} if n["props"] else {},
        "relations": {
            "incoming": [{"source_id": r["source_id"], "source_name": r["source_name"], "type": r["type"]} for r in incoming],
            "outgoing": [{"target_id": r["target_id"], "target_name": r["target_name"], "type": r["type"]} for r in outgoing],
        }
    }
```

### Task 5: 修改 app.py
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.web.router.chat import router as chat_router
from layerkg.web.router.graph import router as graph_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    config = LayerKGConfig.from_env()
    store = Neo4jGraphStore(
        uri=config.neo4j_uri, user=config.neo4j_user, password=config.neo4j_password
    )
    app.state.graph_store = store
    yield
    store.close()

def create_app() -> FastAPI:
    app = FastAPI(title="LayerKG Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(chat_router, prefix="/api")
    app.include_router(graph_router, prefix="/api")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

## Batch 2：前端组件

### Task 6: 新增 frontend/src/stores/graph.ts
Pinia store 管理图谱状态：
- `graphData: GraphData` — 当前显示的节点/边
- `stats: GraphStats | null` — 统计信息
- `selectedNode: NodeDetail | null` — 选中节点详情
- `isLoading: boolean`
- `loadGraph(params?)` — 加载图谱
- `loadStats()` — 加载统计
- `selectNode(id)` — 选中节点并加载详情
- `expandNode(name)` — 双击展开邻居（合并模式）
- `refresh()` — 重新加载

### Task 7: 新增 frontend/src/components/NodeDetail.vue
右侧面板，显示节点详情：
- Props: `node: NodeDetail | null`
- 显示名称、类型、属性列表、关系列表
- [展开邻居] 按钮 → emit('expand', node.name)
- [删除] 按钮 → emit('delete', node.id)（确认弹窗）

### Task 8: 新增 frontend/src/views/GraphView.vue
主页面：
- 左侧：Cytoscape 画布（ref 容器 + 初始化 cytoscape 实例）
- 右侧：NodeDetail 面板
- 顶部：搜索框 + 类型筛选 + 重置 + 刷新按钮
- 底部：统计栏
- 左下角：图例
- Cytoscape 初始化：绑定 tap(选中)、dbltap(展开)、拖拽事件
- 双击展开实现：获取邻居数据 → cy.add() 合并新节点/边

### Task 9: 修改 router/index.ts
```typescript
import GraphView from '../views/GraphView.vue'
// 新增路由
{ path: '/graph', name: 'graph', component: GraphView }
```

### Task 10: 修改 App.vue
```vue
<template>
  <div class="app-container">
    <nav class="nav-bar">
      <span class="logo">LayerKG</span>
      <div class="nav-links">
        <router-link to="/">💬 对话</router-link>
        <router-link to="/graph">🕸️ 图谱</router-link>
      </div>
    </nav>
    <router-view />
  </div>
</template>
```
简单 CSS 样式（固定顶部导航栏，flex 布局）。

## Batch 3：测试验证

### Task 4.5: 后端 graph.py 新增删除端点
（用户要求：实体和边可能动态删除）

在 graph.py 末尾新增：
```python
@router.delete("/graph/node/{node_id}")
def delete_node(node_id: str, request: Request):
    store = request.app.state.graph_store
    node = store.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    store.delete_node(node_id)  # DETACH DELETE 同时删关系
    return {"status": "deleted", "id": node_id}
```

### Task 11: 后端单元测试（扩展 tests/unit/test_web.py）
新增测试：
- `test_graph_stats` — mock store.query，验证返回格式
- `test_get_graph_full` — 全图模式
- `test_get_graph_center` — 中心展开模式
- `test_get_node_detail` — 节点详情
- `test_get_node_detail_not_found` — 404
- `test_delete_node` — 删除节点
- `test_delete_node_not_found` — 404

### Task 12: 前端 build + 全量测试
```bash
cd frontend && npm run build
uv run pytest tests/ -v
uv run ruff check src/ tests/
```
