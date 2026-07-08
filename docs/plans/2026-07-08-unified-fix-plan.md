# OntoAgent 统一修复计划

> **日期**: 2026-07-08
> **基线**: commit `5e32bf4`，1714 tests passing，21K LOC，114 files
> **审核问题**: P0×8(已完成) + P1×6(全真) + P2×8(全真) = **14 项待修**

---

## 总览

| Phase | 名称 | 问题编号 | 改动范围 | 预计删除 | 风险 |
|-------|------|---------|---------|---------|------|
| **1** | 死代码清除 | P1-1, P2-4(死), P2-8(死测试) | execution/ + tests/ | ~2800 行 | 低 |
| **2** | ActionExecutor 公共 API | P1-3 | action_executor.py + tools.py | 0 | 低 |
| **3** | Schema 检查安全 | P2-5 | builder.py | 0 | 低 |
| **4** | API 层不绕过 Agent | P1-4 | chat.py | 0 | 中 |
| **5** | build() 拆分 | P2-1 | builder.py | 0 | 中 |
| **6** | Parser DRY | P2-3 | base.py + parsers | ~80 行 | 中 |
| **7** | 清理收尾 | P2-2, P2-6, P2-7, P1-6, P1-5 | 多文件 | ~100 行 | 低 |
| **延后** | v0.3 重构项 | P1-2 | — | — | — |

每个 Phase 完成后：**全量回归测试 + commit**。

---

## Phase 1: 死代码清除（最高优先级）

### 1.1 删除死代码源文件

以下文件/目录**零外部引用**（已 grep 确认）：

| 文件 | 行数 | 状态 |
|------|------|------|
| `execution/saga.py` | 134 | 零引用 |
| `execution/dag_orchestrator.py` | 380 | 零引用 |
| `execution/transaction_manager.py` | 97 | 零引用 |
| `execution/planner/` (4 files) | 485 | 零引用 |
| `execution/reasoner/` (2 files) | 112 | 零引用 |
| `execution/connectors/` (3 files) | 76 | 零引用 |
| **小计** | **1284** | |

### 1.2 处理 constraints/ 死代码（需谨慎）

| 文件 | 行数 | 外部引用 | 处理 |
|------|------|---------|------|
| `constraints/engine.py` | 198 | 仅 `__init__.py` 导出 | **删除** |
| `constraints/propagator.py` | 162 | `loader.py:7` 导入 `PropagationRule` | **先迁移 PropagationRule → loader.py，再删除** |
| `constraints/__init__.py` 导出 | — | 删除 ConstraintEngine/Propagator/PropagationResult/PropagationRule/aggregate_levels | **清理导出** |

> **注意**: `aggregate_levels()` 函数（`__init__.py:20-40`）被注释引用"ConstraintEngine and ConstraintPropagator"，需确认是否被其他代码使用。grep 确认：仅 `__init__.py` 自身引用 → **删除**。

### 1.3 删除死代码测试

| 测试文件 | 行数 | 测试目标 |
|---------|------|---------|
| `tests/unit/execution/test_saga.py` | 230 | saga.py |
| `tests/unit/test_dag_orchestrator.py` | 197 | dag_orchestrator.py |
| `tests/unit/test_dag_compensation.py` | 198 | dag_orchestrator.py 补偿 |
| `tests/unit/execution/test_transaction_manager.py` | 158 | transaction_manager.py |
| `tests/unit/test_planner.py` | 128 | planner/ |
| `tests/unit/test_capability_reasoner.py` | 180 | reasoner/ |
| `tests/unit/execution/test_connector_registry.py` | 59 | connectors/ |
| `tests/unit/execution/test_mock_connector.py` | 30 | connectors/ |
| `tests/evaluation/planner_eval.py` | ? | planner/ |
| `tests/evaluation/test_planner_eval.py` | ? | planner/ |
| **小计** | **~1180** | |

### 1.4 修复活代码中的危险 except+pass

| 文件:行 | 问题 | 修复 |
|---------|------|------|
| `agent/trace.py:98` | SQLite 加载 trace 失败静默 | 加 `logger.warning("Failed to load trace history: %s", e)` |

> `dag_orchestrator.py:264` 和 `planner/bridge.py:66,78` 的危险 except+pass **随死代码删除一并消除**。

### 1.5 更新 CLAUDE.md / 文档

- 移除对 SAGA/DAG/TransactionManager/Planner/Reasoner/Connectors 的任何文档引用
- 更新架构图：Control 层和 Capability 层标注为"精简版（预留扩展点）"

### 1.6 回归验证

```bash
# 全量测试
pytest tests/ -x -q

# 预期：测试数下降（删除了 ~40 个死代码测试），
# 但 passing rate = 100%
```

### Phase 1 预计净效果
- **删除 ~2480 行死代码**（1284 源 + 360 constraints + ~1180 测试 - 少量 __init__ 修改）
- 项目从 21K → ~18.5K LOC
- 114 → ~100 files

---

## Phase 2: ActionExecutor 公共 API（P1-3）

### 问题
`tools.py` 直接访问 `ActionExecutor` 的 3 个私有成员：
- `executor._resolve_entity(target)` — L452, L570
- `executor._shape_registry` — L462, L563
- `executor._check_with_shapes(entity, config)` — L586

### 修复

**action_executor.py** — 添加公共方法/属性：

```python
# 将 _resolve_entity 改为 resolve_entity（删除下划线），_resolve_entity 保留为别名
def resolve_entity(self, target: str) -> dict | None:
    """公共 API：解析实体名称为实体字典。"""
    return self._resolve_entity(target)

@property
def shape_registry(self):
    """公共 API：返回 ShapeRegistry 或 None。"""
    return self._shape_registry

def check_with_shapes(self, entity: dict, config: ActionConfig) -> tuple[str, list[str]]:
    """公共 API：运行 Shape 约束检查。"""
    return self._check_with_shapes(entity, config)
```

**tools.py** — 替换所有 `executor._xxx` 为 `executor.xxx`：
- L452: `executor._resolve_entity` → `executor.resolve_entity`
- L462: `executor._shape_registry` → `executor.shape_registry`
- L563: `executor._shape_registry` → `executor.shape_registry`
- L570: `executor._resolve_entity` → `executor.resolve_entity`
- L586: `executor._check_with_shapes` → `executor.check_with_shapes`

### 回归验证
```bash
pytest tests/ -x -q
```

---

## Phase 3: Schema 检查安全（P2-5）

### 问题
`builder.py:520-532`：Schema 版本检查的 try-except 把所有异常（含 `OntoAgentError` = schema AHEAD）都降级为 debug log。

### 修复

```python
# builder.py L516-532 改为：
try:
    status = get_schema_status(graph_store)
    if status in (SchemaStatus.BEHIND, SchemaStatus.EMPTY):
        registry = MigrationRegistry()
        runner = MigrationRunner(graph_store, registry)
        applied = runner.run_pending()
        if applied:
            self._logger.info("Auto-applied %d schema migrations: %s", len(applied), applied)
    elif status == SchemaStatus.AHEAD:
        db_ver = get_current_db_version(graph_store)
        raise OntoAgentError(
            f"Database schema ({db_ver}) is ahead of code ({CURRENT_SCHEMA_VERSION}). "
            "Please update OntoAgent."
        )
except OntoAgentError:
    raise  # Schema 不一致必须中止
except Exception as e:
    # 连接不可用等非 schema 问题可以降级
    self._logger.debug("Schema version check skipped (store unavailable): %s", e)
```

> **关键**: `OntoAgentError` 先捕获并 re-raise，确保 schema 不一致不被吞。

### 回归验证
```bash
pytest tests/ -x -q
```

---

## Phase 4: API 层不绕过 Agent（P1-4）

### 问题
`chat.py:158-165`：审批端点直接 `Neo4jGraphStore(...)` + `_get_action_executor(graph_store)`，绕过连接池。

### 修复方案

在 `agent/tools.py` 中提取共享初始化函数（已有 `_get_action_executor`），让 `chat.py` 复用：

```python
# chat.py L156-165 改为：
from ontoagent.agent.tools import _get_action_executor, _get_shared_graph_store

graph_store = _get_shared_graph_store()  # 复用 tools.py 的单例
executor = _get_action_executor(graph_store)
```

在 `tools.py` 中添加 `_get_shared_graph_store()`：

```python
_GRAPH_STORE: Neo4jGraphStore | None = None

def _get_shared_graph_store() -> Neo4jGraphStore:
    """获取或初始化共享 GraphStore 单例。"""
    global _GRAPH_STORE
    if _GRAPH_STORE is not None:
        return _GRAPH_STORE
    import os
    from ontoagent.store.neo4j_store import Neo4jGraphStore
    uri = os.environ.get("ONTOAGENT_NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("ONTOAGENT_NEO4J_USER", "neo4j")
    password = os.environ.get("ONTOAGENT_NEO4J_PASSWORD", "")
    _GRAPH_STORE = Neo4jGraphStore(uri=uri, user=user, password=password)
    return _GRAPH_STORE
```

> **CLI 绕过**（cli.py 调 builder.query/info）不修——CLI 本就是直接调用 builder 的入口，设计如此。

### 回归验证
```bash
pytest tests/ -x -q
```

---

## Phase 5: build() 拆分（P2-1）

### 问题
`build()` L479-830 = 351 行，混合编排和执行。

### 拆分方案

```python
def build(self, repo_path, *, skip_semantic=False, skip_clustering=False, clear=False) -> BuildResult:
    """全量构建：5阶段流水线编排（仅编排逻辑）。"""
    # --- 0. Schema 检查 ---
    self._check_and_migrate_schema()       # ~15 行

    t0 = time.monotonic()
    all_errors = []

    # --- 1. Pre-build ---
    if clear:
        self._pre_build_clear()             # ~15 行

    # --- 2. 解析 ---
    code_entities, doc_entities = self._stage_parse(repo_path, ...)  # 已有

    # --- 3. 结构写入 ---
    self._stage_write_structural(...)       # 已有

    # --- 4. Service/Topic + 业务本体 ---
    self._write_service_topic_entities(...) # ~87 行 → 提取
    self._write_business_ontology(...)      # ~50 行 → 提取

    # --- 5. 语义+聚类 ---
    if not skip_semantic:
        self._stage_semantic_clustering(...)  # ~30 行 → 提取

    elapsed = int((time.monotonic() - t0) * 1000)
    return BuildResult(...)
```

新增方法：
- `_check_and_migrate_schema()` — 从 L506-532 提取
- `_pre_build_clear()` — 从 L538-549 提取
- `_write_service_topic_entities()` — 从 L587-674 提取（87 行内联）
- `_write_business_ontology()` — 从 L697-748 提取
- `_stage_semantic_clustering()` — 从 L768-798 提取

build() 缩减到 ~80 行纯编排。

### 回归验证
```bash
pytest tests/ -x -q
```

---

## Phase 6: Parser DRY（P2-3）

### 问题
`python_parser.py` 和 `java_parser.py` 的 `parse_source()` 结构几乎相同。

### 修复

**base.py BaseParser** — 添加模板方法：

```python
class BaseParser(ABC):
    def parse_source(self, source: bytes, file_path: str = "<string>") -> ParseResult:
        """模板方法：解析源码的公共骨架。"""
        entities: list[CodeEntity] = []
        relations: list[ExtractedRelation] = []

        # 公共：创建文件实体
        root_entity = self._create_root_entity(source, file_path)
        entities.append(root_entity)

        try:
            tree = self._parser.parse(source)
            root_node = tree.root_node
            module_name = self._get_root_name(file_path)

            # 子类扩展点：预扫描（Java 的 package）
            module_name = self._pre_scan(root_node, source, file_path, entities, relations) or module_name

            # 递归遍历
            self._walk(root_node, source, file_path, entities, relations, module_name)

            # 外部调用提取
            relations.extend(self._extract_external_calls(root_node, source, file_path, module_name))

        except Exception as e:
            self._on_parse_error(file_path, e)

        return ParseResult(file_path=file_path, entities=entities, relations=relations, language=self.language)

    @abstractmethod
    def _create_root_entity(self, source: bytes, file_path: str) -> CodeEntity: ...

    @abstractmethod
    def _get_root_name(self, file_path: str) -> str: ...

    def _pre_scan(self, root_node, source, file_path, entities, relations) -> str | None:
        """子类可选覆写。"""
        return None

    @abstractmethod
    def _extract_external_calls(self, root_node, source, file_path, module_name) -> list[ExtractedRelation]: ...

    def _on_parse_error(self, file_path: str, error: Exception) -> None:
        """子类可选覆写。默认静默。"""
        pass
```

**python_parser.py** / **java_parser.py** — 只实现抽象方法，删除重复的 `parse_source()` 骨架。

### 回归验证
```bash
pytest tests/ -x -q
```

---

## Phase 7: 清理收尾

### 7.1 butler/skills/store.py 代码去重（P2-2）

```python
_SKILL_COLUMNS = "skill_id, name, layer, pattern_json, action_json, confidence, source, status, hit_count, version, parent_id, created_at, updated_at"

def _row_to_skill(self, row) -> SkillEntity:
    return SkillEntity(
        skill_id=row[0], name=row[1], layer=SkillLayer(row[2]),
        pattern=json.loads(row[3]), action=json.loads(row[4]),
        confidence=row[5], source=row[6], status=row[7],
        hit_count=row[8], version=row[9], parent_id=row[10],
        created_at=row[11], updated_at=row[12],
    )
```

替换 4 处 SkillEntity 构造 + 5 处 SELECT 列名。

### 7.2 except Exception 细化（P2-6，分批）

**高优先级**（活代码核心路径）：
- `tools.py` 的 8 处 → 细化为具体异常
- `graph.py` 的 3 处 → 细化

**低优先级**（边界/CLI）：
- `cli.py`、`chat.py` 等 → 可后续处理

### 7.3 错误消息英文化（P2-7）

| 文件:行 | 当前 | 改为 |
|---------|------|------|
| graph.py:145 | `"Agent 工具调用次数超限..."` | `"Agent tool call limit exceeded. Try a more specific entity name."` |
| tools.py:280 | `"获取模块树失败"` | `"Failed to get module tree"` |
| tools.py 其他 | 逐步英文化 | — |

> 用户提示（返回给前端的 JSON）可保留中文。

### 7.4 移动 ConstraintFieldDescriptor（P1-6）

将 `ConstraintFieldDescriptor` 从 `ontology_constraints.py` 移到 `constraints.py`，消除 schema.py ↔ ontology_constraints.py 的循环依赖，删除 schema.py:881 的延迟 import。

### 7.5 butler 标注实验性（P1-5）

- `butler/__init__.py` 添加 docstring：`"Experimental: automated build/update orchestration. API may change."`
- README 中 butler 功能标注 `[实验性]`
- 不删除（有 CLI 入口 `ontoagent butler serve`）

---

## 延后到 v0.3（不修，仅记录）

| 问题 | 原因 |
|------|------|
| **P1-2** builder.py 上帝类（1276行） | 需要大规模拆分为 Builder/Writer/Extractor/Clusterer 多个类，风险高，留给 v0.3 |
| **P1-5** butler/ 深度重构 | 实验性功能，不影响核心链路 |

---

## 执行顺序与验证策略

```
Phase 1 (死代码) → pytest → commit
  ↓
Phase 2 (公共API) → pytest → commit
  ↓
Phase 3 (Schema安全) → pytest → commit
  ↓
Phase 4 (API不绕过) → pytest → commit
  ↓
Phase 5 (build拆分) → pytest → commit
  ↓
Phase 6 (Parser DRY) → pytest → commit
  ↓
Phase 7 (清理收尾) → pytest → commit → push
```

**每个 Phase 独立 commit**，方便回滚。

**验证标准**：每个 Phase 完成后全量 `pytest tests/ -x -q` 必须 100% 通过。

---

## 预期最终效果

| 指标 | 当前 | 目标 | 变化 |
|------|------|------|------|
| 源代码 LOC | 21,082 | ~18,500 | -2,582 |
| 文件数 | 114 | ~98 | -16 |
| 死代码 | ~1,644 行 | 0 | 消除 |
| 私有方法访问 | 5 处 | 0 | 消除 |
| 危险 except+pass | 3 处（活代码 1） | 0 | 消除 |
| Schema 安全 bug | 1 | 0 | 消除 |
| 测试数 | 1714 | ~1670 | -44（删死代码测试） |
| Passing rate | 100% | 100% | 不变 |
