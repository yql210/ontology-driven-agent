# Phase 5 v3：三层本体约束架构实施计划

> **For Hermes:** 使用 subagent-driven-development skill 逐个任务实施。代码由 delegate_task 子代理执行（Claude Code API 限额未重置）。

**目标：** 将约束规则从"手工 YAML"升级为"本体定义 + YAML 覆盖"三层架构，让本体定义成为约束的单一事实源。

**架构：** Layer 1（本体自动推导）→ Layer 2（YAML 覆盖）→ Layer 3（运行时确认）。优先级 Layer 3 > Layer 2 > Layer 1。

**技术栈：** Python 3.13 · pytest · PyYAML · Neo4j

---

## 核心设计

### 现状：两层分离

```
手工 YAML (value_mapping 完整配置) → ConstraintEngine → Guard 拦截
schema.py (DataAsset.sensitivity) — 定义存在但完全未被约束消费
```

### 目标：本体即约束源

```
schema.py (DataAsset.OntoConstraints.constraint_fields)
  │                              ↓
  │                YAML (仅遍历路径，无 value_mapping)
  │                     ↓
  └────→ ConstraintLoader.merge() → ConstraintEngine → Guard 拦截
                           ↑
              constraint_overrides.yaml (patch/allow_all)
```

**变更实质：** `value_mapping` 从 YAML 搬到 `schema.py` 的 `OntoConstraints` 类属性中。YAML 只保留遍历路径配置。

### 三层优先级（越上层越权威）

| 层 | 来源 | 语义 | 示例 |
|----|------|------|------|
| L1 自动推导 | `schema.py` OntoConstraints | 本体定义的默认约束 | `sensitivity=restricted → block` |
| L2 覆盖 | `constraint_overrides.yaml` | 人工微调 | 某函数白名单允许操作 restricted 数据 |
| L3 运行时 | Agent + 用户确认 | 临时放行 | 本次会话允许某操作 |

合并逻辑：
```
最终约束 = L1自动推导 ∪ L2覆盖补丁
```
- `patch`: 局部修改（add/remove/modify）
- `allow_all`: 完全放行（白名单）
- `add_constraint`: 叠加额外约束

---

## 文件变更清单

| 操作 | 文件 | 变更量 |
|------|------|--------|
| **修改** | `domain/schema.py` | +40 行：给 DataAsset、ComplianceItem、CodeEntity 加 `OntoConstraints` |
| **新建** | `domain/ontology_constraints.py` | ~80 行：`ConstraintFieldDescriptor` + `OntoConstraints` + `derive_constraint()` |
| **新建** | `execution/constraints/loader.py` | ~100 行：`OntologyConstraintLoader` 加载本体 + YAML 遍历 + 合并 |
| **修改** | `pipeline/constraints.yaml` | -30 行：移除 `value_mapping` 字段，保留遍历路径 |
| **新建** | `config/constraint_overrides.yaml` | ~30 行：覆盖配置模板（含注释示例） |
| **修改** | `agent/tools.py:_get_action_executor()` | -40/+20 行：用 `OntologyConstraintLoader` 替代手工 YAML 解析 |
| **修改** | `execution/constraints/__init__.py` | +3 行：导出 `OntologyConstraintLoader` |
| **新建** | `tests/unit/test_ontology_constraint_loader.py` | ~120 行：15 个测试 |
| **修改** | `tests/unit/test_constraint_engine.py` | ±20 行：适配新 loader |
| **修改** | `tests/integration/test_constraint_integration.py` | ±10 行：适配 |

**总计：** 5 新建 + 5 修改，~400 行新增代码，~70 行删除

---

## 任务分解

### Task 1: 创建 `domain/ontology_constraints.py` — 约束字段描述符

**目标：** 定义 `ConstraintFieldDescriptor` 和 `OntoConstraints` 基类，提供 `derive_constraint()` 工厂函数。

**文件：** 
- 创建：`src/ontoagent/domain/ontology_constraints.py`
- 修改：`tests/unit/test_ontology_constraint_loader.py`（Task 3 中写测试）

**实现：**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ontoagent.domain.constraints import GuardLevel, TraversalConstraint


@dataclass
class ConstraintFieldDescriptor:
    """描述实体字段上的约束语义。

    Attributes:
        field_name: 字段名 (e.g. "sensitivity")
        value_mapping: 字段值 → 约束级别的映射
    """
    field_name: str
    value_mapping: dict[str, GuardLevel]

    def derive_traversals(
        self,
        entity_label: str,
        paths: list[_ConstraintPath],
    ) -> list[TraversalConstraint]:
        """从本体字段定义 + 遍历路径生成完整 TraversalConstraint 列表。"""
        constraints: list[TraversalConstraint] = []
        for path in paths:
            constraints.append(
                TraversalConstraint(
                    name=path.name,
                    source_label=path.source_label,
                    relation_chain=path.relation_chain,
                    target_label=entity_label,
                    collect_property=self.field_name,
                    value_mapping=self.value_mapping,
                    aggregation=path.aggregation,
                )
            )
        return constraints


@dataclass
class _ConstraintPath:
    """遍历路径定义 — 约束引擎需要知道从哪里开始沿什么关系走到目标实体。

    Attributes:
        name: 约束名称
        source_label: 起始实体标签
        relation_chain: 关系链
        aggregation: 聚合策略
    """
    name: str
    source_label: str
    relation_chain: list[str]
    aggregation: str = "max"


@dataclass
class OntoConstraints:
    """实体类上的约束声明。放在 dataclass 内部作为类属性。

    Usage:
        @dataclass
        class DataAsset:
            ...
            OntoConstraints = OntoConstraints(
                entity_label="DataAsset",
                constraint_fields=[
                    ConstraintFieldDescriptor(
                        field_name="sensitivity",
                        value_mapping={
                            "restricted": GuardLevel.BLOCK,
                            "confidential": GuardLevel.WARN,
                            "internal": GuardLevel.ALLOW,
                            "public": GuardLevel.ALLOW,
                        },
                    )
                ],
            )
    """
    entity_label: str
    constraint_fields: list[ConstraintFieldDescriptor] = field(default_factory=list)

    def all_constraint_fields(self) -> dict[str, ConstraintFieldDescriptor]:
        """返回 {field_name: ConstraintFieldDescriptor} 映射。"""
        return {cf.field_name: cf for cf in self.constraint_fields}
```

**验证方式：** 
```bash
uv run pytest tests/unit/test_ontology_constraint_loader.py -v  # Task 3 中创建
```

**提交：**
```bash
git add src/ontoagent/domain/ontology_constraints.py
git commit -m "feat: add ontology constraint descriptors (ConstraintFieldDescriptor, OntoConstraints)"
```

---

### Task 2: 给 schema.py 的实体类添加 OntoConstraints

**目标：** 在 DataAsset、ComplianceItem、CodeEntity 三个实体类上添加 `OntoConstraints` 类属性。

**文件：** 修改 `src/ontoagent/domain/schema.py`

**修改：**

给 DataAsset 类（在 `class DataAsset:` 内部，`VALID_DATA_TYPES` 之后，`__post_init__` 之前）：

```python
    from ontoagent.domain.constraints import GuardLevel
    from ontoagent.domain.ontology_constraints import ConstraintFieldDescriptor, OntoConstraints

    OntoConstraints = OntoConstraints(
        entity_label="DataAsset",
        constraint_fields=[
            ConstraintFieldDescriptor(
                field_name="sensitivity",
                value_mapping={
                    "restricted": GuardLevel.BLOCK,
                    "confidential": GuardLevel.WARN,
                    "internal": GuardLevel.ALLOW,
                    "public": GuardLevel.ALLOW,
                },
            )
        ],
    )
```

给 ComplianceItem 类：

```python
    from ontoagent.domain.constraints import GuardLevel
    from ontoagent.domain.ontology_constraints import ConstraintFieldDescriptor, OntoConstraints

    OntoConstraints = OntoConstraints(
        entity_label="ComplianceItem",
        constraint_fields=[
            ConstraintFieldDescriptor(
                field_name="severity",
                value_mapping={
                    "critical": GuardLevel.BLOCK,
                    "high": GuardLevel.WARN,
                    "medium": GuardLevel.ALLOW,
                    "low": GuardLevel.ALLOW,
                },
            )
        ],
    )
```

给 CodeEntity 类：

```python
    from ontoagent.domain.constraints import GuardLevel
    from ontoagent.domain.ontology_constraints import ConstraintFieldDescriptor, OntoConstraints

    OntoConstraints = OntoConstraints(
        entity_label="CodeEntity",
        constraint_fields=[
            ConstraintFieldDescriptor(
                field_name="entry_category",
                value_mapping={
                    "http_api": GuardLevel.WARN,
                    "rpc_service": GuardLevel.WARN,
                    "scheduled": GuardLevel.ALLOW,
                    "mq_consumer": GuardLevel.ALLOW,
                    "event_handler": GuardLevel.ALLOW,
                },
            )
        ],
    )
```

注意：由于这些类在 `__post_init__` 中引用 `VALID_*` 常量，`OntoConstraints` 必须放在类体内但在 `__post_init__` 之前（或之后，只要不干扰 dataclass 字段）。`OntoConstraints` 不是 dataclass 字段（是裸类属性），放在 `VALID_*` 常量旁边即可。

**验证方式：**
```bash
uv run python -c "
from ontoagent.domain.schema import DataAsset, ComplianceItem, CodeEntity
assert hasattr(DataAsset, 'OntoConstraints')
assert DataAsset.OntoConstraints.constraint_fields[0].field_name == 'sensitivity'
assert DataAsset.OntoConstraints.constraint_fields[0].value_mapping['restricted'].value == 'block'
print('OK')
"
```

**提交：**
```bash
git add src/ontoagent/domain/schema.py
git commit -m "feat: add OntoConstraints to DataAsset, ComplianceItem, CodeEntity"
```

---

### Task 3: 创建 `execution/constraints/loader.py` — 本体约束加载器

**目标：** 创建 `OntologyConstraintLoader`，从本体内省 + YAML 遍历路径 + 覆盖文件，生成最终 `TraversalConstraint` 列表。

**文件：** 
- 创建：`src/ontoagent/execution/constraints/loader.py`
- 创建：`tests/unit/test_ontology_constraint_loader.py`

**核心接口：**

```python
class OntologyConstraintLoader:
    """三层约束加载器：本体推导 + YAML 遍历 + 覆盖合并。"""

    def __init__(self, entity_classes: list[type]) -> None:
        """从本体内省加载所有约束字段定义。

        Args:
            entity_classes: 带有 OntoConstraints 属性的 dataclass 列表。
        """
        # 内省所有 entity_classes，收集 {entity_label: OntoConstraints}
        self._ontologies: dict[str, OntoConstraints] = {}
        for cls in entity_classes:
            oc = getattr(cls, "OntoConstraints", None)
            if oc is not None and isinstance(oc, OntoConstraints):
                self._ontologies[oc.entity_label] = oc

    def load_traversals(
        self,
        traversal_configs: list[dict],
    ) -> list[TraversalConstraint]:
        """从 YAML 遍历路径 + 本体约束字段 生成完整 TraversalConstraint 列表。

        traversal_configs 格式（YAML 中去掉 value_mapping 后的剩余）:
            [{"name": "data_sensitivity", "source_label": "CodeEntity",
              "relation_chain": ["PROCESSES_DATA"], "target_label": "DataAsset",
              "collect_property": "sensitivity", "aggregation": "max"}, ...]
        """
        constraints: list[TraversalConstraint] = []
        for cfg in traversal_configs:
            target_label = cfg["target_label"]
            ontology = self._ontologies.get(target_label)
            if ontology is None:
                continue  # 未注册实体，跳过
            field_name = cfg["collect_property"]
            field_descriptor = ontology.all_constraint_fields().get(field_name)
            if field_descriptor is None:
                continue  # 字段未声明约束语义，跳过
            constraints.append(
                TraversalConstraint(
                    name=cfg["name"],
                    source_label=cfg["source_label"],
                    relation_chain=cfg["relation_chain"],
                    target_label=target_label,
                    collect_property=field_name,
                    value_mapping=field_descriptor.value_mapping,
                    aggregation=cfg.get("aggregation", "max"),
                )
            )
        return constraints

    def apply_overrides(
        self,
        constraints: list[TraversalConstraint],
        overrides: list[dict],
    ) -> list[TraversalConstraint]:
        """对现有约束应用 Layer 2 覆盖。

        覆盖类型：
        - {"type": "patch", "target": "data_sensitivity", "patch": {"value_mapping": {"restricted": "warn"}}}
        - {"type": "allow_all", "target": "CodeEntity:validate_credit_card", "reason": "..."}
        - {"type": "add_constraint", "constraint": {...}}

        allow_all 和 add_constraint 返回带标记的约束，由上层处理。
        """
        ...
```

**合并逻辑：**

```python
# 优先级顺序处理
for override in overrides:
    if override["type"] == "patch":
        # 找到对应约束，局部修改 value_mapping
        ...
    elif override["type"] == "allow_all":
        # 记录到白名单，后续检查时跳过
        ...
    elif override["type"] == "add_constraint":
        # 直接追加
        ...
```

**测试覆盖：**
1. 空本体内省 → 空约束列表
2. 单实体单字段 → 生成正确 TraversalConstraint
3. 多实体多字段 → 各自独立
4. YAML 引用未注册实体/字段 → 静默跳过
5. patch override → value_mapping 被修改
6. patch 覆盖单个值 → 其他值不变
7. allow_all → 白名单记录
8. add_constraint → 约束列表追加
9. 空覆盖 → 约束不变
10. 多层覆盖顺序 → 后覆盖先生效
11. value_mapping from ontology matches YAML-less config
12. CodeEntity.entry_category → correct mapping
13. ComplianceItem.severity → correct mapping
14. 不存在的覆盖目标 → 不报错
15. 全管道集成：loader → engine → guard → 正常执行

**提交：**
```bash
git add src/ontoagent/execution/constraints/loader.py tests/unit/test_ontology_constraint_loader.py
git commit -m "feat: add OntologyConstraintLoader (L1 auto-derive + L2 overrides)"
```

---

### Task 4: 更新 `constraints.yaml` — 移除 value_mapping

**目标：** 从 `constraints.yaml` 移除 `value_mapping` 字段，保留遍历路径配置。

**文件：** 修改 `src/ontoagent/pipeline/constraints.yaml`

**修改后内容：**

```yaml
# 遍历路径配置 — 仅定义"从哪里沿什么关系找什么字段"
# value_mapping 由本体类定义自动提供（schema.py 中的 OntoConstraints）
traversal_constraints:
  data_sensitivity:
    name: data_sensitivity
    source_label: "CodeEntity"
    relation_chain: ["PROCESSES_DATA"]
    target_label: "DataAsset"
    collect_property: "sensitivity"
    aggregation: "max"
    # value_mapping 移除此字段 — 从 DataAsset.OntoConstraints 自动加载

  data_sensitivity_check:
    name: data_sensitivity_check
    source_label: "CodeEntity"
    relation_chain: ["PROCESSES_DATA"]
    target_label: "DataAsset"
    collect_property: "sensitivity"
    aggregation: "max"
    # value_mapping 移除此字段

propagation_rules:
  upstream_risk:
    name: upstream_risk
    along: ["CALLS"]
    direction: "backward"
    max_depth: 5
    collect_property: "entryCategory"
    value_mapping:
      http_api: "warn"
      rpc_service: "warn"
      scheduled: "allow"
      mq_consumer: "allow"
      event_handler: "allow"
    aggregation: "exists"
```

**提交：**
```bash
git add src/ontoagent/pipeline/constraints.yaml
git commit -m "refactor: remove value_mapping from constraints.yaml — auto-derived from ontology"
```

---

### Task 5: 创建 `constraint_overrides.yaml` — Layer 2 覆盖模板

**目标：** 创建覆盖配置文件，初始为空（含注释示例）。

**文件：** 创建 `src/ontoagent/config/constraint_overrides.yaml`

```yaml
# Layer 2 — 约束覆盖配置
# 覆盖优先级高于本体自动推导（Layer 1）
# 三种覆盖类型：

# 1. patch: 局部修改某个约束的值映射
# overrides:
#   - type: patch
#     target: data_sensitivity
#     patch:
#       value_mapping:
#         restricted: "warn"  # 降级：restricted 从 block → warn
#         - remove: ["confidential"]  # 移除 confidential 约束
#         - add: {"archived": "block"}  # 新增 archived 值的约束

# 2. allow_all: 单点白名单 — 指定实体完全豁免约束
#   - type: allow_all
#     target: CodeEntity:validate_credit_card
#     reason: "已脱敏测试数据，安全评审通过"
#     expires: "2026-07-15"  # 可选：过期时间

# 3. add_constraint: 追加额外约束
#   - type: add_constraint
#     constraint:
#       name: compliance_check
#       source_label: CodeEntity
#       target_label: ComplianceItem
#       relation_chain: ["SUBJECT_TO"]
#       collect_property: "severity"
#       value_mapping:
#         critical: "block"
#         high: "warn"
#       aggregation: "max"

overrides: []
allow_all: []
```

**注意：** 由于 coverage 配置文件不在 `pipeline/` 下，需要在 loader 或 tools.py 中引用此路径。

**提交：**
```bash
git add src/ontoagent/config/constraint_overrides.yaml
git commit -m "feat: add constraint_overrides.yaml template (Layer 2)"
```

---

### Task 6: 更新 `agent/tools.py:_get_action_executor()` — 使用新 Loader

**目标：** 用 `OntologyConstraintLoader` 替代手工 YAML 解析。

**文件：** 修改 `src/ontoagent/agent/tools.py`

**修改范围：** `_get_action_executor()` 函数（第 410-492 行）

**核心变更：**

```python
def _get_action_executor(graph_store: object) -> ActionExecutor:
    global _action_executor
    if _action_executor is None:
        from pathlib import Path
        
        import yaml
        
        from ontoagent.domain.schema import CodeEntity, ComplianceItem, DataAsset
        from ontoagent.domain.ontology_constraints import OntoConstraints
        from ontoagent.execution.action_executor import ActionExecutor
        from ontoagent.execution.constraints import (
            ActionGuardPipeline,
            ConstraintEngine,
            ConstraintPropagator,
            EntityExistsGuard,
            EntityPropertyGuard,
            OntologyPropagationGuard,
            OntologyTraversalGuard,
            PropagationRule,
        )
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        constraints_yaml = (
            Path(__file__).parent.parent / "pipeline" / "constraints.yaml"
        )
        overrides_yaml = (
            Path(__file__).parent.parent / "config" / "constraint_overrides.yaml"
        )
        
        # Layer 1: Auto-derive from ontology
        loader = OntologyConstraintLoader([DataAsset, ComplianceItem, CodeEntity])
        
        traversal_constraints: list = []
        propagation_rules: dict[str, PropagationRule] = {}
        
        if constraints_yaml.exists():
            with open(constraints_yaml) as f:
                data = yaml.safe_load(f) or {}
            
            # 从 YAML 加载遍历路径配置（无 value_mapping）
            traversal_configs = [
                {
                    "name": cfg.get("name", name),
                    "source_label": cfg["source_label"],
                    "relation_chain": cfg["relation_chain"],
                    "target_label": cfg["target_label"],
                    "collect_property": cfg["collect_property"],
                    "aggregation": cfg.get("aggregation", "max"),
                }
                for name, cfg in data.get("traversal_constraints", {}).items()
            ]
            # 本体自动填充 value_mapping
            traversal_constraints = loader.load_traversals(traversal_configs)
            
            # Propagation rules 保持不变（与 traverse 无关）
            for name, cfg in data.get("propagation_rules", {}).items():
                propagation_rules[name] = PropagationRule(
                    name=cfg.get("name", name),
                    along=cfg.get("along", []),
                    direction=cfg.get("direction", "forward"),
                    max_depth=cfg.get("max_depth", 5),
                    collect_property=cfg.get("collect_property", ""),
                    value_mapping=cfg.get("value_mapping", {}),
                    aggregation=cfg.get("aggregation", "max"),
                )
        
        # Layer 2: Apply overrides
        if overrides_yaml.exists():
            with open(overrides_yaml) as f:
                overrides_data = yaml.safe_load(f) or {}
            overrides_list = overrides_data.get("overrides", [])
            if overrides_list:
                traversal_constraints = loader.apply_overrides(traversal_constraints, overrides_list)
        
        # Build pipeline (unchanged)
        engine = ConstraintEngine(graph_store, traversal_constraints)
        propagator = ConstraintPropagator(graph_store)
        guard_pipeline = ActionGuardPipeline([
            EntityExistsGuard(),
            EntityPropertyGuard(),
            OntologyTraversalGuard(engine),
            OntologyPropagationGuard(propagator, rules=propagation_rules),
        ])
        
        _action_executor = ActionExecutor(
            graph_store,
            function_runner=_get_function_runner(),
            guard_pipeline=guard_pipeline,
        )
    return _action_executor
```

**验证方式：**
```bash
uv run pytest tests/unit/test_ontology_constraint_loader.py -v
uv run pytest tests/unit/test_constraint_engine.py -v
uv run pytest tests/unit/test_guard_pipeline.py -v
```

**提交：**
```bash
git add src/ontoagent/agent/tools.py
git commit -m "refactor: use OntologyConstraintLoader in _get_action_executor()"
```

---

### Task 7: 更新 `__init__.py` 及其他适配

**目标：** 导出新模块，确保向后兼容。

**文件：** 修改 `src/ontoagent/execution/constraints/__init__.py`

```python
# 新增导出
from ontoagent.execution.constraints.loader import OntologyConstraintLoader  # noqa: F401

__all__ = [
    ...
    "OntologyConstraintLoader",
]
```

**提交：**
```bash
git add src/ontoagent/execution/constraints/__init__.py
git commit -m "chore: export OntologyConstraintLoader from constraints package"
```

---

### Task 8: 集成测试验证 + 全量回归

**目标：** 确认三层架构完整工作：本体定义 → YAML 遍历路径 → Engine → Guard → BLOCK/WARN/ALLOW。

**验证命令：**

```bash
# 1. 全量测试（核心）
uv run pytest tests/ -v --tb=short 2>&1 | tail -5
# 预期：所有测试通过（≥1416），无 failure

# 2. 验证约束加载正确
uv run python -c "
from ontoagent.domain.schema import DataAsset
oc = DataAsset.OntoConstraints
fd = oc.all_constraint_fields()['sensitivity']
assert fd.value_mapping['restricted'].value == 'block'
assert fd.value_mapping['public'].value == 'allow'
print('Ontology constraints OK')
"

# 3. 验证 Loader 整合
uv run python -c "
from ontoagent.domain.schema import DataAsset, CodeEntity, ComplianceItem
from ontoagent.execution.constraints.loader import OntologyConstraintLoader
loader = OntologyConstraintLoader([DataAsset, CodeEntity, ComplianceItem])
traversals = [{'name': 'test', 'source_label': 'CodeEntity',
               'relation_chain': ['PROCESSES_DATA'], 'target_label': 'DataAsset',
               'collect_property': 'sensitivity', 'aggregation': 'max'}]
constraints = loader.load_traversals(traversals)
assert len(constraints) == 1
assert constraints[0].value_mapping['restricted'].value == 'block'
print('Loader integration OK')
"

# 4. 集成测试（需要 Neo4j）
uv run pytest tests/integration/test_constraint_integration.py -v
# 预期： validate_credit_card → BLOCKED, daily_reconciliation → ALLOWED
```

**提交：**
```bash
git add -A
git commit -m "test: verify Layer 1-3 constraint pipeline end-to-end"
```

---

### Task 9: 最终清理 — 检查和推送

```bash
# 静态检查
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pyright src/

# 全量测试+覆盖率
uv run pytest tests/ --cov=ontoagent --cov-report=term-missing -q 2>&1 | tail -20

# 推送
git push
```

---

## 验证检查点

| 检查项 | 预期 |
|--------|------|
| DataAsset.OntoConstraints 存在 | sensitivity 映射到 BLOCK/WARN/ALLOW |
| ComplianceItem.OntoConstraints 存在 | severity 映射到 BLOCK/WARN/ALLOW |
| CodeEntity.OntoConstraints 存在 | entry_category 映射到 WARN/ALLOW |
| constraints.yaml 无 value_mapping | 遍历路径格式正确 |
| constraint_overrides.yaml 存在 | overrides: [], allow_all: [] |
| Loader 生成正确 TraversalConstraint | value_mapping 来自本体 |
| 空覆盖 → 约束不变 | 全量测试 ≥1416 pass |
| patch 覆盖 → value_mapping 变更 | 单元测试覆盖 |
| 集成测试 → BLOCK 生效 | validate_credit_card BLOCKED |

---

## 风险

| 风险 | 缓解 |
|------|------|
| dataclass 中放 `OntoConstraints` 类属性与 `@dataclass` 冲突 | `OntoConstraints` 是裸属性（无类型注解），`@dataclass` 不会将其视为字段 |
| 循环导入（schema.py ↔ constraints.py） | schema.py 已经 import constraints.py（GuardDecision, GuardLevel），ontology_constraints.py 也在 domain/ 层，无循环风险 |
| 现有测试使用硬编码 value_mapping | 现有测试 mock 了 ConstraintEngine，不受影响；只有直接构造 TraversalConstraint 的测试需更新 |

---

## 实施顺序

依赖图：
```
Task 1 (ontology_constraints.py) 
  ↓
Task 2 (schema.py OntoConstraints) — 依赖 Task 1
  ↓
Task 3 (loader.py + tests) — 依赖 Task 1
  ↓
Task 4 (constraints.yaml 去 value_mapping) — 独立
Task 5 (overrides.yaml 模板) — 独立
  ↓
Task 6 (tools.py 使用 Loader) — 依赖 Task 1-5
Task 7 (__init__.py 导出) — 独立
  ↓
Task 8 (集成验证) — 依赖 Task 1-7
Task 9 (清理推送) — 依赖 Task 8
```

**可并行：** Task 4 和 Task 5 可在 Task 1-3 完成后并行执行。
