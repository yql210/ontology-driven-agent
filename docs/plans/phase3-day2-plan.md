# Phase 3 Day 2 实施计划：工具补全 + Langfuse 可观测性（v2 修订版）

> 审核历史：v1 评分 7.8/10，已按审核意见修订
> 前置条件：Day 1 已完成（commit 1feeed1），agent/ 模块骨架 + 2 个工具 + CLI ask + 端到端测试通过

## 目标

1. Agent 工具从 2 个扩展到 8 个
2. 集成 Langfuse 可观测性（追踪每次 Agent 调用的 token/耗时/工具链）
3. Prompt 更新为 8 工具版本
4. 修复 Day 1 遗留的 config.py 重复字段 bug

## Task 0：修复 config.py 重复字段 bug（Day 1 遗留）

**文件**：`src/layerkg/config.py`

删除第 80-98 行重复的 `build_doc_extensions`、`build_doc_max_length`、`build_skip_dirs` 字段定义。这些字段已在第 53-73 行正确定义，第 80-98 行是误加的重复。

同时添加 3 个 Langfuse 字段：

```python
    # Agent/LLM 配置（Phase 3 新增）
    agent_llm_provider: str = "zhipu"
    agent_llm_model: str = "glm-4-flash"
    agent_api_key: str = ""
    agent_base_url: str = "https://open.bigmodel.cn/api/coding/paas/v4"

    # Langfuse 可观测性（Phase 3 Day 2 新增）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://REDACTED_IP:3000"
```

**from_env() 方法**中添加对应环境变量读取：
```python
langfuse_public_key=os.getenv("LAYERKG_LANGFUSE_PUBLIC_KEY", cls.langfuse_public_key),
langfuse_secret_key=os.getenv("LAYERKG_LANGFUSE_SECRET_KEY", cls.langfuse_secret_key),
langfuse_host=os.getenv("LAYERKG_LANGFUSE_HOST", cls.langfuse_host),
```

验证：`uv run python -c "from layerkg.config import LayerKGConfig; c = LayerKGConfig.from_env(); print(c.agent_llm_model, c.langfuse_host)"`

## Task 1：安装 Langfuse 依赖

```bash
uv add langfuse
```

验证：`uv run python -c "import langfuse; print('OK')"`

## Task 2：扩展 _helpers.py — 添加 3 个新单例

当前只有 `get_config`, `get_neo4j`, `get_chroma`。需要添加：

```python
from layerkg.aligner import ConceptAligner
from layerkg.module_clustering import ModuleClustering
from layerkg.impact_propagator import ImpactPropagator

_aligner: ConceptAligner | None = None
_clustering: ModuleClustering | None = None
_impact_propagator: ImpactPropagator | None = None


def get_aligner() -> ConceptAligner:
    """获取 ConceptAligner 单例（从 Neo4j 加载已有概念）。"""
    global _aligner
    if _aligner is None:
        neo4j = get_neo4j()
        # 从 Neo4j 加载概念，否则 list_concepts() 返回空列表
        results = neo4j.query(
            "MATCH (c:ConceptEntity) RETURN c.id AS id, c.name AS name, "
            "c.entity_type AS type, c.description AS description, c.aliases AS aliases"
        )
        from layerkg.schema import ConceptEntity
        concepts = []
        for r in results:
            aliases = r.get("aliases") or []
            concepts.append(ConceptEntity(
                id=r["id"], name=r["name"], entity_type=r["type"],
                description=r.get("description"), aliases=set(aliases)
            ))
        _aligner = ConceptAligner(
            chroma_store=get_chroma(),
            neo4j_store=neo4j,
            concepts=concepts,
        )
    return _aligner


def get_clustering() -> ModuleClustering:
    """获取 ModuleClustering 单例。"""
    global _clustering
    if _clustering is None:
        _clustering = ModuleClustering(neo4j_store=get_neo4j())
    return _clustering


def get_impact_propagator() -> ImpactPropagator:
    """获取 ImpactPropagator 单例。"""
    global _impact_propagator
    if _impact_propagator is None:
        _impact_propagator = ImpactPropagator(graph_store=get_neo4j())
    return _impact_propagator
```

**注意**：
- `ConceptAligner.__init__(chroma_store, concepts=None, ..., neo4j_store=None)` — **必须从 Neo4j 加载 concepts 传入**，否则 `list_concepts()` 返回空
- `ModuleClustering.__init__(neo4j_store, algorithm='label_propagation')` — 正确
- `ImpactPropagator.__init__(graph_store, ...)` — graph_store 接受 GraphStore ABC，Neo4jGraphStore 实现了它

## Task 3：补全 6 个新工具到 tools.py

在现有 `semantic_search` 和 `graph_query` 之后添加 6 个工具：

### 3.1 impact_analysis — 调用 ImpactPropagator（带权重+衰减）

```python
@tool
def impact_analysis(entity_name: str, depth: int = 3) -> str:
    """分析代码实体的变更影响范围（使用权重矩阵 + 衰减调度的 BFS）。

    Args:
        entity_name: 实体名称（函数名/类名，如 "ConceptAligner"）
        depth: 搜索深度，默认 3，建议 2-4

    Returns:
        受影响实体列表（JSON），包含影响分数、严重程度、距离
    """
    from layerkg.change_detector import ChangeType

    neo4j = get_neo4j()
    # 先通过 name 找到 entity_id
    match_result = neo4j.query(
        "MATCH (n) WHERE n.name = $name RETURN n.id AS id LIMIT 1",
        {"name": entity_name}
    )
    if not match_result:
        # 尝试模糊匹配
        match_result = neo4j.query(
            "MATCH (n) WHERE n.name CONTAINS $name RETURN n.id AS id, n.name AS name LIMIT 5",
            {"name": entity_name}
        )
        if not match_result:
            return json.dumps({"error": f"未找到实体: {entity_name}"}, ensure_ascii=False)
        entity_id = match_result[0]["id"]
    else:
        entity_id = match_result[0]["id"]

    # 使用 ImpactPropagator（带权重和衰减）
    propagator = get_impact_propagator()
    impacts = propagator.compute_impact([entity_id], ChangeType.BODY)

    return json.dumps({
        "source": entity_name,
        "total_count": len(impacts),
        "impacted_entities": [i.to_dict() for i in impacts[:50]]
    }, ensure_ascii=False, indent=2)
```

### 3.2 get_context — 节点+关系+相似实体

```python
@tool
def get_context(entity_name: str) -> str:
    """获取函数/类的完整上下文信息：节点属性 + 双向关系 + 语义相似实体。

    Args:
        entity_name: 实体名称（如 "ConceptAligner.align"）

    Returns:
        节点详情、关系列表、相似实体（JSON）
    """
    neo4j = get_neo4j()
    # 找到节点
    match_result = neo4j.query(
        "MATCH (n) WHERE n.name = $name RETURN n.id AS id LIMIT 1",
        {"name": entity_name}
    )
    if not match_result:
        return json.dumps({"error": f"未找到实体: {entity_name}"}, ensure_ascii=False)

    entity_id = match_result[0]["id"]
    node = neo4j.get_node(entity_id)
    # 获取双向关系（source_id + target_id）
    outgoing = neo4j.get_relations(source_id=entity_id)
    incoming = neo4j.get_relations(target_id=entity_id)
    relations = outgoing + incoming

    # 语义相似实体
    similar = []
    if node:
        text = node.get("name", "")
        if text:
            chroma = get_chroma()
            try:
                chroma_results = chroma.search(query_text=text, n_results=5)
                similar = chroma_results if isinstance(chroma_results, list) else []
            except Exception:
                pass  # 降级，不阻塞

    return json.dumps({
        "node": node,
        "relations": relations,
        "similar_entities": similar[:5],
    }, ensure_ascii=False, indent=2)
```

### 3.3 list_concepts — 调用 ConceptAligner

```python
@tool
def list_concepts() -> str:
    """列出项目中所有已识别的业务概念和设计模式。

    Returns:
        概念列表（JSON），包含名称、类型、描述
    """
    aligner = get_aligner()
    results = aligner.list_concepts()
    return json.dumps(results, ensure_ascii=False, indent=2)
```

### 3.4 get_module_tree — 调用 ModuleClustering

```python
@tool
def get_module_tree() -> str:
    """获取代码模块层次结构树。

    Returns:
        模块树（JSON），每个模块包含名称、实体数、内聚度
    """
    clustering = get_clustering()
    tree = clustering.get_module_tree()
    return json.dumps(tree, ensure_ascii=False, indent=2)
```

### 3.5 detect_changes — 调用 subprocess

```python
@tool
def detect_changes(since: str = "HEAD~1") -> str:
    """检测代码仓库最近变更。

    Args:
        since: Git 引用（如 "HEAD~1", "HEAD~5", commit hash）

    Returns:
        变更文件列表（JSON），包含添加/修改/删除
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", since],
            cwd="/opt/data/workspace/ontology-driven-agent",
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return json.dumps({"error": f"git diff 失败: {result.stderr}"}, ensure_ascii=False)

        changed_files = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                changed_files.append({"status": parts[0], "file": parts[1]})

        return json.dumps({
            "since": since,
            "total_changes": len(changed_files),
            "changed_files": changed_files
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
```

### 3.6 export_graph — Neo4j 查询

```python
@tool
def export_graph(limit: int = 100) -> str:
    """导出知识图谱数据（节点和边）。

    Args:
        limit: 最大导出数量，默认 100

    Returns:
        图谱数据（JSON），包含 nodes 和 edges
    """
    neo4j = get_neo4j()
    nodes = neo4j.query(
        "MATCH (n) RETURN n.id AS id, n.name AS name, labels(n) AS labels LIMIT $limit",
        {"limit": limit}
    )
    edges = neo4j.query(
        "MATCH (a)-[r]->(b) RETURN a.id AS source, b.id AS target, "
        "type(r) AS type, properties(r) AS properties LIMIT $limit",
        {"limit": limit}
    )
    return json.dumps({
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges)
    }, ensure_ascii=False, indent=2)
```

### 更新 ALL_TOOLS

```python
ALL_TOOLS = [
    semantic_search,
    graph_query,
    impact_analysis,
    get_context,
    list_concepts,
    get_module_tree,
    detect_changes,
    export_graph,
]
```

**文件头部 import 更新**：
```python
import json
from langchain_core.tools import tool
from layerkg.agent._helpers import get_neo4j, get_chroma, get_aligner, get_clustering, get_impact_propagator
```

## Task 4：更新 prompt.py — 8 工具版 System Prompt

将 Day 1 的 2 工具 prompt 替换为完整 8 工具版：

```python
AGENT_SYSTEM_PROMPT = """你是 LayerKG 代码知识图谱助手。你可以帮助用户理解代码架构、查询依赖关系、分析变更影响。

【工具列表】
1. semantic_search - 语义搜索代码片段（top_k 建议 5-10）
2. graph_query - 执行 Cypher 图查询（关系、依赖、调用链）
3. impact_analysis - 分析代码变更的影响范围（depth 建议 2-4，使用权重+衰减）
4. get_context - 获取函数/类的完整上下文（属性+双向关系+相似实体）
5. list_concepts - 列出项目中的概念和设计模式
6. get_module_tree - 查看项目的模块结构树
7. detect_changes - 检测最近的代码变更
8. export_graph - 导出知识图谱数据

【Schema 参考】
节点标签: CodeEntity, ConceptEntity, DocEntity, ResourceEntity, ModuleEntity
关系类型: CALLS, IMPORTS, CONTAINS, EXTENDS, IMPLEMENTS, DESCRIBES, ILLUSTRATES, DERIVED_FROM, SEMANTIC_IMPACT

【CodeEntity 属性】
- name: 函数/类名（如 "ConceptAligner", "ConceptAligner.align"）
- file_path: 源文件路径
- entity_type: "function" | "class" | "module"
- start_line, end_line: 行号范围
- docstring: 文档字符串（部分实体有）
- code_parameters: 参数列表（部分实体有）

【常用查询模板】
1. 查找实体：MATCH (n:CodeEntity) WHERE n.name CONTAINS '关键词' RETURN n.name, n.file_path, n.entity_type LIMIT 20
2. 调用关系：MATCH (a:CodeEntity)-[:CALLS]->(b:CodeEntity) WHERE a.name CONTAINS '关键词' RETURN a.name, b.name LIMIT 20
3. 被谁调用：MATCH (a:CodeEntity)-[:CALLS]->(b:CodeEntity) WHERE b.name CONTAINS '关键词' RETURN a.name, b.name LIMIT 20
4. CONTAINS关系：MATCH (m:ModuleEntity)-[:CONTAINS]->(n:CodeEntity) RETURN m.name, n.name LIMIT 20
5. 概念关联：MATCH (c:ConceptEntity)<-[:DESCRIBES]-(e:CodeEntity) WHERE c.name CONTAINS '关键词' RETURN c.name, e.name

【工作流程】
1. 理解用户问题，选择合适的工具
2. 优先用专用工具（impact_analysis, get_context），不要手写 BFS Cypher
3. 执行工具，分析结果
4. 如需更多信息，调用其他工具（最多 10 轮）
5. 综合结果，用中文给出清晰回答

【注意事项】
- impact_analysis 和 get_context 接受 entity_name（名称），不是 ID
- 如果名称没匹配，工具内部会尝试模糊匹配
- 不要查询不存在的属性（如 code_snippet、source_code）
"""
```

## Task 5：Langfuse 回调集成

修改 `graph.py`：

```python
def _get_langfuse_handler():
    """创建 Langfuse 回调 handler（可选，无 key 时返回 None）"""
    from layerkg.agent._helpers import get_config
    cfg = get_config()
    if not cfg.langfuse_public_key or not cfg.langfuse_secret_key:
        return None
    from langfuse.callback import CallbackHandler
    return CallbackHandler(
        public_key=cfg.langfuse_public_key,
        secret_key=cfg.langfuse_secret_key,
        host=cfg.langfuse_host,
    )


async def run_query(question: str) -> str:
    """运行单次查询（异步）"""
    agent = create_agent()

    config = {"recursion_limit": 50}
    handler = _get_langfuse_handler()
    if handler:
        config["callbacks"] = [handler]

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=question)]},
        config=config,
    )
    # ... 其余不变
```

**降级策略**：无 Langfuse key 时正常工作，不追踪。不阻塞功能。

## Task 6：补充单元测试

### tests/unit/agent/test_helpers.py — 新增

```python
def test_get_aligner_returns_aligner(mock_config, mock_neo4j):
    """get_aligner 返回 ConceptAligner（带 concepts 加载）"""
    # mock neo4j.query 返回空概念列表
    mock_neo4j.query.return_value = []
    from layerkg.agent._helpers import get_aligner
    aligner = get_aligner()
    assert aligner is not None

def test_get_clustering_returns_clustering(mock_config, mock_neo4j):
    """get_clustering 返回 ModuleClustering"""
    from layerkg.agent._helpers import get_clustering
    clustering = get_clustering()
    assert clustering is not None

def test_get_impact_propagator_returns_propagator(mock_config, mock_neo4j):
    """get_impact_propagator 返回 ImpactPropagator"""
    from layerkg.agent._helpers import get_impact_propagator
    propagator = get_impact_propagator()
    assert propagator is not None
```

### tests/unit/agent/test_tools.py — 新增

```python
def test_impact_analysis_calls_propagator(mock_config, mock_neo4j):
    """impact_analysis 调用 ImpactPropagator.compute_impact"""
    mock_neo4j.query.side_effect = [
        [{"id": "test-123"}],  # name → id 查询
    ]
    # mock propagator
    ...

def test_impact_analysis_entity_not_found(mock_config, mock_neo4j):
    """impact_analysis 实体不存在时返回错误"""
    mock_neo4j.query.return_value = []
    result = impact_analysis.invoke({"entity_name": "不存在"})
    data = json.loads(result)
    assert "error" in data

def test_get_context_returns_json(mock_config, mock_neo4j, mock_chroma):
    """get_context 返回 node + relations + similar"""
    mock_neo4j.query.return_value = [{"id": "test-123"}]
    mock_neo4j.get_node.return_value = {"name": "test", "entity_type": "function"}
    mock_neo4j.get_relations.return_value = []
    result = get_context.invoke({"entity_name": "test"})
    data = json.loads(result)
    assert "node" in data
    assert "relations" in data

def test_list_concepts_calls_aligner(mock_config, mock_neo4j):
    """list_concepts 调用 ConceptAligner"""
    # 需 mock get_aligner
    ...

def test_get_module_tree_returns_json(mock_config, mock_neo4j):
    """get_module_tree 返回模块树"""
    ...

def test_detect_changes_returns_json():
    """detect_changes 返回变更列表"""
    ...

def test_export_graph_returns_json(mock_config, mock_neo4j):
    """export_graph 返回 nodes + edges"""
    mock_neo4j.query.side_effect = [
        [{"id": "1", "name": "A", "labels": ["CodeEntity"]}],
        [{"source": "1", "target": "2", "type": "CALLS", "properties": {}}],
    ]
    result = export_graph.invoke({"limit": 10})
    data = json.loads(result)
    assert data["node_count"] == 1
    assert data["edge_count"] == 1

def test_all_tools_count():
    """ALL_TOOLS 包含 8 个工具"""
    from layerkg.agent.tools import ALL_TOOLS
    assert len(ALL_TOOLS) == 8
```

### tests/unit/agent/test_langfuse.py — 新增

```python
def test_langfuse_handler_returns_none_without_keys(mock_config):
    """无 Langfuse key 时返回 None"""
    mock_config.langfuse_public_key = ""
    mock_config.langfuse_secret_key = ""
    from layerkg.agent.graph import _get_langfuse_handler
    assert _get_langfuse_handler() is None

def test_langfuse_handler_returns_handler_with_keys(mock_config):
    """有 Langfuse key 时返回 CallbackHandler"""
    mock_config.langfuse_public_key = "pk-test"
    mock_config.langfuse_secret_key = "sk-test"
    mock_config.langfuse_host = "http://localhost:3000"
    from layerkg.agent.graph import _get_langfuse_handler
    handler = _get_langfuse_handler()
    assert handler is not None
```

## Task 7：端到端验证

```bash
# 全量测试
uv run pytest tests/ -v

# ruff
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# 端到端测试
uv run layerkg ask "项目有哪些概念"
uv run layerkg ask "分析 ConceptAligner 的影响范围"
uv run layerkg ask "模块结构是怎样的"
uv run layerkg ask "最近有什么变更"
```

## 验收标准

- [ ] `uv run pytest tests/` 全部通过
- [ ] `uv run ruff check src/ tests/` 无错误
- [ ] config.py 无重复字段
- [ ] ALL_TOOLS 包含 8 个工具
- [ ] impact_analysis 调用 ImpactPropagator（不是简单 BFS）
- [ ] get_context 包含双向关系 + 相似实体
- [ ] list_concepts 通过 ConceptAligner 加载（从 Neo4j 获取 concepts）
- [ ] Langfuse 集成：有 key 时追踪，无 key 时降级不报错
- [ ] 端到端：4 个 ask 查询返回正确结果

## 文件清单

| 操作 | 文件 |
|------|------|
| 修改 | `src/layerkg/config.py`（删重复字段 + 3 Langfuse 字段） |
| 修改 | `src/layerkg/agent/_helpers.py`（+3 单例函数，get_aligner 加载 concepts） |
| 修改 | `src/layerkg/agent/tools.py`（+6 工具，更新 ALL_TOOLS） |
| 修改 | `src/layerkg/agent/prompt.py`（8 工具版 prompt，含 DocEntity/ResourceEntity） |
| 修改 | `src/layerkg/agent/graph.py`（Langfuse 回调） |
| 修改 | `tests/unit/agent/test_helpers.py` |
| 修改 | `tests/unit/agent/test_tools.py` |
| 新增 | `tests/unit/agent/test_langfuse.py` |
