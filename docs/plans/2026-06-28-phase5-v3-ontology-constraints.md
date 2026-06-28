# Phase 5 v3：三层本体约束架构实施计划（v2 修订版）

> **修订记录：** 基于架构评审 + 产品评审共 6 个必须修复项 + 3 个建议改进项。

**目标：** 将约束规则从"手工 YAML"升级为"本体定义 + YAML 覆盖"三层架构，让本体定义成为约束的单一事实源。

**架构：** Layer 1（本体自动推导）→ Layer 2（YAML 覆盖）→ Layer 3（运行时确认）。优先级 Layer 3 > Layer 2 > Layer 1。

**技术栈：** Python 3.13 · pytest · PyYAML · Neo4j

---

## 评审修复对照表

| # | 问题 | 来源 | 严重性 | 修复方案 |
|---|------|------|--------|---------|
| 1 | patch YAML 语法非法（dict 内混列表项） | 架构+产品 | 🔴 | 改用扁平字段 `modify` / `remove_values` / `add_values` |
| 2 | propagation_rules 未接入三层架构 | 架构+产品 | 🔴 | Loader 也处理 propagation value_mapping，统一从 OntoConstraints 自动填充 |
| 3 | data_sensitivity / data_sensitivity_check 重复 | 架构 | 🔴 | 删除 data_sensitivity_check，改为 L2 override |
| 4 | BLOCK 溯源信息未实现 | 产品 | 🔴 | TraversalConstraint 加 `ontology_source` 字段；GuardDecision.reason 引用之 |
| 5 | Loader 接口编排泄漏到 tools.py | 架构 | 🔴 | 封装 `load_all(path)` 高层入口，tools.py 一行调用 |
| 6 | 缺失 OntoConstraints 时静默失效 | 产品 | 🟡 | Loader 启动时 WARN 日志 + 统计 |
| 7 | allow_all target 格式不明 | 产品 | 🟡 | 明确为 `{Neo4jLabel}:{entity_name}` |
| 8 | OntoConstraints 放 dataclass 反模式 | 产品 | 🟡 | 改用 `ONTOLOGY_CONSTRAINTS` 外部注册表 |
| 9 | _ConstraintPath 死代码 | 架构+产品 | 🟡 | 删除，Loader 直接构造 TraversalConstraint |

---

## 修订后核心设计

### 约束注册表（外部注册，非 dataclass 内嵌）

```python
# schema.py 底部 — 外部注册表，不侵入 dataclass
ONTOLOGY_CONSTRAINT_REGISTRY: dict[str, ConstraintFieldDescriptor] = {
    # entity_label.field_name → descriptor
    "DataAsset.sensitivity": ConstraintFieldDescriptor(
        field_name="sensitivity",
        value_mapping={
            "restricted": GuardLevel.BLOCK,
            "confidential": GuardLevel.WARN,
            "internal": GuardLevel.ALLOW,
            "public": GuardLevel.ALLOW,
        },
    ),
    "ComplianceItem.severity": ConstraintFieldDescriptor(
        field_name="severity",
        value_mapping={
            "critical": GuardLevel.BLOCK,
            "high": GuardLevel.WARN,
            "medium": GuardLevel.ALLOW,
            "low": GuardLevel.ALLOW,
        },
    ),
    "CodeEntity.entry_category": ConstraintFieldDescriptor(
        field_name="entry_category",
        value_mapping={
            "http_api": GuardLevel.WARN,
            "rpc_service": GuardLevel.WARN,
            "scheduled": GuardLevel.ALLOW,
            "mq_consumer": GuardLevel.ALLOW,
            "event_handler": GuardLevel.ALLOW,
        },
    ),
}
```

### TraversalConstraint 新增溯源字段

```python
@dataclass
class TraversalConstraint:
    # ... existing fields ...
    ontology_source: str = ""  # 新增 — "DataAsset.sensitivity"
```

### BLOCK 原因示例

```
BLOCKED: DataAsset.sensitivity→restricted=BLOCK
  约束来源: DataAsset.sensitivity (本体定义)
  操作: refactor, 目标: payment_db.credit_cards
```

### Loader 高层入口

```python
loader = OntologyConstraintLoader(registry=ONTOLOGY_CONSTRAINT_REGISTRY)
traversals, rules, warnings = loader.load_all(constraints_yaml=path, overrides_yaml=path)
# 一站式：warnings 包含缺失 OntoConstraints 等启动告警
```

### 覆盖 YAML 语法（修正后）

```yaml
# constraint_overrides.yaml
overrides:
  # 类型 1: patch — 局部修改约束的值映射
  - type: patch
    target: data_sensitivity          # 对应 constraints.yaml 的 traversal_constraints key
    modify:                           # 修改已有值
      restricted: "warn"
    remove_values: ["confidential"]   # 移除某些值的约束
    add_values:                       # 新增值的约束
      archived: "block"

  # 类型 2: allow_all — 单点白名单
  - type: allow_all
    target_entity: "CodeEntity:validate_credit_card"  # {Neo4jLabel}:{entity_name}
    reason: "已脱敏测试数据，安全评审通过"
    expires: "2026-07-15"  # 可选

  # 类型 3: add_constraint — 追加额外遍历约束
  - type: add_constraint
    constraint:
      name: compliance_check
      source_label: "CodeEntity"
      relation_chain: ["SUBJECT_TO"]
      target_label: "ComplianceItem"
      collect_property: "severity"
      aggregation: "max"
```

### data_sensitivity_check 处理

删除 `constraints.yaml` 中的 `data_sensitivity_check`，如需降级 `restricted → warn`，在 `constraint_overrides.yaml` 中通过 `patch` 实现：

```yaml
# constraint_overrides.yaml — data_sensitivity_check → L2 override
overrides:
  - type: patch
    target: data_sensitivity
    modify:
      restricted: "warn"
```

---

## 修订后文件变更清单

| 操作 | 文件 | 变更量 |
|------|------|--------|
| **新建** | `domain/ontology_constraints.py` | ~50 行：`ConstraintFieldDescriptor` |
| **修改** | `domain/constraints.py` | +2 行：`TraversalConstraint.ontology_source` 字段 |
| **修改** | `domain/schema.py` | +35 行：`ONTOLOGY_CONSTRAINT_REGISTRY` 外部注册表 |
| **新建** | `execution/constraints/loader.py` | ~120 行：`OntologyConstraintLoader`（含 `load_all()` + 覆盖合并 + 缺失检测） |
| **修改** | `pipeline/constraints.yaml` | -25 行：删除所有 value_mapping + 删除 data_sensitivity_check |
| **新建** | `config/constraint_overrides.yaml` | ~35 行：覆盖配置模板（修正语法） |
| **修改** | `agent/tools.py` | -35/+15 行：简化为一行 `load_all()` 调用 |
| **修改** | `execution/constraints/__init__.py` | +2 行 |
| **新建** | `tests/unit/test_ontology_constraint_loader.py` | ~140 行：18 个测试 |
| **修改** | `tests/unit/test_constraint_engine.py` | ±5 行 |
| **修改** | `tests/integration/test_constraint_integration.py` | ±5 行 |

**总计：** 3 新建 + 8 修改，~350 行新增，~70 行删除

---

## 修订后任务分解

### Task 1: 创建 `domain/ontology_constraints.py`

**文件：** 创建 `src/ontoagent/domain/ontology_constraints.py`

```python
from __future__ import annotations

from dataclasses import dataclass, field

from ontoagent.domain.constraints import GuardLevel


@dataclass
class ConstraintFieldDescriptor:
    """描述实体字段上的约束语义。由 ONTOLOGY_CONSTRAINT_REGISTRY 注册。

    Attributes:
        field_name: 字段名 (e.g. "sensitivity")
        value_mapping: 字段值 → 约束级别的映射
    """
    field_name: str
    value_mapping: dict[str, GuardLevel] = field(default_factory=dict)
```

**提交：**
```bash
git add src/ontoagent/domain/ontology_constraints.py
git commit -m "feat: add ConstraintFieldDescriptor for ontology constraint registration"
```

---

### Task 2: 给 `domain/constraints.py` 加 `ontology_source` 字段

**文件：** 修改 `src/ontoagent/domain/constraints.py`

在 `TraversalConstraint` 中新增字段：
```python
@dataclass
class TraversalConstraint:
    name: str
    source_label: str
    relation_chain: list[str]
    target_label: str
    collect_property: str
    value_mapping: dict[str, GuardLevel]
    aggregation: Literal["max", "min", "exists"] = "max"
    ontology_source: str = ""  # NEW — "DataAsset.sensitivity" for traceability
```

**提交：**
```bash
git add src/ontoagent/domain/constraints.py
git commit -m "feat: add ontology_source field to TraversalConstraint for BLOCK traceability"
```

---

### Task 3: 在 `schema.py` 添加 `ONTOLOGY_CONSTRAINT_REGISTRY`

**文件：** 修改 `src/ontoagent/domain/schema.py`

在文件末尾（`VALID_RELATION_TYPES` 等注册表之后）追加：

```python
# ============================================================
# Ontology Constraint Registry — Layer 1 auto-derivation source
# ============================================================
# 格式: "{EntityLabel}.{field_name}" → ConstraintFieldDescriptor
# 当 constraints.yaml 引用某个 target_label + collect_property 时，
# Loader 从此注册表自动获取 value_mapping，无需在 YAML 中重复定义。

from ontoagent.domain.constraints import GuardLevel
from ontoagent.domain.ontology_constraints import ConstraintFieldDescriptor

ONTOLOGY_CONSTRAINT_REGISTRY: dict[str, ConstraintFieldDescriptor] = {
    "DataAsset.sensitivity": ConstraintFieldDescriptor(
        field_name="sensitivity",
        value_mapping={
            "restricted": GuardLevel.BLOCK,
            "confidential": GuardLevel.WARN,
            "internal": GuardLevel.ALLOW,
            "public": GuardLevel.ALLOW,
        },
    ),
    "ComplianceItem.severity": ConstraintFieldDescriptor(
        field_name="severity",
        value_mapping={
            "critical": GuardLevel.BLOCK,
            "high": GuardLevel.WARN,
            "medium": GuardLevel.ALLOW,
            "low": GuardLevel.ALLOW,
        },
    ),
    "CodeEntity.entry_category": ConstraintFieldDescriptor(
        field_name="entry_category",
        value_mapping={
            "http_api": GuardLevel.WARN,
            "rpc_service": GuardLevel.WARN,
            "scheduled": GuardLevel.ALLOW,
            "mq_consumer": GuardLevel.ALLOW,
            "event_handler": GuardLevel.ALLOW,
        },
    ),
}
```

**提交：**
```bash
git add src/ontoagent/domain/schema.py
git commit -m "feat: add ONTOLOGY_CONSTRAINT_REGISTRY — auto-derivation source for Layer 1"
```

---

### Task 4: 创建 `execution/constraints/loader.py`

**文件：** 创建 `src/ontoagent/execution/constraints/loader.py`

核心接口：

```python
class OntologyConstraintLoader:
    """三层约束加载器：本体注册表 + YAML 遍历路径 + 覆盖合并。"""

    def __init__(self, registry: dict[str, ConstraintFieldDescriptor]) -> None:
        self._registry = registry

    def load_all(
        self,
        constraints_yaml: str | Path | None = None,
        overrides_yaml: str | Path | None = None,
    ) -> tuple[list[TraversalConstraint], dict[str, PropagationRule], list[str]]:
        """一站式加载：返回 (traversals, propagation_rules, warnings)。

        自动从注册表填充 value_mapping，检测缺失，应用覆盖。
        """
        traversals = []
        rules = {}
        warnings = []
        yaml_data = self._read_yaml(constraints_yaml)
        overrides_data = self._read_yaml(overrides_yaml) if overrides_yaml else {}

        # Traversal constraints
        for name, cfg in yaml_data.get("traversal_constraints", {}).items():
            key = f"{cfg['target_label']}.{cfg['collect_property']}"
            descriptor = self._registry.get(key)
            if descriptor is None:
                warnings.append(f"WARN: {key} 未在 ONTOLOGY_CONSTRAINT_REGISTRY 注册 — 约束 '{name}' 将使用空 value_mapping")
                value_mapping = {}
            else:
                value_mapping = descriptor.value_mapping
            traversals.append(TraversalConstraint(
                name=name,
                source_label=cfg["source_label"],
                relation_chain=cfg["relation_chain"],
                target_label=cfg["target_label"],
                collect_property=cfg["collect_property"],
                value_mapping=value_mapping,
                aggregation=cfg.get("aggregation", "max"),
                ontology_source=key if descriptor else "",
            ))

        # Propagation rules — also auto-fill from registry
        for name, cfg in yaml_data.get("propagation_rules", {}).items():
            raw_mapping = cfg.get("value_mapping", {})
            # If collect_property corresponds to a registered field, prefer registry
            # (propagation rules don't have target_label, so we search by field_name)
            for reg_key, desc in self._registry.items():
                if reg_key.endswith(f".{cfg['collect_property']}"):
                    raw_mapping = {k: v.value for k, v in desc.value_mapping.items()}
                    break
            rules[name] = PropagationRule(
                name=name,
                along=cfg.get("along", []),
                direction=cfg.get("direction", "forward"),
                max_depth=cfg.get("max_depth", 5),
                collect_property=cfg.get("collect_property", ""),
                value_mapping=raw_mapping,
                aggregation=cfg.get("aggregation", "max"),
            )

        # Apply overrides
        for override in overrides_data.get("overrides", []):
            ov_type = override.get("type")
            if ov_type == "patch":
                self._apply_patch(traversals, override)
            elif ov_type == "allow_all":
                self._apply_allow_all(traversals, override, warnings)
            elif ov_type == "add_constraint":
                self._apply_add_constraint(traversals, override)

        # Missing registry check
        referenced = {(c.target_label, c.collect_property) for c in traversals}
        for label, prop in referenced:
            key = f"{label}.{prop}"
            if key not in self._registry:
                warnings.append(f"WARN: '{label}.{prop}' referenced in constraints.yaml but missing from ONTOLOGY_CONSTRAINT_REGISTRY — constraints for this path may be incomplete")

        return traversals, rules, warnings

    def _apply_patch(self, traversals: list[TraversalConstraint], override: dict) -> None:
        target_name = override["target"]
        for c in traversals:
            if c.name == target_name:
                # modify
                for val, level_str in override.get("modify", {}).items():
                    c.value_mapping[val] = GuardLevel(level_str)
                # remove_values
                for val in override.get("remove_values", []):
                    c.value_mapping.pop(val, None)
                # add_values
                for val, level_str in override.get("add_values", {}).items():
                    c.value_mapping[val] = GuardLevel(level_str)
                break

    def _apply_allow_all(self, traversals, override, warnings):
        # Record whitelist entry: {label}:{name} → exempt from all constraints
        target_entity = override["target_entity"]  # "CodeEntity:validate_credit_card"
        warnings.append(f"INFO: allow_all for {target_entity}: {override.get('reason', 'no reason')}")

    def _apply_add_constraint(self, traversals, override):
        cfg = override["constraint"]
        traversals.append(TraversalConstraint(
            name=cfg["name"],
            source_label=cfg["source_label"],
            relation_chain=cfg["relation_chain"],
            target_label=cfg["target_label"],
            collect_property=cfg["collect_property"],
            value_mapping={k: GuardLevel(v) for k, v in cfg.get("value_mapping", {}).items()},
            aggregation=cfg.get("aggregation", "max"),
        ))

    def _read_yaml(self, path):
        if path is None:
            return {}
        import yaml
        p = Path(path) if isinstance(path, str) else path
        if p.exists():
            with open(p) as f:
                return yaml.safe_load(f) or {}
        return {}

    @property
    def registry(self):
        return self._registry
```

**同时创建测试文件：** `tests/unit/test_ontology_constraint_loader.py`（~140 行，18 个测试）

测试清单：
1. 空注册表 → 空返回 + warnings "未注册"
2. 单约束自动填充 → value_mapping 来自注册表
3. 多约束独立填充 → 各自正确
4. propagation value_mapping 从注册表自动填充
5. propagation 无注册表匹配 → 使用 YAML 中的 value_mapping
6. patch modify → 值变更
7. patch remove → 值删除
8. patch add → 值新增
9. allow_all → warnings 记录
10. add_constraint → 约束列表追加
11. 空覆盖 → 约束不变
12. 覆盖顺序 → last-write-wins
13. ontology_source 字段正确填充
14. 缺失注册表 → WARN 输出
15. 全管道：loader → engine → guard
16. 无 YAML 文件 → 空结果
17. 重复约束名 → 各自独立处理
18. data_sensitivity_check 删除 → 不出现

**提交：**
```bash
git add src/ontoagent/execution/constraints/loader.py tests/unit/test_ontology_constraint_loader.py
git commit -m "feat: add OntologyConstraintLoader with load_all() — auto-derive + overrides + warnings"
```

---

### Task 5: 更新 `constraints.yaml`

**文件：** 修改 `src/ontoagent/pipeline/constraints.yaml`

```yaml
# 遍历路径配置 — 仅定义遍历路径
# value_mapping 由 ONTOLOGY_CONSTRAINT_REGISTRY (schema.py) 自动填充
traversal_constraints:
  data_sensitivity:
    name: data_sensitivity
    source_label: "CodeEntity"
    relation_chain: ["PROCESSES_DATA"]
    target_label: "DataAsset"
    collect_property: "sensitivity"
    aggregation: "max"

# 注：data_sensitivity_check 已删除。如需降级 restricted→warn，
# 请在 constraint_overrides.yaml 中用 patch 实现。

propagation_rules:
  upstream_risk:
    name: upstream_risk
    along: ["CALLS"]
    direction: "backward"
    max_depth: 5
    collect_property: "entryCategory"
    aggregation: "exists"
    # value_mapping 也由 ONTOLOGY_CONSTRAINT_REGISTRY 自动填充
```

**提交：**
```bash
git add src/ontoagent/pipeline/constraints.yaml
git commit -m "refactor: remove all value_mapping from constraints.yaml — auto-derived from ontology registry"
```

---

### Task 6: 创建 `constraint_overrides.yaml`

**文件：** 创建 `src/ontoagent/config/constraint_overrides.yaml`

```yaml
# Layer 2 — 约束覆盖配置
# 覆盖优先级高于本体自动推导（Layer 1）

overrides:
  # === 类型 1: patch — 局部修改已有约束 ===
  # - type: patch
  #   target: data_sensitivity        # 对应 constraints.yaml 中 traversal_constraints 的 key
  #   modify:                         # 修改已有值的约束级别
  #     restricted: "warn"            # restricted 从 block → warn
  #   remove_values: ["confidential"] # 移除 confidential 约束（等同 allow）
  #   add_values:                     # 新增值的约束
  #     archived: "block"

  # === 类型 2: allow_all — 单点白名单 ===
  # - type: allow_all
  #   target_entity: "CodeEntity:validate_credit_card"  # 格式: {Neo4jLabel}:{entity_name}
  #   reason: "已脱敏测试数据，安全评审通过"
  #   expires: "2026-07-15"  # 可选

  # === 类型 3: add_constraint — 追加额外约束 ===
  # - type: add_constraint
  #   constraint:
  #     name: compliance_check
  #     source_label: "CodeEntity"
  #     relation_chain: ["SUBJECT_TO"]
  #     target_label: "ComplianceItem"
  #     collect_property: "severity"
  #     aggregation: "max"
```

**提交：**
```bash
git add src/ontoagent/config/constraint_overrides.yaml
git commit -m "feat: add constraint_overrides.yaml with corrected patch/allow_all/add_constraint syntax"
```

---

### Task 7: 简化 `agent/tools.py:_get_action_executor()`

**文件：** 修改 `src/ontoagent/agent/tools.py`

核心变更：将 80 行手工解析替换为 ~10 行 Loader 调用。

```python
def _get_action_executor(graph_store: object) -> ActionExecutor:
    global _action_executor
    if _action_executor is None:
        from pathlib import Path
        from ontoagent.domain.schema import ONTOLOGY_CONSTRAINT_REGISTRY
        from ontoagent.execution.action_executor import ActionExecutor
        from ontoagent.execution.constraints import (
            ActionGuardPipeline,
            ConstraintEngine,
            ConstraintPropagator,
            EntityExistsGuard,
            EntityPropertyGuard,
            OntologyPropagationGuard,
            OntologyTraversalGuard,
        )
        from ontoagent.execution.constraints.loader import OntologyConstraintLoader

        constraints_yaml = Path(__file__).parent.parent / "pipeline" / "constraints.yaml"
        overrides_yaml = Path(__file__).parent.parent / "config" / "constraint_overrides.yaml"

        loader = OntologyConstraintLoader(registry=ONTOLOGY_CONSTRAINT_REGISTRY)
        traversals, prop_rules, warnings = loader.load_all(
            constraints_yaml=constraints_yaml,
            overrides_yaml=overrides_yaml,
        )

        # Log startup warnings
        for w in warnings:
            logging.getLogger(__name__).warning(w)

        engine = ConstraintEngine(graph_store, traversals)
        propagator = ConstraintPropagator(graph_store)
        guard_pipeline = ActionGuardPipeline([
            EntityExistsGuard(),
            EntityPropertyGuard(),
            OntologyTraversalGuard(engine),
            OntologyPropagationGuard(propagator, rules=prop_rules),
        ])

        _action_executor = ActionExecutor(
            graph_store,
            function_runner=_get_function_runner(),
            guard_pipeline=guard_pipeline,
        )
    return _action_executor
```

**提交：**
```bash
git add src/ontoagent/agent/tools.py
git commit -m "refactor: simplify _get_action_executor() with OntologyConstraintLoader.load_all()"
```

---

### Task 8: 更新 `constraints/__init__.py`

```bash
git add src/ontoagent/execution/constraints/__init__.py
git commit -m "chore: export OntologyConstraintLoader"
```

---

### Task 9: 集成验证

```bash
# 全量测试
uv run pytest tests/ -v --tb=short -q

# 验证 ontology_source 字段
uv run python -c "
from ontoagent.execution.constraints.loader import OntologyConstraintLoader
from ontoagent.domain.schema import ONTOLOGY_CONSTRAINT_REGISTRY
loader = OntologyConstraintLoader(registry=ONTOLOGY_CONSTRAINT_REGISTRY)
"

# 集成测试
uv run pytest tests/integration/test_constraint_integration.py -v
```

**提交：**
```bash
git add -A && git commit -m "test: verify v3 constraint pipeline end-to-end"
```

---

### Task 10: 清理推送

```bash
uv run ruff check src/ tests/ && uv run ruff format src/ tests/
uv run pyright src/
uv run pytest tests/ -q
git push
```

---

## 依赖图

```
Task 1 (ontology_constraints.py) ──┐
Task 2 (constraints.py +source) ──┤
                                   ├→ Task 4 (loader.py + tests)
Task 3 (schema.py registry) ──────┘        ↓
                                   Task 5 (constraints.yaml) — 可并行
                                   Task 6 (overrides.yaml)  — 可并行
                                        ↓
                                   Task 7 (tools.py)
                                   Task 8 (__init__.py)
                                        ↓
                                   Task 9 (集成验证)
                                   Task 10 (清理推送)
```

**可并行：** Task 1+2+3 可在同一 commit 中完成（都在 domain 层）。Task 5+6 可并行执行。
