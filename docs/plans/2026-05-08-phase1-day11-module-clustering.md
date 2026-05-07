# Day 11: ModuleClustering + ConceptAligner Step 4 实现计划

> **For Hermes:** 使用 claude-code skill 的 print mode (`-p`) 执行任务。每个任务 2-5 分钟粒度。强制 TDD。详细规范见 `.claude/rules/` 目录。

**Goal:** 实现模块聚类器（社区发现算法）和概念对齐器图结构匹配（Step 4）。

---

## 一、前置分析

### 现有基础设施
| 组件 | 状态 | 位置 |
|------|------|------|
| `ModuleEntity` | ✅ schema 已定义 | `schema.py` |
| `contains` 关系 | ✅ 已定义 | `schema.py` VALID_RELATION_TYPES |
| `ConceptAligner` | ✅ Step 1-3 完成 | `aligner.py` |
| `Neo4jGraphStore` | ✅ 完整 CRUD | `neo4j_store.py` |
| `ChromaStore` | ✅ 向量搜索 | `chroma_store.py` |
| 测试基线 | ✅ 456 passed | 全量 |

### 设计决策
1. **算法选择**: Label Propagation — 纯 Cypher 可实现，无需额外依赖（Louvain 需要 GDS 插件或 Python 社区库）
2. **Step 4 降级**: neo4j_store 为 None 时跳过，保持向后兼容
3. **模块命名**: 基于聚类中最频繁出现的文件路径前缀自动生成

---

## 二、新增/修改文件

| 文件 | 类型 | 预估行数 |
|------|------|----------|
| `src/layerkg/module_clustering.py` | 新增 | ~200 |
| `src/layerkg/aligner.py` | 修改 | +60 行 |
| `tests/unit/test_module_clustering.py` | 新增 | ~400 |
| `tests/unit/test_aligner.py` | 修改 | +100 行 |

---

## 三、数据结构

### ModuleCluster（新增 dataclass）

```python
@dataclass
class ModuleCluster:
    """聚类结果中间数据结构。"""
    module: ModuleEntity
    entity_ids: list[str]       # 包含的 CodeEntity ID 列表
    cohesion: float             # 模块内聚度 [0, 1]
    entity_count: int           # 实体数量
```

### AlignResult 扩展
- `match_type` 新增 `"graph_structure"` 类型
- `VALID_MATCH_TYPES` 新增 `"graph_structure"`

---

## 四、ModuleClustering 接口设计

```python
class ModuleClustering:
    """模块聚类器：基于图结构的社区发现。
    
    使用 Neo4j 图遍历，通过 Label Propagation 算法
    将代码实体聚类为功能模块。
    """

    def __init__(
        self,
        neo4j_store: Neo4jGraphStore,
        algorithm: str = "label_propagation",  # 预留扩展
    ) -> None: ...

    def detect_modules(self) -> list[ModuleCluster]:
        """执行社区发现，返回模块聚类结果。
        
        步骤：
        1. 从 Neo4j 加载 CodeEntity 子图（calls/imports/extends 关系）
        2. 在内存中构建邻接表
        3. 执行 Label Propagation 算法
        4. 为每个聚类生成 ModuleEntity + 计算 cohesion
        """
        ...

    def save_modules(self, clusters: list[ModuleCluster]) -> int:
        """将聚类结果保存为 ModuleEntity + contains 关系。
        
        Returns: 保存的模块数量。
        """
        ...

    def get_module_tree(self) -> dict:
        """返回模块层次结构树。
        
        格式: {module_name: {"entities": [...], "cohesion": float}}
        """
        ...

    def _load_graph(self) -> tuple[dict[str, set[str]], dict[str, dict]]:
        """从 Neo4j 加载邻接表和实体数据。
        
        使用 neo4j_store.query() 执行 Cypher。
        
        Returns: (adj, entity_data)
            adj: {entity_id: {neighbor_id, ...}}
            entity_data: {entity_id: {name, file_path, ...}}
        
        Cypher:
            // 获取所有 CodeEntity
            MATCH (c:CodeEntity) RETURN c.id, c.name, c.file_path
            // 获取关系
            MATCH (c1:CodeEntity)-[r]->(c2:CodeEntity)
            WHERE type(r) IN ['CALLS', 'IMPORTS', 'EXTENDS', 'IMPLEMENTS']
            RETURN c1.id, c2.id
        """
        ...

    def _label_propagation(
        self,
        adj: dict[str, set[str]],
        max_iterations: int = 100,
    ) -> dict[str, str]:
        """Label Propagation 算法（确定性版本）。
        
        关键：使用 sorted() 确保节点遍历顺序和邻居选择顺序确定。
        增加收敛检查：labels 不变时提前退出。
        
        Args:
            adj: 邻接表 {node_id: {neighbor_ids}}
            max_iterations: 最大迭代次数
            
        Returns: {entity_id: community_label}
        
        Algorithm:
            1. 初始化：每个节点的标签 = 自身 id
            2. 每轮迭代：
               a. sorted(adj.keys()) 确定性遍历
               b. 对每个节点，sorted(neighbors) 统计邻居标签
               c. 选择出现次数最多的标签（平局选字典序最小的）
               d. labels == old_labels → 收敛退出
            3. 返回最终 labels
        """
        ...

    def _compute_cohesion(
        self,
        entity_ids: list[str],
        adj: dict[str, set[str]],
    ) -> float:
        """计算模块内聚度。
        
        cohesion = 模块内边数 / 最大可能边数(n*(n-1)/2)
        
        除零保护：n <= 1 时返回 0.0（单节点无内聚可言）
        """
        ...

    def _generate_module_name(
        self,
        entity_ids: list[str],
        entity_data: dict[str, dict],
    ) -> str:
        """根据聚类内容生成模块名称。
        
        策略：
        1. 收集所有 file_path（过滤 None）
        2. 计算 os.path.commonpath → 取最后一段作为名称
        3. 无 file_path → 降级为 "module_{N}"
        4. 空列表 → "module_0"
        """
        ...
```

---

## 五、ConceptAligner Step 4 接口扩展

```python
# aligner.py 修改：
# 1. ConceptAligner.__init__ 新增参数：
#    neo4j_store: Neo4jGraphStore | None = None
#    graph_overlap_threshold: float = 0.8
# 2. 新增 _graph_structure_match(term) -> AlignResult | None
# 3. align() 方法中 Step 3 后调用 Step 4
# 4. VALID_MATCH_TYPES 新增 "graph_structure"

def _graph_structure_match(self, term: str) -> AlignResult | None:
    """Step 4: 图结构匹配。
    
    算法：
    1. 降级检查：neo4j_store 为 None → 返回 None
    2. 批量加载所有概念-代码关系（一次 Cypher 查询）：
       MATCH (concept:ConceptEntity)-[:DERIVED_FROM|SEMANTIC_IMPACT]-(code:CodeEntity)
       RETURN concept.name, collect(DISTINCT code.id) as code_ids
       → 缓存为 self._concept_code_map: dict[str, set[str]]
    3. 查询 term（作为 CodeEntity name）关联的 CodeEntity：
       MATCH (c:CodeEntity {name: $term})-[*1..2]-(other:CodeEntity)
       RETURN DISTINCT other.id
    4. 对每个已知 Concept，计算 Jaccard 重叠度 = |交集| / |并集|
    5. **空集保护**：union 为空 → 跳过（不匹配）
    6. 返回重叠度最高且 > graph_overlap_threshold 的 Concept
    """
    ...
```

---

## 六、TDD 任务分解（15 tasks, ~31 tests）

### Part A: ModuleClustering（10 tasks, ~23 tests）

#### Task 1: ModuleCluster dataclass（2 tests）
- 测试创建 ModuleCluster（正常）
- 测试 entity_count 与 entity_ids 长度一致

#### Task 2: ModuleClustering 构造函数（2 tests）
- 测试正常初始化（neo4j_store + algorithm）
- 测试不支持算法抛 ValueError

#### Task 3: _load_graph — 从 Neo4j 加载邻接表（3 tests）
- 测试正常加载（mock Neo4j 返回 3 个实体 + 关系）
- 测试空图（无 CodeEntity）
- 测试孤立节点（无关系）

#### Task 4: _label_propagation 核心算法（5 tests）
- 测试简单图（3 个连通节点 → 1 个社区）
- 测试两个分离的组件（6 节点 2 边 → 2 个社区）
- 测试空图返回空 dict
- 测试收敛性（固定种子确保确定结果，2 轮迭代后收敛）
- 测试孤立节点（无邻居 → 保持自身标签）

#### Task 5: _compute_cohesion 内聚度（3 tests）
- 测试完全连通图 → 1.0
- 测试链式图（1-2-3）→ 2/3 ≈ 0.67
- 测试孤立节点 → 0.0

#### Task 6: _generate_module_name 模块命名（3 tests）
- 测试公共前缀生成名称（src/layerkg/parser/*.py → "parser"）
- 测试无公共前缀 → "module_N"
- 测试空列表 → "module_0"

#### Task 7: detect_modules 完整流程（2 tests）
- mock _load_graph + _label_propagation + _compute_cohesion
- 验证返回 list[ModuleCluster]，包含正确的 ModuleEntity

#### Task 8: save_modules 保存到 Neo4j（2 tests）
- mock neo4j_store.merge_node + merge_relation
- 验证 ModuleEntity 节点 + contains 关系被正确创建

#### Task 9: get_module_tree 层次结构（2 tests）
- 测试返回正确嵌套 dict
- 测试空模块列表 → {}

#### Task 10: ModuleClustering 边界测试（1 test）
- 单节点图 → 1 个模块

### Part B: ConceptAligner Step 4（5 tasks, ~8 tests）

#### Task 11: ConceptAligner 构造函数扩展（1 test）
- 新增 neo4j_store + graph_overlap_threshold 参数
- neo4j_store=None 时向后兼容（现有测试不受影响）

#### Task 12: _graph_structure_match 核心逻辑（3 tests）
- mock Neo4j 返回关联 CodeEntity
- 测试 Jaccard > 0.8 → 匹配成功
- 测试 Jaccard < 0.8 → 返回 None
- neo4j_store=None → 返回 None

#### Task 13: align() 集成 Step 4（2 tests）
- Step 1-3 未匹配 + Step 4 匹配 → match_type="graph_structure"
- Step 1-3 已匹配 → 不调用 Step 4

#### Task 14: VALID_MATCH_TYPES 更新（1 test）
- "graph_structure" in VALID_MATCH_TYPES

#### Task 15: Step 4 边界测试（1 test）
- term 无关联 CodeEntity → NO_MATCH

---

## 七、实现顺序

```
Part A: ModuleClustering（独立新模块）
  Task 1 (ModuleCluster) → Task 2 (构造) → Task 3 (_load_graph) → 
  Task 4 (_label_propagation) → Task 5 (_cohesion) → Task 6 (_name) →
  Task 7 (detect_modules) → Task 8 (save_modules) → Task 9 (get_module_tree) →
  Task 10 (边界)

Part B: ConceptAligner Step 4（扩展现有模块）
  Task 11 (构造扩展) → Task 12 (_graph_structure_match) → 
  Task 13 (align集成) → Task 14 (VALID_MATCH_TYPES) → Task 15 (边界)
```

---

## 八、Neo4j Cypher 查询

### _load_graph
```cypher
// 获取所有 CodeEntity
MATCH (c:CodeEntity) RETURN c.id, c.name, c.file_path

// 获取 CodeEntity 之间的关系（calls, imports, extends, implements）
MATCH (c1:CodeEntity)-[r]->(c2:CodeEntity)
WHERE type(r) IN ['CALLS', 'IMPORTS', 'EXTENDS', 'IMPLEMENTS']
RETURN c1.id, c2.id
```

### _graph_structure_match
```cypher
// 获取 term 关联的 CodeEntity（2 跳以内）
MATCH (c:CodeEntity {name: $term})-[*1..2]-(other:CodeEntity)
RETURN DISTINCT other.id

// 获取 Concept 关联的 CodeEntity
MATCH (concept:ConceptEntity {name: $concept_name})-[:DERIVED_FROM|SEMANTIC_IMPACT]-(code:CodeEntity)
RETURN DISTINCT code.id
```

---

## 九、Claude Code 执行指导

### Part A 批次
**Batch 1（Task 1-6）**: 数据结构 + 内部方法（无外部依赖，纯 mock）
- 创建 `src/layerkg/module_clustering.py` + `tests/unit/test_module_clustering.py`
- ~17 tests

**Batch 2（Task 7-10）**: 对外方法 + 边界测试
- detect_modules / save_modules / get_module_tree
- ~5 tests

### Part B 批次
**Batch 3（Task 11-15）**: ConceptAligner 扩展
- 修改 `src/layerkg/aligner.py` + 扩展 `tests/unit/test_aligner.py`
- ~8 tests

### 检查点
每批次完成后：
1. `uv run pytest tests/unit/ -v` — 所有测试通过
2. `uv run ruff check src/ tests/` — 无 lint 错误
3. 确认不破坏已有 456 个测试

---

## 十、文件导入关系

```python
# module_clustering.py
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.schema import ModuleEntity

# aligner.py 新增
from layerkg.neo4j_store import Neo4jGraphStore  # 可选依赖
```

---

## 十一、风险与缓解

| 风险 | 缓解 |
|------|------|
| Label Propagation 不确定性 | 固定随机种子（sorted + hash），确保测试可重复 |
| 空图 / 单节点 | 边界测试覆盖 |
| Step 4 破坏现有 aligner 测试 | neo4j_store 默认 None，不改变现有行为 |
| Jaccard 分母为 0 | 特殊处理：两者都无关联 CodeEntity → 返回 None |
