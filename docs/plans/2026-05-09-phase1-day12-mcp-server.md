# Day 12: FastMCP Server 实现计划

> **For Hermes:** 使用 claude-code skill 的 print mode (`-p`) 执行任务。每个任务 2-5 分钟粒度。强制 TDD。详细规范见 `.claude/rules/` 目录。

**Goal:** 实现 FastMCP Server，暴露 8 个 MCP 工具供外部 Agent 调用。

---

## 一、前置分析

### 技术选型
- **FastMCP 3.2.4** — Python MCP SDK，装饰器注册工具
- 传输层：stdio（本地）/ HTTP（远程），默认 stdio
- 依赖：`mcp>=1.24.0`, `pydantic>=2.11.7`（项目已有 pydantic）

### 安装
```bash
uv add fastmcp
```

### 现有组件接口映射

| MCP 工具 | 映射组件 | 方法 |
|---------|---------|------|
| `semantic_search` | LayerKGBuilder | `.query()` |
| `graph_query` | Neo4jGraphStore | `.query()` |
| `impact_analysis` | ImpactPropagator | `.propagate()` |
| `get_context` | Neo4j + ChromaDB | `.get_node()` + `.search()` |
| `list_concepts` | ConceptAligner | `.list_concepts()` |
| `get_module_tree` | ModuleClustering | `.get_module_tree()` |
| `detect_changes` | GitChangeDetector | `.detect()` |
| `export_graph` | Neo4jGraphStore | `.query()` |

---

## 二、新增/修改文件

| 文件 | 类型 | 预估行数 |
|------|------|----------|
| `src/layerkg/mcp_server.py` | 新增 | ~250 |
| `tests/unit/test_mcp_server.py` | 新增 | ~400 |
| `src/layerkg/cli.py` | 修改 | +15 行（`serve` 命令） |

---

## 三、FastMCP Server 架构

```python
from fastmcp import FastMCP
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.chroma_store import ChromaStore
from layerkg.builder import LayerKGBuilder
from layerkg.module_clustering import ModuleClustering
from layerkg.aligner import ConceptAligner

mcp = FastMCP("LayerKG", instructions="LayerKG Knowledge Graph MCP Server")

# 组件实例化在 _init_components() 中懒加载
_components: dict = {}

def _get_builder() -> LayerKGBuilder: ...
def _get_neo4j() -> Neo4jGraphStore: ...
def _get_chroma() -> ChromaStore: ...
def _get_module_clustering() -> ModuleClustering: ...
def _get_concept_aligner() -> ConceptAligner: ...

@mcp.tool
def semantic_search(query: str, k: int = 10) -> list[dict]:
    """语义检索代码/文档实体。"""
    ...

@mcp.tool
def graph_query(cypher: str) -> list[dict]:
    """执行 Cypher 查询图数据库。"""
    ...

# ... 其他 6 个工具
```

---

## 四、8 个 MCP 工具详细设计

### Tool 1: semantic_search
```python
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
```

### Tool 2: graph_query
```python
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
```

### Tool 3: impact_analysis
```python
@mcp.tool
def impact_analysis(entity: str, depth: int = 3) -> dict:
    """分析代码实体的变更影响范围。

    Args:
        entity: 实体名称或 ID。
        depth: BFS 搜索深度（默认 3）。

    Returns:
        {entity, impacted_entities: [{id, name, type, distance}], total_count}
    """
    # 使用 Neo4j Cypher 实现简化版影响分析
    # 先查实体节点，再 BFS 遍历关系
    neo4j = _get_neo4j()
    ...
```

### Tool 4: get_context
```python
@mcp.tool
def get_context(entity: str) -> dict:
    """获取实体的 360° 上下文（代码、文档、概念关联）。

    Args:
        entity: 实体名称或 ID。

    Returns:
        {node, relations, similar_entities}
    """
    neo4j = _get_neo4j()
    chroma = _get_chroma()
    # 1. Neo4j get_node
    # 2. Neo4j get_relations (outgoing + incoming)
    # 3. ChromaDB search (相似实体)
    ...
```

### Tool 5: list_concepts
```python
@mcp.tool
def list_concepts() -> list[dict]:
    """列出所有已注册的业务概念。

    Returns:
        概念列表 [{name, id, aliases, entity_type}]。
    """
    aligner = _get_concept_aligner()
    return aligner.list_concepts()
```

### Tool 6: get_module_tree
```python
@mcp.tool
def get_module_tree() -> dict:
    """获取代码模块层次结构树。

    Returns:
        {module_name: {entities, cohesion, entity_count}}
    """
    clustering = _get_module_clustering()
    return clustering.get_module_tree()
```

### Tool 7: detect_changes
```python
@mcp.tool
def detect_changes(since: str = "HEAD~1", repo_path: str = ".") -> dict:
    """检测代码仓库变更。

    Args:
        since: Git 引用（如 HEAD~1, commit hash）。
        repo_path: 仓库路径。

    Returns:
        {changed_files, added, modified, deleted}
    """
    # 使用 git diff + parser 检测变更
    ...
```

### Tool 8: export_graph
```python
@mcp.tool
def export_graph(format: str = "json") -> dict:
    """导出知识图谱数据。

    Args:
        format: 导出格式（"json" | "dot" | "cytoscape"）。

    Returns:
        JSON 格式: {nodes: [{id, label, properties}], edges: [{source, target, type, properties}]}
        DOT 格式: {content: "digraph {...}"}
        Cytoscape 格式: {elements: {nodes: [{data: {id, label, ...}}], edges: [{data: {source, target, label, ...}}]}}
    """
    neo4j = _get_neo4j()
    # 1. 查询所有节点: MATCH (n) RETURN n, labels(n)
    # 2. 查询所有关系: MATCH ()-[r]->() RETURN startNode(r).id, endNode(r).id, type(r), properties(r)
    # 3. 按 format 转换:
    if format == "json":
        return {"nodes": nodes, "edges": edges}
    elif format == "dot":
        return {"content": _to_dot(nodes, edges)}
    elif format == "cytoscape":
        return {"elements": _to_cytoscape(nodes, edges)}
    ...

def _to_dot(nodes: list[dict], edges: list[dict]) -> str:
    """转换为 Graphviz DOT 格式字符串。"""
    ...

def _to_cytoscape(nodes: list[dict], edges: list[dict]) -> dict:
    """转换为 Cytoscape.js 格式。"""
    return {
        "nodes": [{"data": {"id": n["id"], "label": n.get("name", n["id"]), **n.get("properties", {})}} for n in nodes],
        "edges": [{"data": {"source": e["source"], "target": e["target"], "label": e["type"], **e.get("properties", {})}} for e in edges],
    }
```

---

## 五、辅助函数设计

```python
# 组件生命周期管理（懒加载 + 缓存）
_components: dict[str, Any] = {}

def _get_config() -> LayerKGConfig:
    if "config" not in _components:
        _components["config"] = LayerKGConfig.from_env()
    return _components["config"]

def _get_neo4j() -> Neo4jGraphStore:
    if "neo4j" not in _components:
        config = _get_config()
        _components["neo4j"] = Neo4jGraphStore(
            config.neo4j_uri, config.neo4j_user, config.neo4j_password
        )
    return _components["neo4j"]

def _get_chroma() -> ChromaStore:
    if "chroma" not in _components:
        config = _get_config()
        _components["chroma"] = ChromaStore(
            persist_dir=config.chroma_persist_dir,
            ollama_url=config.ollama_base_url,  # 注意：config 用 ollama_base_url，ChromaStore 用 ollama_url
            embedding_model=config.embedding_model,
        )
    return _components["chroma"]

def _get_builder() -> LayerKGBuilder:
    if "builder" not in _components:
        _components["builder"] = LayerKGBuilder(_get_config())
    return _components["builder"]

def _get_module_clustering() -> ModuleClustering:
    if "clustering" not in _components:
        _components["clustering"] = ModuleClustering(_get_neo4j())
    return _components["clustering"]

def _get_concept_aligner() -> ConceptAligner:
    if "aligner" not in _components:
        _components["aligner"] = ConceptAligner(
            _get_chroma(), neo4j_store=_get_neo4j()
        )
    return _components["aligner"]

def _reset_components() -> None:
    """测试辅助：清空组件缓存。"""
    for key in list(_components.keys()):
        comp = _components.pop(key)
        if hasattr(comp, "close"):
            comp.close()
```

---

## 六、CLI 入口

```python
# cli.py 新增：
@cli.command()
@click.option("--transport", default="stdio", type=click.Choice(["stdio", "http"]))
@click.option("--port", default=8000, type=int)
def serve(transport: str, port: int) -> None:
    """启动 LayerKG MCP Server。"""
    from layerkg.mcp_server import mcp
    if transport == "http":
        mcp.run(transport="http", port=port)
    else:
        mcp.run()
```

---

## 七、TDD 任务分解（12 tasks, ~25 tests）

### Part A: 基础设施（3 tasks, ~5 tests）

#### Task 1: 安装 fastmcp + 模块骨架（1 test）
- `uv add fastmcp`
- 创建 `src/layerkg/mcp_server.py`：导入 FastMCP，创建 mcp 实例
- 测试：`from layerkg.mcp_server import mcp` 成功，mcp.name == "LayerKG"

#### Task 2: _reset_components + _get_config（2 tests）
- 测试 _reset_components 清空缓存
- 测试 _get_config 返回 LayerKGConfig（mock from_env）

#### Task 3: 组件懒加载（2 tests）
- 测试 _get_neo4j 首次创建 + 二次缓存
- 测试 _reset_components 后重新创建

### Part B: MCP 工具实现（6 tasks, ~14 tests）

#### Task 4: semantic_search（3 tests）
- mock builder.query，验证参数传递和返回值
- 测试默认 k=10
- 测试自定义 k=5

#### Task 5: graph_query（2 tests）
- mock neo4j.query，验证 Cypher 传递
- 测试空结果返回 []

#### Task 6: impact_analysis（3 tests）
- 使用 Cypher 查询实现（不直接依赖 ImpactPropagator，避免额外依赖）
- mock neo4j.query 返回实体 + 关系
- 测试深度参数
- 测试不存在的实体 → 空结果

#### Task 7: get_context（2 tests）
- mock neo4j.get_node + neo4j.get_relations + chroma.search
- 测试不存在的实体 → {node: None, relations: [], similar: []}

#### Task 8: list_concepts（1 test）
- mock aligner.list_concepts

#### Task 9: get_module_tree（1 test）
- mock clustering.get_module_tree

### Part C: 高级工具 + CLI（3 tasks, ~6 tests）

#### Task 10: detect_changes（2 tests）
- 使用 git diff 命令实现（subprocess）
- mock subprocess.run
- 测试正常变更检测

#### Task 11: export_graph（2 tests）
- mock neo4j.query 导出节点 + 关系
- 测试 JSON 格式
- 测试空图

#### Task 12: CLI serve 命令（2 tests）
- 测试 `layerkg serve` 命令注册
- 测试 --transport 和 --port 参数

---

## 八、测试策略

由于 FastMCP 工具是普通 Python 函数（装饰器只是注册），测试时：
1. **直接调用函数**（不通过 MCP 协议）
2. **mock 组件懒加载**：patch `_get_builder` / `_get_neo4j` 等
3. **验证参数传递和返回值格式**

```python
# 测试示例
def test_semantic_search(mocker):
    mock_builder = mocker.MagicMock()
    mock_builder.query.return_value = [{"id": "1", "text": "foo"}]
    mocker.patch("layerkg.mcp_server._get_builder", return_value=mock_builder)

    from layerkg.mcp_server import semantic_search
    result = semantic_search("test query", k=5)

    mock_builder.query.assert_called_once_with("test query", n_results=5)
    assert result == [{"id": "1", "text": "foo"}]
```

---

## 九、Claude Code 执行指导

### Batch 1（Task 1-5）: 基础设施 + 简单工具
- 安装 fastmcp
- 创建 mcp_server.py 骨架
- 实现 semantic_search + graph_query
- ~8 tests

### Batch 2（Task 6-12）: 高级工具 + CLI
- impact_analysis + get_context + list_concepts + get_module_tree
- detect_changes + export_graph
- CLI serve 命令
- ~17 tests

### 检查点
每批次完成后：
1. `uv run pytest tests/unit/test_mcp_server.py -v` — 全部通过
2. `uv run ruff check src/ tests/` — 无 lint 错误
3. 确认不破坏已有 494 个测试

---

## 十、风险与缓解

| 风险 | 缓解 |
|------|------|
| FastMCP 版本兼容性 | 已确认 v3.2.4 API，使用稳定装饰器模式 |
| 组件初始化失败 | 懒加载 + 异常传播到工具调用方 |
| 测试中组件状态残留 | _reset_components() 在 fixture 中调用 |
| impact_analysis 复杂度 | 使用 Cypher 实现，不直接依赖 ImpactPropagator |
| detect_changes 依赖 git | mock subprocess.run，不依赖真实 git |
