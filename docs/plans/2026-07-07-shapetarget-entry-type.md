# ShapeTarget 拆解 v2：entry_type × ontology_ref

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.
> **CC Review:** 6/10 → v2 修正 12 个问题，目标 8.5+

**Goal:** 将 `ShapeTarget.resource_type` 拆分为 `entry_type`（图入口标签）和 `ontology_ref`（本体概念引用），解决资源类型字段承担双重角色的架构问题。

**Architecture:** `ShapeTarget` 数据模型语义修复。`resource_type` 当前既充当图入口标签（用于 Neo4j 匹配）又充当本体概念名（约束语义），demo 场景里两者重合，换域即崩塌。拆分后 `entry_type` 负责图遍历入口（参与 ShapeRegistry 索引），`ontology_ref` 承载领域语义（不参与索引，由调用方按需过滤）——两者正交，各自独立演化。

**Impact:** 11 个文件，约 55 处引用。YAML 格式字段名从 `resource_type` 改为 `entry_type`。Schema version bump 到 2.1。

---

## 设计决策记录

| 决策 | 结论 | 理由 |
|------|------|------|
| `ontology_ref` 是否参与 ShapeRegistry 索引？ | 否 | `entry_type` 是粗粒度图遍历入口，`ontology_ref` 是细粒度语义过滤，由调用方拿到候选 Shape 后再 match |
| 旧 `resource_type` YAML 是否兼容？ | 兼容 | `from_yaml_dict` 保留 `resource_type` fallback（兼容期） |
| ontology_loader 映射策略？ | 统一使用 `_DOMAIN_TO_ENTRY_TYPE` 映射表 | 避免不同转换函数的硬编码不一致 |
| 字段顺序？ | `ontology_ref` 放 `field_filter` 之后 | 避免位置参数调用将 `None` 误传给 `ontology_ref` |
| Schema version bump？ | 2.0 → 2.1（minor） | `ontology_ref` 是新增语义维度，旧版代码收到含 `ontology_ref` 的 Shape 应知晓 |

---

## 改动清单

| # | 文件 | 改动类型 | 内容 |
|---|------|---------|------|
| 1 | `domain/shapes.py` | 数据模型 | 重命名 + 加字段 + 校验修复 |
| 2 | `pipeline/shapes.yaml` | YAML 格式 | 字段名 + schema version |
| 3 | `execution/shape_registry.py` | 字段访问 + doc | 全部引用重命名 + 索引语义说明 |
| 4 | `agent/tools.py` | 序列化 + doc | dict key 重命名 + 补 `ontology_ref` + docstring 澄清 |
| 5 | `api/cli.py` | 显示 | 字段访问重命名 |
| 6 | `pipeline/ontology_loader.py` | 生成器 | 输出字段名 + 统一映射表 |
| 7 | `domain/schema.py` | 注释 | line 645 注释更新 |
| 8 | `tests/unit/test_shape_cross_validation.py` | 测试 | 构造函数参数 |
| 9 | `tests/unit/execution/test_action_executor_shapes.py` | 测试 | 构造函数参数 |
| 10 | `tests/unit/execution/test_submission_criteria_migration.py` | 测试 | 断言 |
| 11 | `tests/unit/test_cli_validate_shapes.py` | 测试 | 内联 YAML |

---

### Task 0: 新增测试（TDD — RED 阶段）✅ CC审查要求

**Objective:** 先写失败测试，覆盖新增行为和兼容路径。

**Files:**
- Create: `tests/unit/test_shape_target_ontology_ref.py`

**测试用例：**

```python
"""ShapeTarget ontology_ref 字段与 entry_type 重命名 测试。"""
import pytest
from ontoagent.domain.shapes import ShapeTarget, Operation, ConstraintShape, ShapeKind

def test_shape_target_accepts_ontology_ref():
    """新增 ontology_ref 字段可正确设置和读取"""
    target = ShapeTarget(
        entry_type="ResourceEntity",
        operation=Operation.UPDATE,
        ontology_ref="客户",
    )
    assert target.ontology_ref == "客户"
    assert target.entry_type == "ResourceEntity"


def test_shape_target_ontology_ref_defaults_to_none():
    """未提供 ontology_ref 时默认 None"""
    target = ShapeTarget(entry_type="CodeEntity", operation=Operation.READ)
    assert target.ontology_ref is None


def test_from_yaml_dict_legacy_resource_type_compat():
    """旧 YAML 含 resource_type 字段时能 fallback 解析"""
    data = {
        "id": "test:legacy",
        "name": "Legacy Shape",
        "description": "Test",
        "kind": "operational",
        "target": {"resource_type": "CodeEntity", "operation": "READ"},
        "path": "SELF",
        "constraint": {"field": "test"},
        "severity": "warn",
    }
    shape = ConstraintShape.from_yaml_dict(data)
    assert shape.target.entry_type == "CodeEntity"
    assert shape.target.ontology_ref is None


def test_from_yaml_dict_entry_type_takes_precedence():
    """同时有 entry_type 和 resource_type 时，entry_type 优先"""
    data = {
        "id": "test:dual",
        "name": "Dual Shape",
        "description": "Test",
        "kind": "operational",
        "target": {
            "entry_type": "ResourceEntity",
            "resource_type": "CodeEntity",
            "operation": "UPDATE",
        },
        "path": "SELF",
        "constraint": {"field": "test"},
        "severity": "block",
    }
    shape = ConstraintShape.from_yaml_dict(data)
    assert shape.target.entry_type == "ResourceEntity"


def test_from_yaml_dict_missing_entry_type_raises():
    """entry_type 和 resource_type 都缺失时显式报错"""
    data = {
        "id": "test:missing",
        "name": "Missing Entry",
        "description": "Test",
        "kind": "operational",
        "target": {"operation": "READ"},
        "path": "SELF",
        "constraint": {"field": "test"},
        "severity": "warn",
    }
    with pytest.raises(ValueError, match="entry_type"):
        ConstraintShape.from_yaml_dict(data)


def test_from_yaml_dict_reads_ontology_ref():
    """YAML 含 ontology_ref 字段时正确解析"""
    data = {
        "id": "test:with_ref",
        "name": "With Ref",
        "description": "Test",
        "kind": "operational",
        "target": {
            "entry_type": "ResourceEntity",
            "operation": "UPDATE",
            "ontology_ref": "订单表",
        },
        "path": "SELF",
        "constraint": {"field": "test"},
        "severity": "warn",
    }
    shape = ConstraintShape.from_yaml_dict(data)
    assert shape.target.ontology_ref == "订单表"


def test_shape_target_field_order_respects_backward_compat():
    """字段顺序：ontology_ref 在 field_filter 之后，避免位置参数误传"""
    # 验证用两个位置参数（旧代码模式）调用不会崩溃
    target = ShapeTarget("CodeEntity", Operation.READ, None)
    assert target.entry_type == "CodeEntity"
    assert target.ontology_ref is None  # 第三个位置参数是 field_filter，不是 ontology_ref
    assert target.field_filter is None
```

**Step 1:** 运行 `uv run pytest tests/unit/test_shape_target_ontology_ref.py -v`

预期：全部 7 条 FAIL — 因为 `entry_type` 和 `ontology_ref` 字段还不存在。

**Step 2: Commit**
```bash
git add tests/unit/test_shape_target_ontology_ref.py
git commit -m "test: add RED tests for ShapeTarget entry_type + ontology_ref"
```

---

### Task 1: 修改 `ShapeTarget` 数据模型

**Objective:** 将 `resource_type` 重命名为 `entry_type`，新增 `ontology_ref` 可选字段。

**Files:**
- Modify: `src/ontoagent/domain/shapes.py`

**变更：**

1.1 `ShapeTarget` dataclass（字段顺序修正：`ontology_ref` 放 `field_filter` 之后）：

```python
# Before
@dataclass(frozen=True)
class ShapeTarget:
    resource_type: str
    operation: Operation
    field_filter: dict[str, str] | None = None

# After
@dataclass(frozen=True)
class ShapeTarget:
    entry_type: str                               # was: resource_type
    operation: Operation
    field_filter: dict[str, str] | None = None
    ontology_ref: str | None = None               # NEW: 本体概念引用（放最后，不破坏位置参数兼容）
```

1.2 `from_yaml_dict` 中的构造调用（修复 CC P1 — 保留显式校验）：

```python
# Before
target = ShapeTarget(
    resource_type=target_data["resource_type"],
    operation=operation,
    field_filter=dict(field_filter) if field_filter else None,
)

# After
raw_entry = target_data.get("entry_type")
if not raw_entry:
    raw_entry = target_data.get("resource_type")
if not raw_entry:
    raise ValueError(
        f"Shape {shape_id!r} 缺少 target.entry_type / target.resource_type"
    )
target = ShapeTarget(
    entry_type=raw_entry,
    operation=operation,
    field_filter=dict(field_filter) if field_filter else None,
    ontology_ref=target_data.get("ontology_ref"),
)
```

1.3 对应 docstring 更新：

- `ShapeTarget` 的 `Attributes` 改为：`entry_type: Neo4j 实体标签，如 "CodeEntity"。`
- 新增：`ontology_ref: 可选的本体概念引用，如 "客户"、"订单"。不参与图遍历索引。`
- Annotation comment line 645 in `domain/schema.py`: `resource_type` → `entry_type`

**Step 1: 修改代码**

**Step 2: 运行新增测试**
```bash
uv run pytest tests/unit/test_shape_target_ontology_ref.py -v
```
预期：7 passed ✅

---

### Task 2: 更新 `shapes.yaml`（demo 配置）+ schema version bump

**Objective:** YAML 字段名 `resource_type` → `entry_type`，version bump。

**Files:**
- Modify: `src/ontoagent/pipeline/shapes.yaml`

**变更：**
- `version: "2.0"` → `version: "2.1"`
- 5 处 `resource_type: CodeEntity` → `entry_type: CodeEntity`

---

### Task 3: 更新 `shape_registry.py`

**Objective:** 全部引用重命名 + 索引语义文档。

**Files:**
- Modify: `src/ontoagent/execution/shape_registry.py`

**变更清单：**

3.1 文件头 docstring：`resource_type` → `entry_type`
3.2 类 docstring 加索引语义说明：
```
维护 (entry_type, operation) → list[ConstraintShape] 的倒排索引。
entry_type 是粗粒度图遍历入口（仅校验标准图标签），
ontology_ref 是细粒度语义过滤（由调用方自行 match，不参与索引）。
```
3.3 `_add_shape()`: `shape.target.resource_type` → `shape.target.entry_type`（line 56, 111）
3.4 `get_shapes()` 参数名：`resource_type` → `entry_type`，docstring 更新
3.5 `validate_shape()`: `shape.target.resource_type` → `shape.target.entry_type`（line 161, 162, 172）

---

### Task 4: 更新 `agent/tools.py`

**Objective:** 序列化补全 `ontology_ref` + docstring 澄清。

**Files:**
- Modify: `src/ontoagent/agent/tools.py`

**变更：**

4.1 Line 686-689：序列化 dict 补全
```python
"target": {
    "entry_type": target.entry_type,
    "operation": target.operation.value,
    "ontology_ref": target.ontology_ref,    # NEW
    "field_filter": target.field_filter,
},
```

4.2 Line 723 docstring 加说明：
```
{source_intent: str, entry_type: str, count: int, ...}
# 注意：此处的 resource_type（line 749）属于 intent_router 层，
# 与 ShapeTarget.entry_type 是不同层级的概念。为保持意图层 API 稳定，此处不改名。
```

4.3 Line 749 的 `"resource_type": bind_to` 保持不变（意图层，非 Shape 层）。

---

### Task 5: 更新 `api/cli.py`

**Objective:** CLI 显示字段访问重命名。

**Files:**
- Modify: `src/ontoagent/api/cli.py`

**变更：**
- Line 414: docstring `resource_type` → `entry_type`
- Line 441: `shape.target.resource_type` → `shape.target.entry_type`

---

### Task 6: 更新 `ontology_loader.py`（统一映射策略）

**Objective:** 统一使用映射表，消除硬编码不一致。

**Files:**
- Modify: `src/ontoagent/pipeline/ontology_loader.py`

**变更：**

6.1 扩展映射常量：
```python
_DOMAIN_TO_ENTRY_TYPE: dict[str, str] = {
    "rdb": "ResourceEntity",
    "doc": "DocEntity",
    "code": "CodeEntity",
    "unknown": "ResourceEntity",
}

def _resolve_entry_type(source: str) -> str:
    """根据数据来源决定图入口标签。"""
    return _DOMAIN_TO_ENTRY_TYPE.get(source, "ResourceEntity")
```

6.2 `_convert_entity_types()`: 
```python
"entry_type": _resolve_entry_type(entity.get("source", "unknown")),
"ontology_ref": ename,
```

6.3 `_make_axiom_shape()`:
```python
"entry_type": _resolve_entry_type("code"),  # 公理源自代码语义
"ontology_ref": from_name or None,
```

6.4 `_convert_properties()`:
```python
"entry_type": _resolve_entry_type(prop.get("source", "rdb")),
"ontology_ref": f"{concept_name}.{prop_name}",
```

6.5 `_convert_relations()`:
```python
"entry_type": _resolve_entry_type(rel.get("source", "rdb")),
"ontology_ref": f"{domain_name} --[{rel_upper}]--> {range_name}",
```

---

### Task 7: 更新存量测试文件

**Objective:** 机械替换字段名 + `shape.target.resource_type` → `shape.target.entry_type`。

**Files:**
- Modify: `tests/unit/test_shape_cross_validation.py`
- Modify: `tests/unit/execution/test_action_executor_shapes.py`
- Modify: `tests/unit/execution/test_submission_criteria_migration.py`
- Modify: `tests/unit/test_cli_validate_shapes.py`

**变更：** 所有 `resource_type=` → `entry_type=`，`.target.resource_type` → `.target.entry_type`，内联 YAML key 改名。

---

### Task 8: 新增 ontology_loader 单元测试

**Objective:** 覆盖 `_DOMAIN_TO_ENTRY_TYPE` 映射和 `ontology_ref` 生成。

**Files:**
- Create: `tests/unit/test_ontology_loader_output.py`

```python
"""ontology_loader 输出 entry_type + ontology_ref 测试。"""
import json, tempfile
from pathlib import Path
from ontoagent.pipeline.ontology_loader import (
    load_ontology_to_shapes,
    _resolve_entry_type,
)


def test_resolve_entry_type_known_source():
    assert _resolve_entry_type("rdb") == "ResourceEntity"
    assert _resolve_entry_type("code") == "CodeEntity"
    assert _resolve_entry_type("doc") == "DocEntity"


def test_resolve_entry_type_unknown_source():
    assert _resolve_entry_type("garbage") == "ResourceEntity"


def test_load_minimal_ontology_emits_entry_type_and_ontology_ref():
    ontology = {
        "version": "1.0",
        "domain": "ecommerce",
        "entity_types": [
            {
                "id": "concept_001",
                "name": "订单",
                "source": "rdb",
                "source_ref": "table:order",
                "is_entity_type": True,
                "confidence": 0.85,
            }
        ],
        "axioms": [],
        "properties": [],
        "relations": [],
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(ontology, f)
        tmp_path = f.name

    shapes = load_ontology_to_shapes(
        tmp_path, include_axioms=False, include_properties=False, include_relations=False
    )
    Path(tmp_path).unlink()

    assert len(shapes) == 1
    target = shapes[0]["target"]
    assert target["entry_type"] == "ResourceEntity"
    assert target["ontology_ref"] == "订单"
```

---

### Task 9: 全量回归 + 端到端验证

**Step 1: 全量测试**
```bash
uv run pytest tests/ -q
```
预期：~1715 passed（Task 0 加 7 条 + Task 8 加 3 条）

**Step 2: 电商端到端**
```bash
uv run python -m ontoagent.pipeline.ontology_loader \
  /tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json \
  /tmp/ecommerce_shapes_v2.yaml
uv run ontoagent validate-shapes --path /tmp/ecommerce_shapes_v2.yaml
```
预期：校验通过（0 错误）

**Step 3: 确认 effective_severity**
```bash
grep -A1 "confidence: 0\.6" /tmp/ecommerce_shapes_v2.yaml | head -5
```
确认低置信度 Shape 的 severity 被降级。

---

### Task 10: ruff + commit（不自动 push）

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
git add -A
git commit -m "refactor: split ShapeTarget.resource_type into entry_type + ontology_ref"
```
提交后等用户确认再 push。

---

## 改后效果预期

**Before：**
```yaml
target:
  resource_type: 客户    # ❌ 校验失败
```

**After：**
```yaml
target:
  entry_type: ResourceEntity    # ✅ 标准标签
  ontology_ref: 客户             # ✅ 域语义
```

---

## Risk

| 风险 | 缓解 |
|------|------|
| YAML breaking change | `from_yaml_dict` 保留 `resource_type` fallback |
| frozen dataclass hash 变化 | ShapeTarget 当前不用于 dict 键/set 元素，无实际影响。已在此文档中记录。 |
| 位置参数兼容 | `ontology_ref` 放字段最后，旧代码 `ShapeTarget("X", op, None)` 仍正确 |
| /tmp 临时文件依赖 | 电商 ontology 测试经 Task 8 纳入 pytest，CI 可复现 |
