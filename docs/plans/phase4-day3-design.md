# Phase 4 Day 3 方案设计：图谱可视化（Cytoscape.js）v2

> 基于 Claude Code 审核反馈 + 用户补充（实体/边动态增删）修复

## 一、目标

在 Vue 3 前端中集成 Cytoscape.js，实现 LayerKG 知识图谱的交互式可视化：
1. **后端图谱 API** — 提供 Neo4j 图谱数据的 REST 接口
2. **前端图谱页面** — Vue 3 组件 + Cytoscape.js 力导向布局
3. **交互功能** — 搜索、类型筛选、点击详情、双击展开邻居、刷新、删除节点

## 二、后端 API 设计

### 2.1 新增文件：`src/layerkg/web/router/graph.py`

#### GET /api/graph/stats
图谱统计信息。

```json
{
  "node_count": 389,
  "edge_count": 749,
  "by_type": {
    "CodeEntity": 311,
    "ConceptEntity": 42
  }
}
```

Cypher:
```cypher
// 节点统计
MATCH (n) WHERE size(labels(n)) > 0
RETURN labels(n)[0] AS label, count(*) AS count

// 边统计
MATCH ()-[r]->() RETURN count(*) AS edge_count
```

#### GET /api/graph?center={name}&depth={n}&limit={m}&type={types}

参数：
- `center`（可选）：中心节点 name，不传则返回全图
- `depth`（可选，默认 2，最大 3）：展开层数
- `limit`（可选，默认 200，最大 500）：最大节点数
- `type`（可选）：实体类型筛选，逗号分隔如 `CodeEntity,ConceptEntity`

响应：
```json
{
  "nodes": [
    {"id": "uuid-xxx", "name": "ConceptAligner", "label": "CodeEntity", "entity_type": "class"}
  ],
  "edges": [
    {"source": "uuid-a", "target": "uuid-b", "type": "CALLS"}
  ]
}
```

**全图模式 Cypher（两步查询，避免语法错误）：**
```python
# Step 1: 获取节点 ID 列表
allowed_types = type_filter if type_filter else []
node_records = store.query(
    "MATCH (n) WHERE size(labels(n)) > 0 "
    "AND ($types = [] OR labels(n)[0] IN $types) "
    "RETURN n.id AS id, n.name AS name, labels(n)[0] AS label, "
    "n.entity_type AS entity_type "
    "LIMIT $limit",
    {"types": allowed_types, "limit": limit}
)

# Step 2: 获取这些节点之间的边
node_ids = [r["id"] for r in node_records]
edge_records = store.query(
    "MATCH (a)-[r]->(b) "
    "WHERE a.id IN $ids AND b.id IN $ids "
    "RETURN startNode(r).id AS source, endNode(r).id AS target, type(r) AS type",
    {"ids": node_ids}
)
```

序列化（P0-5 修复）：
```python
def _serialize_graph(node_records, edge_records):
    nodes = [
        {"id": r["id"], "name": r["name"], "label": r["label"], "entity_type": r.get("entity_type")}
        for r in node_records
    ]
    edges = [
        {"source": r["source"], "target": r["target"], "type": r["type"]}
        for r in edge_records
    ]
    return {"nodes": nodes, "edges": edges}
```

**中心展开模式 Cypher（原生可变深度，不依赖 APOC）：**
```python
# 使用 [*1..depth] 可变深度路径
records = store.query(
    "MATCH path = (center {name: $name})-[*1..$depth]-(neighbor) "
    "WHERE size(labels(neighbor)) > 0 "
    "AND ($types = [] OR labels(neighbor)[0] IN $types) "
    "WITH DISTINCT neighbor, relationships(path) AS rels "
    "LIMIT $limit "
    "UNWIND rels AS r "
    "RETURN DISTINCT "
    "  neighbor.id AS id, neighbor.name AS name, labels(neighbor)[0] AS label, "
    "  neighbor.entity_type AS entity_type, "
    "  startNode(r).id AS source, endNode(r).id AS target, type(r) AS type",
    {"name": center_name, "depth": depth, "types": allowed_types, "limit": limit}
)
```

#### GET /api/graph/node/{node_id}
节点详情 + 关系列表。

Cypher（P0-4 修复）：
```cypher
// 获取节点属性
MATCH (n {id: $node_id})
RETURN n.id AS id, n.name AS name, labels(n)[0] AS label, properties(n) AS properties

// 获取 outgoing 关系
MATCH (n {id: $node_id})-[r]->(target)
RETURN target.id AS target_id, target.name AS target_name, type(r) AS type

// 获取 incoming 关系
MATCH (source)-[r]->(n {id: $node_id})
RETURN source.id AS source_id, source.name AS source_name, type(r) AS type
```

响应：
```json
{
  "id": "uuid-xxx",
  "name": "ConceptAligner",
  "label": "CodeEntity",
  "properties": {
    "entity_type": "class",
    "file_path": "src/layerkg/aligner.py",
    "start_line": 42
  },
  "relations": {
    "incoming": [{"source_id": "...", "source_name": "...", "type": "CALLS"}],
    "outgoing": [{"target_id": "...", "target_name": "...", "type": "CONTAINS"}]
  }
}
```

错误响应（P1-5 统一格式）：
```json
{"detail": "Node not found", "code": "NODE_NOT_FOUND"}
```

### 2.2 Neo4j 连接管理（P0-6 修复）

使用 FastAPI lifespan，不用全局变量：

```python
# app.py
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    config = LayerKGConfig.from_env()
    store = Neo4jGraphStore(
        uri=config.neo4j_uri, user=config.neo4j_user, password=config.neo4j_password
    )
    app.state.graph_store = store
    yield
    store.close()
```

路由中获取 store：
```python
from fastapi import Request

@router.get("/graph/stats")
def get_stats(request: Request):
    store = request.app.state.graph_store
    ...
```

### 2.3 app.py 修改

```python
from layerkg.web.router.graph import router as graph_router

def create_app() -> FastAPI:
    app = FastAPI(title="LayerKG Agent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, ...)
    app.include_router(chat_router, prefix="/api")
    app.include_router(graph_router, prefix="/api")  # 新增
    ...
```

## 三、前端设计

### 3.1 新增依赖

```bash
cd frontend && npm install cytoscape @types/cytoscape
```

> 不用 cytoscape-cose-bk（需要额外安装复杂依赖），使用 Cytoscape 内置 cose 布局即可。

### 3.2 新增文件

```
frontend/src/
├── api/
│   ├── types.ts          # 扩展：新增 GraphNode/GraphEdge/GraphStats/NodeDetail
│   ├── chat.ts           # 不改
│   └── graph.ts          # 新增：图谱 API 封装
├── views/
│   ├── ChatView.vue      # 不改
│   └── GraphView.vue     # 新增：图谱可视化页面
├── components/
│   ├── ...               # 不改
│   └── NodeDetail.vue    # 新增：节点详情面板
├── stores/
│   ├── chat.ts           # 不改
│   └── graph.ts          # 新增：图谱 Pinia store
└── router/
    └── index.ts          # 修改：新增 /graph 路由
```

### 3.3 TypeScript 类型（P1-2 修复：label → neo4jLabel）

```typescript
// api/types.ts 新增

interface GraphNode {
  id: string
  name: string
  neo4jLabel: string     // Neo4j 标签名 "CodeEntity"（不用 label，避免与 Cytoscape 冲突）
  entity_type?: string   // 细分类型 "class", "function"
}

interface GraphEdge {
  source: string
  target: string
  type: string           // "CALLS", "CONTAINS" 等
}

interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

interface GraphStats {
  node_count: number
  edge_count: number
  by_type: Record<string, number>
}

interface NodeDetail {
  id: string
  name: string
  neo4jLabel: string
  properties: Record<string, any>
  relations: {
    incoming: Array<{ source_id: string; source_name: string; type: string }>
    outgoing: Array<{ target_id: string; target_name: string; type: string }>
  }
}
```

### 3.4 GraphView.vue 布局

```
┌─────────────────────────────────────────┐
│ ← 返回对话    LayerKG 知识图谱   [刷新] │
├─────────────────────────────────────────┤
│ [搜索节点...]  [类型▾筛选]  [重置布局]  │
├──────────────────────┬──────────────────┤
│                      │ 节点详情         │
│   Cytoscape 画布     │ ─────────────    │
│   (cose 力导向)      │ 名称: xxx        │
│                      │ 类型: class      │
│   节点按 neo4jLabel  │ 文件: foo.py     │
│   着色（前端映射）：  │ ─────────────    │
│   - CodeEntity 蓝    │ 关系 (8条)       │
│   - ConceptEntity 绿 │ → calls: fn_a    │
│   - ModuleEntity 橙  │ ← imports: fn_c  │
│   - DocEntity 紫     │                  │
│   - ResourceEntity 粉│ [展开邻居]       │
│   - ChangeSetEntity 红│ [删除节点]      │
│                      │                  │
│   左下角图例         │                  │
├──────────────────────┴──────────────────┤
│ 节点: 389 | 边: 749 | 当前显示: 200     │
└─────────────────────────────────────────┘
```

### 3.5 交互功能

| 操作 | 行为 |
|------|------|
| **单击节点** | 右侧面板显示详情（调 GET /api/graph/node/{id}） |
| **双击节点** | **合并模式**：获取该节点 2 层邻居，去重合入当前图（P1-4） |
| **搜索** | 输入名称，模糊匹配 `CONTAINS`，选中+高亮首个结果。无结果显示 Toast（P1-3） |
| **类型筛选** | 下拉多选 checkbox，只显示选中的 neo4jLabel 类型节点 |
| **重置布局** | 重新运行 cose 布局 |
| **刷新按钮** | 重新从后端加载全图数据（响应用户：实体/边动态增删） |
| **删除节点** | 详情面板中 [删除] 按钮，确认后调后端 API，刷新图 |
| **拖拽节点** | 手动调整位置 |
| **滚轮缩放** | 放大/缩小 |

### 3.6 Cytoscape 样式（P1-1 修复）

不使用 `data(color)`，完全用 selector 按类型着色：

```typescript
const cyStyle: cytoscape.Stylesheet[] = [
  {
    selector: 'node',
    style: {
      'label': 'data(name)',
      'text-wrap': 'wrap',
      'text-max-width': '80px',
      'font-size': '10px',
      'width': 24,
      'height': 24,
      'background-color': '#999',  // 默认灰色
    }
  },
  {
    selector: 'node[neo4jLabel="CodeEntity"]',
    style: { 'background-color': '#4A90D9' }
  },
  {
    selector: 'node[neo4jLabel="ConceptEntity"]',
    style: { 'background-color': '#27AE60' }
  },
  {
    selector: 'node[neo4jLabel="ModuleEntity"]',
    style: { 'background-color': '#F39C12' }
  },
  {
    selector: 'node[neo4jLabel="DocEntity"]',
    style: { 'background-color': '#8E44AD' }
  },
  {
    selector: 'node[neo4jLabel="ResourceEntity"]',
    style: { 'background-color': '#E91E8C' }
  },
  {
    selector: 'node[neo4jLabel="ChangeSetEntity"]',
    style: { 'background-color': '#E74C3C' }
  },
  {
    selector: 'edge',
    style: {
      'width': 1,
      'line-color': '#ccc',
      'target-arrow-color': '#ccc',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      'font-size': '8px',
      'text-rotation': 'autorotate',
    }
  },
  {
    selector: 'node:Selected',
    style: { 'border-width': 3, 'border-color': '#FFD700' }
  },
]
```

### 3.7 导航

App.vue 顶部添加导航栏：
- 左侧：LayerKG Logo
- 中间：[💬 对话] [🕸️ 图谱] 标签切换
- 路由 `/` → ChatView，`/graph` → GraphView

### 3.8 图例组件（GraphView 内嵌）

左下角固定定位，显示颜色-类型映射：
```
┌──────────────┐
│ ■ Code       │
│ ■ Concept    │
│ ■ Module     │
│ ■ Doc        │
│ ■ Resource   │
│ ■ ChangeSet  │
└──────────────┘
```

## 四、关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 图布局 | **内置 cose** | 够用，避免额外依赖 |
| 节点数上限 | **200 默认，500 最大** | 性能 vs 信息量平衡 |
| 子图查询 | **原生 Cypher 两步查询** | 不依赖 APOC |
| 节点详情 | **后端 API** | 需 Cypher 查关系 |
| Neo4j 管理 | **FastAPI lifespan** | 线程安全+自动关闭 |
| 双击展开 | **合并模式（去重）** | 保留已有上下文，新增邻居 |
| 动态数据 | **手动刷新按钮** | 简单直接，不做 WebSocket 推送 |
| 颜色映射 | **CSS selector** | 不用 data(color)，避免属性冲突 |
| TS 字段命名 | **neo4jLabel** | 避免 Cytoscape label 语义冲突 |

## 五、不改什么

1. **不修改 neo4j_store.py** — 通过 `query()` 执行自定义 Cypher
2. **不修改 ChatView 及相关组件**
3. **不安装 APOC** — 原生 Cypher
4. **不做 WebSocket 实时推送** — 手动刷新
5. **不做导出图片** — P2，后续可加
