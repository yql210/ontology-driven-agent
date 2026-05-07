# Day 8: ImpactPropagator 实现计划

> **For Hermes:** 使用 claude-code skill 的 print mode (`-p`) 执行任务。每个任务 2-5 分钟粒度。强制 TDD。详细规范见 `.claude/rules/` 目录。

**Goal:** 实现增量引擎 Stage 2 — 双向 BFS 影响传播。接收 Day 7 的 `list[ChangedFile]`，通过 GraphStore 抽象层遍历知识图谱，输出带评分的 `list[ImpactedNode]`。

**Architecture:** ImpactPropagator 从变更文件出发，先通过 GraphStore 查询将 file_path 映射为图谱节点，再执行双向 BFS（正向找依赖方、反向找被依赖方），每跳按 `weight_matrix[relation_type][change_type] × decay_schedule[depth]` 计算影响分数，同一节点多条路径取 MAX，最终返回按分数排序的影响报告。

**Tech Stack:** Python 3.13+ / dataclasses / enum / collections.deque / 无新外部依赖

---

## 一、数据模型

### PropagationDirection 枚举

- `FORWARD`: 正向传播 — 谁依赖了我（我的变更影响谁）
- `BACKWARD`: 反向传播 — 我依赖了谁（我的变更可能受谁影响）

### ImpactSeverity 枚举

| 值 | 分数范围 | 含义 |
|---|---------|------|
| CRITICAL | ≥ 0.8 | 直接依赖，很可能受影响 |
| HIGH | ≥ 0.5 | 较近依赖，需要审查 |
| MEDIUM | ≥ 0.2 | 间接依赖，可能受影响 |
| LOW | < 0.2 | 远距离依赖，影响较小 |

### ImpactedNode dataclass

| 字段 | 类型 | 说明 |
|------|------|------|
| node_id | str | 图谱节点 ID |
| node_label | str | 节点标签（CodeEntity/DocEntity 等） |
| name | str | 实体名称 |
| file_path | str \| None | 文件路径 |
| impact_score | float | 影响分数 [0, 1] |
| severity | ImpactSeverity | 严重程度分类 |
| depth | int | 距离变更源的跳数 |
| direction | PropagationDirection | 传播方向 |
| relation_path | list[str] | 从变更源到当前节点的完整关系类型路径（如 ["calls", "imports"]） |
| source_node_id | str | 变更源节点 ID |

### WeightMatrix 类

关系类型 × 变更类型的权重矩阵，`dict[str, dict[str, float]]` 包装类。

```python
DEFAULT_WEIGHT_MATRIX: dict[str, dict[str, float]] = {
    # relation_type: {ADDED, DELETED, SIGNATURE, BODY, DOC_ONLY}
    "calls":           {"ADDED": 0.9, "DELETED": 1.0, "SIGNATURE": 0.9, "BODY": 0.7, "DOC_ONLY": 0.1},
    "implements":      {"ADDED": 0.8, "DELETED": 1.0, "SIGNATURE": 0.8, "BODY": 0.5, "DOC_ONLY": 0.1},
    "extends":         {"ADDED": 0.8, "DELETED": 0.9, "SIGNATURE": 0.9, "BODY": 0.6, "DOC_ONLY": 0.1},
    "imports":         {"ADDED": 0.5, "DELETED": 0.6, "SIGNATURE": 0.5, "BODY": 0.3, "DOC_ONLY": 0.0},
    "semantic_impact": {"ADDED": 0.4, "DELETED": 0.5, "SIGNATURE": 0.5, "BODY": 0.4, "DOC_ONLY": 0.2},
    "describes":       {"ADDED": 0.3, "DELETED": 0.3, "SIGNATURE": 0.3, "BODY": 0.2, "DOC_ONLY": 0.3},
    # 占位：当前阶段不参与传播，Phase 1+ 可能启用
    "derived_from":    {"ADDED": 0.0, "DELETED": 0.0, "SIGNATURE": 0.0, "BODY": 0.0, "DOC_ONLY": 0.0},
    "affects":         {"ADDED": 0.0, "DELETED": 0.0, "SIGNATURE": 0.0, "BODY": 0.0, "DOC_ONLY": 0.0},
}
```

**说明**（基于技术调研结论）：
- `calls` DELETED=1.0（删除调用关系影响最大），ADDED=0.9（新增调用方影响也很大）
- `imports` DOC_ONLY=0.0（导入关系不受文档变化影响）
- `semantic_impact` 权重适中（语义关系传播力弱于结构关系）
- `derived_from` 和 `affects` 设为 0.0 占位，明确标注"不参与传播"
- `contains`/`illustrates` 等关系不在矩阵中，默认权重 0.0

### DecaySchedule

```python
DEFAULT_DECAY_SCHEDULE: dict[int, float] = {
    1: 1.0,
    2: 0.6,
    3: 0.3,
}
# depth > 3: 停止传播
```

---

## 二、ImpactPropagator 核心类

### 构造函数

```python
class ImpactPropagator:
    def __init__(
        self,
        graph_store: GraphStore,
        weight_matrix: dict[str, dict[str, float]] | None = None,
        decay_schedule: dict[int, float] | None = None,
        max_depth: int = 3,
        impact_threshold: float = 0.05,
    ) -> None: ...
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| graph_store | 必需 | GraphStore ABC 实例（Neo4j 或 mock） |
| weight_matrix | DEFAULT_WEIGHT_MATRIX | 关系×变更类型权重 |
| decay_schedule | DEFAULT_DECAY_SCHEDULE | 深度衰减 |
| max_depth | 3 | 最大传播深度 |
| impact_threshold | 0.05 | 低于此分数的节点不收录 |

### 公开方法

| 方法 | 说明 |
|------|------|
| `propagate(changes: list[ChangedFile]) -> ImpactReport` | 主入口，执行完整传播流程 |
| `map_files_to_nodes(changes: list[ChangedFile]) -> dict[str, list[str]]` | file_path → node_ids 映射 |
| `compute_impact(node_ids: list[str], change_type: ChangeType) -> list[ImpactedNode]` | 单次 BFS 传播 |

### 内部方法

| 方法 | 说明 |
|------|------|
| `_bfs_step(frontier, visited, change_type, depth, direction)` | 单层 BFS 扩展 |
| `_compute_score(relation_type, change_type, depth) -> float` | weight × decay |
| `_classify_severity(score) -> ImpactSeverity` | 分数→严重程度 |
| `_merge_impacts(*impact_lists) -> list[ImpactedNode]` | 多源影响合并（同节点取 MAX） |

### 传播流程

```
ChangedFile[] 
    → map_files_to_nodes: file_path → {node_id: change_type}
    → 对每个 node_id + change_type:
        → 正向BFS: get_relations(source_id=node_id) → 下游节点
        → 反向BFS: get_relations(target_id=node_id) → 上游节点
        → 每跳: score = weight_matrix[rel_type][change_type] × decay[depth]
        → score >= threshold → 收录为 ImpactedNode
    → _merge_impacts: 同节点多条路径取 MAX score
    → 按 score 降序排序
    → ImpactReport
```

---

## 三、ImpactReport 数据类

```python
@dataclass
class ImpactReport:
    """影响传播结果报告。"""
    changed_files: list[str]              # 变更文件路径列表
    changed_node_ids: list[str]           # 变更节点 ID 列表
    impacted_nodes: list[ImpactedNode]    # 受影响节点（按分数降序）
    total_analyzed: int                   # BFS 遍历的总节点数
    propagation_time_ms: float            # 传播耗时（毫秒）

    @property
    def critical_count(self) -> int: ...
    @property
    def affected_files(self) -> set[str]: ...
    @property
    def nodes_by_severity(self) -> dict[ImpactSeverity, list[ImpactedNode]]: ...
    def to_dict(self) -> dict: ...
```

---

## 四、BFS 算法核心伪代码

```python
def compute_impact(self, node_ids, change_type):
    """双向 BFS 影响 propagation。"""
    all_impacts = []
    
    for source_id in node_ids:
        # 正向：谁依赖了我（source 作为关系的 target）
        forward_impacts = self._bidirectional_bfs(
            source_id, change_type, PropagationDirection.FORWARD
        )
        # 反向：我依赖了谁（source 作为关系的 source）
        backward_impacts = self._bidirectional_bfs(
            source_id, change_type, PropagationDirection.BACKWARD
        )
        all_impacts.extend(forward_impacts)
        all_impacts.extend(backward_impacts)
    
    return self._merge_impacts(all_impacts)

def _bidirectional_bfs(self, source_id, change_type, direction):
    """单方向 BFS，带深度衰减和权重矩阵。
    
    visited 策略：使用 dict[str, float] 记录 node_id → best_score，
    允许高分路径覆盖低分的已访问节点。
    """
    # frontier: 当前层的 {node_id: (best_score, relation_path)}
    frontier: dict[str, tuple[float, list[str]]] = {
        source_id: (1.0, [])
    }
    # visited: node_id → best_score（允许高分路径更新）
    visited: dict[str, float] = {source_id: 1.0}
    impacts: list[ImpactedNode] = []
    
    for depth in range(1, self._max_depth + 1):
        decay = self._decay_schedule.get(depth, 0.0)
        if decay == 0.0:
            break
        
        next_frontier: dict[str, tuple[float, list[str]]] = {}
        for node_id, (_, path_so_far) in frontier.items():
            # 根据 direction 查询邻居
            if direction == PropagationDirection.FORWARD:
                relations = self._graph_store.get_relations(target_id=node_id)
            else:
                relations = self._graph_store.get_relations(source_id=node_id)
            
            for rel in relations:
                # 确定邻居节点 ID
                neighbor_id = rel["source_id"] if direction == PropagationDirection.FORWARD else rel["target_id"]
                if neighbor_id == source_id:
                    continue  # 跳过变更源自身
                
                rel_type = rel["rel_type"].lower()
                score = self._compute_score(rel_type, change_type, depth)
                
                if score < self._impact_threshold:
                    continue
                
                # 允许高分路径更新已访问节点（MAX 策略）
                if neighbor_id in visited and score <= visited[neighbor_id]:
                    continue  # 已有更高或相等分数的路径
                
                visited[neighbor_id] = score
                new_path = path_so_far + [rel_type]
                
                node = self._graph_store.get_node(neighbor_id)
                if node:
                    impacts.append(ImpactedNode(
                        node_id=neighbor_id,
                        node_label=node.get("label", "Unknown"),
                        name=node.get("name", ""),
                        file_path=node.get("file_path"),
                        impact_score=score,
                        severity=self._classify_severity(score),
                        depth=depth,
                        direction=direction,
                        relation_path=new_path,
                        source_node_id=source_id,
                    ))
                    next_frontier[neighbor_id] = (score, new_path)
        
        frontier = next_frontier
        
        # 早停：如果 frontier 为空
        if not frontier:
            break
        # 早停：如果 decay × 最大权重 < threshold
        max_weight = max(
            max(weights.values()) for weights in self._weight_matrix.values()
        )
        next_decay = self._decay_schedule.get(depth + 1, 0.0)
        if next_decay * max_weight < self._impact_threshold:
            break
    
    return impacts

def _merge_impacts(self, impacts):
    """同节点同方向取 MAX score。不同方向独立保留。"""
    merged = {}
    for imp in impacts:
        key = (imp.node_id, imp.direction)
        if key not in merged or imp.impact_score > merged[key].impact_score:
            merged[key] = imp
    return sorted(merged.values(), key=lambda x: x.impact_score, reverse=True)
```

---

## 五、map_files_to_nodes 实现策略

ChangedFile.file_path → GraphStore 节点 ID 的映射：

```python
def map_files_to_nodes(self, changes):
    """将 ChangedFile 映射为图谱节点。
    
    file_path 规范：ChangedFile.path 为相对路径（相对于仓库根目录），
    Neo4j 中 file_path 统一存储为相对路径（由 builder.py 保证）。
    """
    result = {}
    skipped = []
    for change in changes:
        # Cypher: MATCH (n {file_path: $fp}) RETURN n.id, n.name, labels(n)
        nodes = self._graph_store.query(
            "MATCH (n {file_path: $fp}) RETURN n.id AS id, n.name AS name, labels(n) AS labels",
            {"fp": change.path}
        )
        node_ids = [n["id"] for n in nodes]
        if node_ids:
            result[change.path] = node_ids
        else:
            skipped.append(change.path)
            self._logger.debug("No graph nodes found for file: %s", change.path)
    
    if skipped:
        self._logger.info("Skipped %d files (no graph nodes): %s", len(skipped), skipped)
    return result
```

**边界处理**：
- file_path 在图谱中不存在（新文件或未构建）→ 跳过，记日志，不报错
- 一个 file_path 对应多个节点（文件含多个函数/类）→ 全部收录
- DELETED 文件特殊处理：
  - 图谱中有节点 → **产生 DELETED 影响传播**（权重矩阵中 DELETED 列权重最高）
  - 图谱中无节点 → 跳过（文件从未被解析过）
  - Day 8 阶段不删除图谱中的节点（节点删除由 Day 9 IncrementalUpdater 负责）
- ADDED 文件：图谱中无节点 → 跳过（新文件尚未构建入图谱，等 Day 9 处理）

---

## 六、TDD 任务列表（~45 tests）

> 每个任务 = 2-5 分钟。严格 RED-GREEN-REFACTOR。

### Task 1: PropagationDirection + ImpactSeverity 枚举 (4 tests)

**Files:** Create `src/layerkg/impact_propagator.py`, Create `tests/unit/test_impact_propagator.py`

- PropagationDirection 有 FORWARD/BACKWARD 两个值
- ImpactSeverity 有 CRITICAL/HIGH/MEDIUM/LOW 四个值
- ImpactSeverity 可从分数推导
- 枚举值类型正确（str）

### Task 2: ImpactedNode dataclass (5 tests)

- 创建完整 ImpactedNode（所有字段）
- impact_score 范围在 [0, 1]
- depth 必须 ≥ 1
- source_node_id 不能为空
- to_dict() 输出正确

### Task 3: WeightMatrix 默认值 + 查询 (5 tests)

- DEFAULT_WEIGHT_MATRIX 包含 8 种关系类型（6 活跃 + 2 占位）
- 每种关系有 ADDED/DELETED/SIGNATURE/BODY/DOC_ONLY 五个键
- 所有权重值在 [0, 1] 范围
- 未收录的关系类型（如 contains）返回 0.0
- **ADDED/DELETED 权重**：calls DELETED=1.0（最高），imports DOC_ONLY=0.0

### Task 4: DecaySchedule 默认值 (3 tests)

- DEFAULT_DECAY_SCHEDULE 包含 depth 1/2/3
- depth > 3 返回 0.0（停止传播）
- depth 0 不在 schedule 中

### Task 5: ImpactReport dataclass (5 tests)

- 创建带 impacted_nodes 的报告
- critical_count 属性正确
- affected_files 属性正确
- nodes_by_severity 分组正确
- to_dict() 序列化完整

### Task 6: _compute_score 内部方法 (7 tests)

- calls+SIGNATURE+depth=1 → 0.9 × 1.0 = 0.9
- imports+BODY+depth=2 → 0.3 × 0.6 = 0.18
- imports+DOC_ONLY+depth=1 → 0.0 × 1.0 = 0.0（不传播）
- describes+DOC_ONLY+depth=3 → 0.3 × 0.3 = 0.09
- 未知关系类型 → 0.0
- depth=4 → 0.0（超出 schedule）
- calls+DELETED+depth=1 → 1.0 × 1.0 = 1.0

### Task 7: _classify_severity (4 tests)

- 0.9 → CRITICAL
- 0.54 → HIGH
- 0.25 → MEDIUM
- 0.05 → LOW

### Task 8: 构造函数 + GraphStore mock (2 tests)

- 正常创建 ImpactPropagator（mock GraphStore）
- max_depth=0 → ValueError

### Task 9: map_files_to_nodes (4 tests)

- 单文件映射成功（mock query 返回节点）
- 多节点映射（一个文件多个函数/类）
- 文件不存在于图谱 → 返回空 dict（不报错）
- 多文件批量映射

### Task 10: _merge_impacts (5 tests)

- 同节点同方向取 MAX score
- 不同节点各自保留
- 同节点不同方向各自保留（因为方向不同代表不同的影响语义）
- 空列表 → 空列表
- **多源变更**：两个 source_node_id 影响同一节点，各自产生独立 ImpactedNode

### Task 11: 正向 BFS — 单跳传播 (5 tests)

- mock GraphStore 返回邻居，验证 ImpactedNode 字段
- weight × decay = 0 的关系不收录
- score < threshold 的节点不收录
- 循环引用不会无限遍历（visited 集合）
- **score 正好等于 threshold** → 收录（>= 语义）

### Task 12: 反向 BFS — 单跳传播 (3 tests)

- 反向查询 get_relations(source_id=node_id)
- 邻居是 rel 的 target_id
- ImpactedNode.direction = BACKWARD

### Task 13: 多跳 BFS — 深度衰减 (5 tests)

- depth=1 score > depth=2 score（衰减生效）
- depth=3 仍可传播（decay=0.3）
- depth=4 停止传播
- 早停：frontier 为空时终止
- **relation_path 多跳累积**：depth=2 的 relation_path 长度为 2

### Task 14: compute_impact 完整流程 (5 tests)

- 双向传播合并结果
- 变更源节点本身不在结果中
- ADDED 类型文件（图谱无节点）→ 空结果
- SIGNATURE 变更比 DOC_ONLY 传播更广
- **DELETED 变更**：图谱有节点时，产生 DELETED 传播（权重最高）

### Task 15: propagate 主入口集成 (5 tests)

- 完整流程：ChangedFile → map → BFS → ImpactReport
- propagation_time_ms > 0
- total_analyzed 正确计数
- **空图谱**：GraphStore 返回空 → ImpactReport.impacted_nodes 为空
- **空变更列表**：传入 [] → ImpactReport 所有计数为 0

### Task 16: __init__.py 导出更新 + ruff + commit

```bash
# 更新 __init__.py
# ruff check + format
git add src/layerkg/impact_propagator.py tests/unit/test_impact_propagator.py
git commit -m "feat(layerkg): add ImpactPropagator with bidirectional BFS impact propagation (Day 8)"
```

---

## 七、依赖与验证

### 依赖
- **仅 Python 标准库**：dataclasses, enum, collections.deque, time, logging
- **不引入新外部依赖**
- 通过 GraphStore ABC 抽象层访问图谱（测试用 mock，生产用 Neo4j）
- 测试使用 unittest.mock（Mock GraphStore）

### Mock GraphStore 约定

测试中 mock 的 GraphStore 需实现以下行为：

```python
mock_store = Mock(spec=GraphStore)
# get_relations 返回: [{"source_id": "...", "target_id": "...", "rel_type": "CALLS", "properties": {}}]
# get_node 返回: {"id": "...", "name": "...", "file_path": "...", "label": "CodeEntity"} 或 None
# query 返回: [{"id": "...", "name": "...", "labels": ["CodeEntity"]}]
```

### 验证清单
- [ ] `uv run pytest tests/unit/test_impact_propagator.py -v` → ~45 tests PASSED
- [ ] `uv run pytest tests/ -v` → 315 tests PASSED（270 + 45，不破坏现有）
- [ ] `uv run ruff check src/ tests/` → 无错误
- [ ] `uv run ruff format src/ tests/` → 格式化通过

### __init__.py 导出
在 `src/layerkg/__init__.py` 中添加：
```python
from layerkg.impact_propagator import (
    ImpactPropagator,
    ImpactReport,
    ImpactedNode,
    ImpactSeverity,
    PropagationDirection,
)
```

---

## 八、设计决策记录

| 决策 | 理由 |
|------|------|
| 纯 Python BFS（非 Cypher 变长路径） | 测试无需 Neo4j、与 GraphStore ABC 一致、算法核心不到 300 行 |
| MAX 聚合策略 | 学术文献一致推荐，防止热门节点分数膨胀 |
| 阶跃衰减 [1.0, 0.6, 0.3] | 代码图影响在 2-3 跳后急剧下降，业界验证 |
| 正向+反向独立保留 | 同一节点从不同方向到达代表不同影响语义 |
| threshold=0.05 早停 | 过滤噪声节点，减少无效遍历 |
| 关系类型大小写处理 | Neo4j 存储 UPPER_SNAKE（CALLS），查询时 .lower() 匹配矩阵 |
| 未收录关系类型权重 0 | contains/illustrates 等关系不参与影响传播（Day 8 阶段） |
| map_files_to_nodes 静默跳过+日志 | 新文件未入图谱是正常情况，不应中断流程，但需记录便于调试 |
| file_path 统一使用相对路径 | ChangedFile.path 和 Neo4j 中 file_path 都使用相对路径（builder.py 保证） |
| visited 使用 dict[str, float] 而非 set | 允许高分路径更新已访问节点，避免丢失高分路径（审核修复） |
| relation_path 记录完整路径 | 多跳时累积关系类型列表，如 ["calls", "imports"]（审核修复） |
| 权重矩阵包含 ADDED/DELETED 列 | ChangeType 全部 5 种类型都有对应权重（审核修复） |
| DELETED 产生最强传播 | DELETED 权重最高（calls DELETED=1.0），节点删除由 Day 9 负责 |
| derived_from/affects 占位为 0.0 | 明确标注"Phase 1+ 可能启用"，避免遗漏 |
