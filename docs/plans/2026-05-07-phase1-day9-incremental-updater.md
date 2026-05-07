# Day 9: IncrementalUpdater 实现计划

> **For Hermes:** 使用 claude-code skill 的 print mode (`-p`) 执行任务。每个任务 2-5 分钟粒度。强制 TDD。详细规范见 `.claude/rules/` 目录。

**Goal:** 实现增量引擎 Stage 3-4 — 编排四阶段增量更新流水线，并添加 CLI `update` 命令。

---

## 一、背景与依赖

### 已有组件

| 组件 | 文件 | 职责 |
|------|------|------|
| GitChangeDetector | `change_detector.py` | Stage 1: 检测文件变更，输出 `list[ChangedFile]` |
| ImpactPropagator | `impact_propagator.py` | Stage 2: 双向 BFS 影响传播，输出 `ImpactReport` |
| LayerKGBuilder | `builder.py` | 全量构建：解析→提取→写图+向量 |
| GraphStore ABC | `graph_store.py` | 图存储抽象：merge_node, delete_node, merge_relation, delete_relation, get_node, get_relations, query |
| Neo4jGraphStore | `neo4j_store.py` | Neo4j 实现 |
| ChromaStore | `chroma_store.py` | 向量存储：put_entities_batch, delete_entities, search |
| PythonParser | `parser/python_parser.py` | AST 解析 → CodeEntity 列表 |
| RelationExtractor | `extractor/relation.py` | 关系提取 → Relation 列表 |
| Schema | `schema.py` | ChangeSetEntity, CodeEntity, Relation 等 dataclass |
| LayerKGConfig | `config.py` | 配置管理（Neo4j/Ollama/ChromaDB 连接参数）|
| CLI | `cli.py` | Click CLI 入口，已有 build/query/info 命令 |

### 本日新增文件

- `src/layerkg/incremental_updater.py` — IncrementalUpdater + UpdateReport
- `tests/unit/test_incremental_updater.py` — ~40 tests
- 修改 `src/layerkg/cli.py` — 新增 `update` 命令
- 修改 `src/layerkg/__init__.py` — 导出新符号

---

## 二、核心设计

### 四阶段流水线

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐    ┌──────────────────┐
│ Stage 1     │    │ Stage 2      │    │ Stage 3         │    │ Stage 4          │
│ 变更检测     │───▶│ 影响传播      │───▶│ 选择性重生成     │───▶│ 图验证+持久化     │
│ GitChange   │    │ Impact       │    │ apply_changes   │    │ validate+persist │
│ Detector    │    │ Propagator   │    │                 │    │                  │
└─────────────┘    └──────────────┘    └─────────────────┘    └──────────────────┘
    list[CF]           ImpactReport        dict counters          UpdateReport
```

### Stage 3 选择性重生成策略

| 变更类型 | 图谱操作 | 向量操作 | 说明 |
|----------|----------|----------|------|
| ADDED | 解析新文件 → merge_node + merge_relation | put_entities_batch | 新文件首次入图谱 |
| DELETED | delete_node（先删关系再删节点） | delete_entities | 从图谱中移除 |
| SIGNATURE | 重新解析 → 更新节点属性 + 重建关系 | 更新向量 | 结构变更，影响调用链 |
| BODY | 重新解析 → 更新节点属性 | 更新向量 | 逻辑变更，关系可能不变 |
| DOC_ONLY | 不更新图谱结构 | 更新向量 | 仅注释/文档变更 |

### Stage 4 图验证

1. **悬空边检测**：DELETED 节点后，查询 `MATCH ()-[r]->(n) WHERE n IS NULL RETURN r` 清理残留关系
2. **ChangeSetEntity**：记录本次更新的元数据（变更文件、影响节点、时间戳）
3. **SHA256Cache**：调用 `update_cache(changes)` 持久化缓存
4. **生成 UpdateReport**

#### 问题 1 ✅ 修复：GitChangeDetector 需要 repo_path

构造函数添加 `repo_path: Path` 参数：

```python
def __init__(self, config: LayerKGConfig, repo_path: Path | None = None) -> None:
    self._config = config
    self._repo_path = repo_path  # 用于 GitChangeDetector
    self._parser = PythonParser()
    self._extractor = RelationExtractor()
    ...
```

`_get_change_detector` 传入 repo_path：
```python
def _get_change_detector(self) -> GitChangeDetector:
    if self._change_detector is None:
        self._change_detector = GitChangeDetector(repo_path=self._repo_path)
    return self._change_detector
```

CLI 传入 `repo_path`（见问题 7）。

### 问题 2 ✅ 修复：ChromaStore 无 delete_entities

ChromaStore API 为：
- `delete_entity(entity_id: str) -> bool` — 单个删除
- `delete_entities_by_metadata(where: dict) -> int` — 按条件批量删除

修复方案：用 `delete_entities_by_metadata({"file_path": change.path})` 按文件路径批量删除：

```python
def _apply_deleted(self, change, graph_store, chroma_store) -> dict:
    # ...
    # 删除向量（按文件路径批量删除）
    vec_count = chroma_store.delete_entities_by_metadata({"file_path": change.path})
    return {
        "nodes_deleted": len(node_ids),
        "vectors_updated": vec_count,
    }
```

### 问题 3 ✅ 修复：删除 _remove_dangling_relations

`Neo4jGraphStore.delete_node` 已使用 `DETACH DELETE`，自动清理关联关系。
**删除** `_remove_dangling_relations` 方法，UpdateReport 中 `orphans_removed` 永远为 0。

### 问题 4 ✅ 修复：提取共享工具函数

在 `builder.py` 中将 `_entity_to_dict` 和 `_entity_to_text` 改为模块级函数，
`IncrementalUpdater` 直接 import：

```python
# builder.py（改为模块级函数）
def entity_to_dict(entity: CodeEntity) -> dict: ...
def entity_to_text(entity: CodeEntity) -> str | None: ...

# incremental_updater.py
from layerkg.builder import entity_to_dict, entity_to_text
```

但注意：这需要修改 builder.py 和其测试，属于破坏性改动。
**折中方案**：IncrementalUpdater 内部自行实现（代码少且稳定），后续重构时再统一。

### 问题 5 ✅ 修复：补充边界测试

添加 Task 14：混合场景 + 错误处理（4 tests）。

### 问题 6 ✅ 修复：UpdateReport 添加错误统计

```python
@dataclass
class UpdateReport:
    ...
    parse_errors: int           # 解析失败的文件数
    failed_files: list[str]     # 解析失败的文件路径列表
```

### 问题 7 ✅ 修复：CLI 添加 repo_path

```python
@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--since", default="HEAD~1", help="Git ref 对比基准", show_default=True)
@click.option("--dry-run", is_flag=True, help="仅检测和传播，不执行图谱更新")
@click.option("--full-scan", is_flag=True, help="全量扫描（非 Git diff）")
def update(repo_path: str, since: str, dry_run: bool, full_scan: bool) -> None:
```

---

## 关键设计决策

1. **复用 Builder 子组件**：IncrementalUpdater 内部创建 PythonParser + RelationExtractor，与 Builder 共享同一初始化模式，但不继承 Builder
2. **dry_run 模式**：只执行 Stage 1+2，跳过 Stage 3+4，返回部分 UpdateReport（仅含 changes_detected + impacted_nodes_count）
3. **DELETED 先删关系再删节点**：用 `delete_relation` → `delete_node` 顺序，避免 Neo4j 约束问题
4. **full_scan 模式**：不用 Git diff，改用 `GitChangeDetector.full_scan()` 做全量 SHA256 对比
5. **ADDED 文件处理**：ImpactPropagator 跳过图谱中不存在的节点，IncrementalUpdater 负责将新文件解析入图谱
6. **事务性**：当前不引入 Neo4j 显式事务（单节点操作已是原子性），Phase 2 考虑批量事务

---

## 三、公共接口规范

### UpdateReport dataclass

```python
@dataclass
class UpdateReport:
    """增量更新结果报告。"""
    changes_detected: int       # Stage 1 检测到的变更文件数
    nodes_added: int            # Stage 3 新增节点数
    nodes_updated: int          # Stage 3 更新节点数
    nodes_deleted: int          # Stage 3 删除节点数
    relations_rebuilt: int      # Stage 3 重建关系数
    vectors_updated: int        # Stage 3 更新向量数
    impacted_nodes_count: int   # Stage 2 受影响节点数
    orphans_removed: int        # 始终为 0（DETACH DELETE 自动清理）
    changeset_id: str           # ChangeSetEntity ID（空字符串 if dry_run）
    elapsed_ms: float           # 总耗时（毫秒）
    parse_errors: int           # 解析失败的文件数
    failed_files: list[str]     # 解析失败的文件路径列表

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "changes_detected": self.changes_detected,
            "nodes_added": self.nodes_added,
            "nodes_updated": self.nodes_updated,
            "nodes_deleted": self.nodes_deleted,
            "relations_rebuilt": self.relations_rebuilt,
            "vectors_updated": self.vectors_updated,
            "impacted_nodes_count": self.impacted_nodes_count,
            "orphans_removed": self.orphans_removed,
            "changeset_id": self.changeset_id,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "parse_errors": self.parse_errors,
            "failed_files": self.failed_files,
        }
```

### IncrementalUpdater 类

```python
class IncrementalUpdater:
    """增量更新编排器。
    
    编排四阶段流水线：变更检测 → 影响传播 → 选择性重生成 → 图验证+持久化。
    """

    def __init__(self, config: LayerKGConfig, repo_path: Path | None = None) -> None:
        """初始化。
        
        Args:
            config: LayerKG 配置。
            repo_path: Git 仓库根目录路径（用于 GitChangeDetector）。
        """
        # 内部持有：config, repo_path, parser, extractor, graph_store(lazy), 
        #           chroma_store(lazy), change_detector(lazy), impact_propagator(lazy)
        
    def update(
        self,
        since: str = "HEAD~1",
        *,
        dry_run: bool = False,
        full_scan: bool = False,
    ) -> UpdateReport:
        """主入口：执行增量更新。
        
        Args:
            since: Git ref 对比基准（默认 HEAD~1）。
            dry_run: 仅检测+传播，不执行图谱更新。
            full_scan: 使用全量扫描替代 Git diff。
            
        Returns:
            UpdateReport 更新报告。
        """
        
    def close(self) -> None:
        """关闭所有存储连接。"""
        
    def __enter__(self) -> IncrementalUpdater: ...
    def __exit__(self, *exc: object) -> None: ...
```

### CLI update 命令

```python
@main.command()
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--since", default="HEAD~1", help="Git ref 对比基准", show_default=True)
@click.option("--dry-run", is_flag=True, help="仅检测和传播，不执行图谱更新")
@click.option("--full-scan", is_flag=True, help="全量扫描（非 Git diff）")
def update(repo_path: str, since: str, dry_run: bool, full_scan: bool) -> None:
    """增量更新知识图谱。
    
    检测代码变更，分析影响范围，选择性更新图谱和向量索引。
    """
    config = LayerKGConfig.from_env()
    with IncrementalUpdater(config, Path(repo_path)) as updater:
        report = updater.update(since, dry_run=dry_run, full_scan=full_scan)
        # 输出报告
```

---

## 四、内部实现逻辑

### update() 主流程

```python
def update(self, since, *, dry_run=False, full_scan=False) -> UpdateReport:
    start = time.time()
    
    # Stage 1: 变更检测
    changes = self._detect_changes(since, full_scan=full_scan)
    
    # Stage 2: 影响传播
    impact_report = self._propagate_impact(changes)
    
    if dry_run:
        return UpdateReport(
            changes_detected=len(changes),
            nodes_added=0, nodes_updated=0, nodes_deleted=0,
            relations_rebuilt=0, vectors_updated=0,
            impacted_nodes_count=len(impact_report.impacted_nodes),
            orphans_removed=0, changeset_id="",
            elapsed_ms=(time.time() - start) * 1000,
            parse_errors=0, failed_files=[],
        )
    
    # Stage 3: 选择性重生成
    stage3 = self._apply_changes(changes, impact_report)
    
    # Stage 4: 图验证 + 持久化
    report = self._validate_and_persist(changes, impact_report, stage3, start)
    return report
```

### _detect_changes

```python
def _detect_changes(self, since: str, *, full_scan: bool = False) -> list[ChangedFile]:
    """Stage 1: 调用 GitChangeDetector 检测变更。"""
    detector = self._get_change_detector()
    if full_scan:
        return detector.full_scan()
    return detector.detect_changes(since)
```

### _propagate_impact

```python
def _propagate_impact(self, changes: list[ChangedFile]) -> ImpactReport:
    """Stage 2: 调用 ImpactPropagator 传播影响。"""
    if not changes:
        return ImpactReport(
            changed_files=[], changed_node_ids=[],
            impacted_nodes=[], total_analyzed=0,
            propagation_time_ms=0.0,
        )
    propagator = self._get_impact_propagator()
    return propagator.propagate(changes)
```

### _apply_changes — 核心调度

```python
def _apply_changes(self, changes: list[ChangedFile], impact_report: ImpactReport) -> dict:
    """Stage 3: 根据变更类型分发处理。返回计数器 dict。"""
    counters = {"nodes_added": 0, "nodes_updated": 0, "nodes_deleted": 0,
                "relations_rebuilt": 0, "vectors_updated": 0}
    
    parser = self._parser
    extractor = self._extractor
    graph_store = self._get_graph_store()
    chroma_store = self._get_chroma_store()
    
    for change in changes:
        if change.change_type == ChangeType.ADDED:
            result = self._apply_added(change, parser, extractor, graph_store, chroma_store)
        elif change.change_type == ChangeType.DELETED:
            result = self._apply_deleted(change, graph_store, chroma_store)
        else:
            result = self._apply_modified(change, parser, extractor, graph_store, chroma_store)
        
        for k, v in result.items():
            counters[k] += v
    
    return counters

def _apply_added(self, change, parser, extractor, graph_store, chroma_store) -> dict:
    """处理新增文件：解析 → 写入图谱 + 向量。"""
    abs_path = Path(change.path)
    if not abs_path.exists():
        return {"nodes_added": 0, "relations_rebuilt": 0, "vectors_updated": 0}
    
    parse_result = parser.parse_file(abs_path)
    if parse_result.error:
        return {"nodes_added": 0, "relations_rebuilt": 0, "vectors_updated": 0}
    
    entities = parse_result.entities
    extractor.add_parse_result(entities, parse_result.relations)
    relations = extractor.resolve(entities)
    
    # 写 Neo4j
    for entity in entities:
        graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))
    for rel in relations:
        graph_store.merge_relation(rel.source_id, rel.target_id, rel.relation_type)
    
    # 写 ChromaDB
    items = []
    for entity in entities:
        text = self._entity_to_text(entity)
        if text:
            items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))
    if items:
        chroma_store.put_entities_batch(items)
    
    return {
        "nodes_added": len(entities),
        "relations_rebuilt": len(relations),
        "vectors_updated": len(items),
    }

def _apply_deleted(self, change, graph_store, chroma_store) -> dict:
    """处理删除文件：查找关联节点 → 删除节点（DETACH DELETE）+ 删除向量。"""
    # 查找该文件关联的所有节点
    nodes = graph_store.query(
        "MATCH (n {file_path: $fp}) RETURN n.id AS id",
        {"fp": change.path}
    )
    if not nodes:
        return {"nodes_deleted": 0, "vectors_updated": 0}
    
    node_ids = [n["id"] for n in nodes]
    
    # delete_node 使用 DETACH DELETE，自动清理关联关系
    for nid in node_ids:
        graph_store.delete_node(nid)
    
    # 按文件路径批量删除向量
    vec_count = chroma_store.delete_entities_by_metadata({"file_path": change.path})
    
    return {
        "nodes_deleted": len(node_ids),
        "vectors_updated": vec_count,
    }

def _apply_modified(self, change, parser, extractor, graph_store, chroma_store) -> dict:
    """处理修改文件：根据 SIGNATURE/BODY/DOC_ONLY 分策略。"""
    abs_path = Path(change.path)
    if not abs_path.exists():
        return {"nodes_updated": 0, "relations_rebuilt": 0, "vectors_updated": 0}
    
    if change.change_type == ChangeType.DOC_ONLY:
        # 仅更新向量，不重建图谱结构
        return self._update_vectors_only(change, abs_path, parser, chroma_store)
    
    # SIGNATURE / BODY: 重新解析 → 更新节点属性 + 重建关系 + 更新向量
    parse_result = parser.parse_file(abs_path)
    if parse_result.error:
        return {"nodes_updated": 0, "relations_rebuilt": 0, "vectors_updated": 0}
    
    entities = parse_result.entities
    extractor.add_parse_result(entities, parse_result.relations)
    relations = extractor.resolve(entities)
    
    # 更新节点属性（merge_node 会覆盖同名属性）
    for entity in entities:
        graph_store.merge_node("CodeEntity", self._entity_to_dict(entity))
    
    # SIGNATURE 变更才重建关系
    rel_count = 0
    if change.change_type == ChangeType.SIGNATURE:
        # 先删除旧关系再重建
        old_nodes = graph_store.query(
            "MATCH (n {file_path: $fp}) RETURN n.id AS id",
            {"fp": change.path}
        )
        for n in old_nodes:
            old_rels = graph_store.get_relations(source_id=n["id"])
            old_rels += graph_store.get_relations(target_id=n["id"])
            for rel in old_rels:
                graph_store.delete_relation(rel["source_id"], rel["target_id"], rel["rel_type"])
        
        for rel in relations:
            graph_store.merge_relation(rel.source_id, rel.target_id, rel.relation_type)
            rel_count += 1
    
    # 更新向量
    items = []
    for entity in entities:
        text = self._entity_to_text(entity)
        if text:
            items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))
    if items:
        chroma_store.put_entities_batch(items)
    
    return {
        "nodes_updated": len(entities),
        "relations_rebuilt": rel_count,
        "vectors_updated": len(items),
    }

def _update_vectors_only(self, change, abs_path, parser, chroma_store) -> dict:
    """DOC_ONLY 变更：仅重新生成向量，不更新图谱结构。"""
    parse_result = parser.parse_file(abs_path)
    if parse_result.error:
        return {"vectors_updated": 0}
    
    items = []
    for entity in parse_result.entities:
        text = self._entity_to_text(entity)
        if text:
            items.append((entity.id, text, {"entity_type": entity.entity_type, "name": entity.name}))
    if items:
        chroma_store.put_entities_batch(items)
    
    return {"vectors_updated": len(items)}
```

### _validate_and_persist

```python
def _validate_and_persist(self, changes, impact_report, stage3, start_time) -> UpdateReport:
    """Stage 4: ChangeSetEntity + 更新缓存 + 生成报告。"""
    # 1. 记录 ChangeSetEntity
    changeset_id = self._record_changeset(changes, impact_report, stage3)
    
    # 2. 更新 SHA256 缓存
    detector = self._get_change_detector()
    detector.update_cache(changes)
    
    return UpdateReport(
        changes_detected=len(changes),
        nodes_added=stage3["nodes_added"],
        nodes_updated=stage3["nodes_updated"],
        nodes_deleted=stage3["nodes_deleted"],
        relations_rebuilt=stage3["relations_rebuilt"],
        vectors_updated=stage3["vectors_updated"],
        impacted_nodes_count=len(impact_report.impacted_nodes),
        orphans_removed=0,  # DETACH DELETE 自动清理
        changeset_id=changeset_id,
        elapsed_ms=(time.time() - start_time) * 1000,
        parse_errors=stage3.get("parse_errors", 0),
        failed_files=stage3.get("failed_files", []),
    )

def _record_changeset(self, changes, impact_report, stage3) -> str:
    """记录 ChangeSetEntity 到 Neo4j。"""
    import uuid
    changeset_id = f"cs-{uuid.uuid4().hex[:12]}"
    graph_store = self._get_graph_store()
    graph_store.merge_node("ChangeSetEntity", {
        "id": changeset_id,
        "commit_hash": "incremental",
        "files_changed": [c.path for c in changes],
        "impacted_count": len(impact_report.impacted_nodes),
        "nodes_added": stage3["nodes_added"],
        "nodes_updated": stage3["nodes_updated"],
        "nodes_deleted": stage3["nodes_deleted"],
    })
    return changeset_id
```

### 辅助方法

```python
def _get_change_detector(self) -> GitChangeDetector:
    if self._change_detector is None:
        self._change_detector = GitChangeDetector(repo_path=self._repo_path)
    return self._change_detector

def _get_impact_propagator(self) -> ImpactPropagator:
    if self._impact_propagator is None:
        self._impact_propagator = ImpactPropagator(self._get_graph_store())
    return self._impact_propagator

def _get_graph_store(self) -> Neo4jGraphStore:
    if self._graph_store is None:
        self._graph_store = Neo4jGraphStore(
            uri=self._config.neo4j_uri,
            user=self._config.neo4j_user,
            password=self._config.neo4j_password,
        )
    return self._graph_store

def _get_chroma_store(self) -> ChromaStore:
    if self._chroma_store is None:
        self._chroma_store = ChromaStore(
            persist_dir=self._config.chroma_persist_dir,
            ollama_url=self._config.ollama_base_url,
            embedding_model=self._config.embedding_model,
        )
    return self._chroma_store

# 复用 Builder 的静态方法模式
@staticmethod
def _entity_to_dict(entity: CodeEntity) -> dict:
    """与 LayerKGBuilder._entity_to_dict 相同逻辑。"""
    d = {"id": entity.id, "name": entity.name, "entity_type": entity.entity_type}
    if entity.file_path: d["file_path"] = entity.file_path
    if entity.start_line is not None: d["start_line"] = entity.start_line
    if entity.end_line is not None: d["end_line"] = entity.end_line
    if entity.language: d["language"] = entity.language
    return d

@staticmethod
def _entity_to_text(entity: CodeEntity) -> str | None:
    """与 LayerKGBuilder._entity_to_text 相同逻辑。"""
    if entity.source:
        return entity.source
    parts = [f"{entity.entity_type} {entity.name}"]
    if entity.file_path:
        parts.append(f"in {entity.file_path}")
    return " ".join(parts)
```

---

## 五、Mock 策略

由于 IncrementalUpdater 是编排器，**所有外部依赖都需 mock**：

| 依赖 | Mock 方式 | 说明 |
|------|-----------|------|
| GitChangeDetector | `updater._change_detector = mock` | 返回预设的 `list[ChangedFile]` |
| ImpactPropagator | `updater._impact_propagator = mock` | 返回预设的 `ImpactReport` |
| Neo4jGraphStore | `updater._graph_store = mock` | mock query/get_relations/delete_node/merge_node 等 |
| ChromaStore | `updater._chroma_store = mock` | mock put_entities_batch/delete_entities |
| PythonParser | `updater._parser = mock` | 返回预设的 ParseResult |
| RelationExtractor | `updater._extractor = mock` | 返回预设的 Relation 列表 |

**关键**：构造函数只初始化 `config`、`parser`、`extractor`，其他组件 lazy init。测试时在构造后直接注入 mock。

---

## 六、TDD 任务列表（~40 tests）

> 每个任务 = 2-5 分钟。严格 RED-GREEN-REFACTOR。

### Task 1: UpdateReport dataclass (4 tests)

**Files:** Create `src/layerkg/incremental_updater.py`, Create `tests/unit/test_incremental_updater.py`

- 创建完整 UpdateReport（所有字段）
- 默认值正确（所有计数器初始为 0）
- to_dict() 输出正确（含 elapsed_ms 四舍五入）
- elapsed_ms 正数

### Task 2: IncrementalUpdater 构造函数 (3 tests)

- 创建实例，config + repo_path 属性正确
- lazy init: graph_store 在构造时不创建
- context manager: __enter__ 返回 self, __exit__ 调用 close

### Task 3: _detect_changes (3 tests)

- mock GitChangeDetector.detect_changes → 返回 3 个 ChangedFile
- full_scan=True → 调用 full_scan()
- 无变更 → 返回空列表

### Task 4: _propagate_impact (3 tests)

- mock ImpactPropagator.propagate → 返回含 5 个 impacted_nodes 的报告
- 空变更列表 → 返回空 ImpactReport（不调用 propagator）
- impacted_nodes_count 正确

### Task 5: _apply_added (4 tests)

- mock parser 返回 2 个 entities + 1 个 relation → nodes_added=2, relations_rebuilt=1, vectors_updated=2
- 文件不存在 → 返回全 0
- parse_result.error → 返回全 0
- 无可嵌入文本 → vectors_updated=0

### Task 6: _apply_deleted (4 tests)

- mock query 返回 3 个节点 → nodes_deleted=3
- delete_node 被调用（DETACH DELETE，不手动删关系）
- 图谱中无节点 → 返回全 0
- delete_entities_by_metadata 被调用且参数正确（按 file_path）

### Task 7: _apply_modified SIGNATURE (4 tests)

- SIGNATURE 变更 → 先删旧关系再重建 → nodes_updated + relations_rebuilt
- 验证 merge_node 被调用（更新属性）
- 文件不存在 → 返回全 0
- parse_result.error → 返回全 0

### Task 8: _apply_modified BODY (3 tests)

- BODY 变更 → nodes_updated > 0 但 relations_rebuilt=0（不重建关系）
- 向量被更新
- 验证不调用 delete_relation

### Task 9: _apply_modified DOC_ONLY (3 tests)

- DOC_ONLY → 走 _update_vectors_only 分支
- 不调用 merge_node / merge_relation
- vectors_updated > 0

### Task 10: _record_changeset (3 tests)

- 生成 changeset_id 格式 "cs-{12hex}"
- merge_node 被调用且参数含 ChangeSetEntity label
- files_changed 列表正确

### Task 11: update() 完整流程 (4 tests)

- 完整四阶段 → UpdateReport 所有字段正确（含 parse_errors, failed_files）
- dry_run=True → 只有 changes_detected + impacted_nodes_count，Stage 3/4 不执行，parse_errors=0
- 空变更 → 所有计数器为 0（Stage 2-4 都短路）
- elapsed_ms > 0

### Task 12: CLI update 命令 (4 tests)

- `layerkg update ./repo --since HEAD~3` 调用正确
- `layerkg update ./repo --dry-run` 传入 dry_run=True
- `layerkg update ./repo --full-scan` 传入 full_scan=True
- 输出包含 "Update complete" + 报告摘要

### Task 13: 混合场景 + 边界测试 (4 tests)

- 混合变更：同一次 update 包含 ADDED + DELETED + SIGNATURE + DOC_ONLY → 各计数器正确
- 缓存持久化：update_cache 被调用且参数正确
- parse_errors 计数：部分文件解析失败 → parse_errors > 0, failed_files 非空
- repo_path=None + full_scan=False → 不崩溃（空变更列表）

**合计：~42 tests**

---

## 七、审核修复记录

> Claude Code 审核返回 NEEDS_CHANGES，7 个问题。以下是逐项修复：

| # | 问题 | 严重性 | 修复 |
|---|------|--------|------|
| 1 | GitChangeDetector 缺 repo_path | 🔴 | 构造函数加 `repo_path: Path \| None = None` |
| 2 | ChromaStore 无 delete_entities | 🔴 | 改用 `delete_entities_by_metadata({"file_path": ...})` |
| 3 | 悬空边 Cypher 错误+多余 | 🟡 | 删除方法（DETACH DELETE 自动处理）|
| 4 | _entity_to_dict 重复 | 🟡 | 折中：内部实现，后续重构统一 |
| 5 | 测试覆盖不足 | 🟢 | 加 Task 13 混合边界测试 |
| 6 | UpdateReport 缺错误统计 | 🟢 | 加 `parse_errors` + `failed_files` |
| 7 | CLI 缺 repo_path | 🔴 | 加 `@click.argument("repo_path")` |
