# Phase 2：业务实体 + 桥接关系 — 实施计划 V2

> **For Hermes:** 使用 delegate_task 逐任务执行。
> **Design:** V2.0（接口驱动）, Phase 1 已完成 1251 tests，`business_` 字段已在 CodeEntity。

**Goal:** DataAsset + ComplianceItem 实体 + 3 条桥接关系 + YAML 配置加载 + migration + 2 个 Action。

**Architecture:** 仿 ConceptEntity 模式加 dataclass，RELATION_CONSTRAINTS 追 3 条。TDD：先写测试 RED，再实现 GREEN。

**Pre-existing:** schema.py 559 行，现有 9 实体 + 15 关系 + 4 Action。

---

## 前置确认

```bash
uv run pytest tests/unit/ -q  # 1251 passed
git log --oneline -6           # c83fd98..3ecc97c (Phase 1)
```

---

### Task 1: schema.py 注册表更新（无新实体，只加注册项）

**Files:** `src/ontoagent/domain/schema.py`

**1a. VALID_ENTITY_LABELS** — 约 schema.py:347，在现有 set 最后追加 `"DataAsset"`, `"ComplianceItem"`（找到 `VALID_ENTITY_LABELS = {` 行）。

**1b. VALID_RELATION_TYPES** — 约 schema.py:361，在现有 set 最后追加 `"processes_data"`, `"subject_to"`, `"governed_by"`。

**1c. RELATION_TYPE_TO_NEO4J** — 约 schema.py:381，在现有 dict 最后追加 3 条（全大写映射: `PROCESSES_DATA`, `SUBJECT_TO`, `GOVERNED_BY`）。

**1d. RELATION_CONSTRAINTS** — 约 schema.py:521（`service_depends_on` 之后），追加：

```python
    "processes_data": RelationConstraint(
        domain="CodeEntity", range="DataAsset",
        description="代码处理了数据资产",
    ),
    "subject_to": RelationConstraint(
        domain="CodeEntity", range="ComplianceItem",
        description="代码受合规要求约束",
    ),
    "governed_by": RelationConstraint(
        domain="DataAsset", range="ComplianceItem",
        description="数据资产受合规要求约束",
    ),
```

**验证:**
```bash
uv run pytest tests/unit/test_schema.py tests/unit/test_schema_extra.py -v -x
```

**Commit:**
```bash
git add src/ontoagent/domain/schema.py
git commit -m "feat(schema): register DataAsset/ComplianceItem labels and 3 bridge relations"
```

---

### Task 2: DataAsset 实体（TDD: 先测试 RED）

**Files:**
- Create: `tests/unit/test_data_asset.py`
- Modify: `src/ontoagent/domain/schema.py`（在 `ConceptEntity` 类后插入）

**Step 1: 测试（RED）**

```python
import pytest
from ontoagent.domain.schema import DataAsset
from ontoagent.domain.exceptions import SchemaValidationError

class TestDataAsset:
    def test_construct_minimal(self):
        da = DataAsset(name="手机号", description="用户手机", sensitivity="confidential", data_type="pii")
        assert da.name == "手机号"
        assert da.aliases == []

    def test_construct_with_aliases(self):
        da = DataAsset(name="手机号", description="...", sensitivity="confidential", data_type="pii",
                       aliases=["phone", "mobile"])
        assert "phone" in da.aliases

    def test_invalid_sensitivity_raises(self):
        with pytest.raises(SchemaValidationError):
            DataAsset(name="x", description="x", sensitivity="INVALID", data_type="pii")

    def test_invalid_data_type_raises(self):
        with pytest.raises(SchemaValidationError):
            DataAsset(name="x", description="x", sensitivity="internal", data_type="INVALID")

    def test_empty_name_raises(self):
        with pytest.raises(SchemaValidationError):
            DataAsset(name="", description="x", sensitivity="internal", data_type="pii")
```

运行: `.venv/bin/pytest tests/unit/test_data_asset.py -v` → 预期 5 FAIL (NameError: DataAsset not defined)

**Step 2: 实现（GREEN）**

在 `src/ontoagent/domain/schema.py` 的 `ConceptEntity` 类定义之后插入 DataAsset dataclass（字段: name, description, sensitivity, data_type, aliases: list[str], id: UUID, created_at: ISO, `__post_init__` 校验 sensitivity/data_type/name）。

运行: `.venv/bin/pytest tests/unit/test_data_asset.py -v` → 5 PASS

**Commit:**
```bash
git add tests/unit/test_data_asset.py src/ontoagent/domain/schema.py
git commit -m "feat(schema): add DataAsset entity with validation"
```

---

### Task 3: ComplianceItem 实体（TDD: 先测试 RED）

**Files:**
- Create: `tests/unit/test_compliance_item.py`
- Modify: `src/ontoagent/domain/schema.py`（在 `DataAsset` 后插入）

**Step 1: 测试（RED）**

```python
class TestComplianceItem:
    def test_construct_minimal(self):
        ci = ComplianceItem(name="GDPR-17", description="删除权", regulation="GDPR", severity="critical", requirement="30天内删除")
        assert ci.name == "GDPR-17"
        assert ci.regulation == "GDPR"

    def test_invalid_severity_raises(self):
        with pytest.raises(SchemaValidationError):
            ComplianceItem(name="x", description="x", regulation="x", severity="INVALID", requirement="x")

    def test_empty_name_raises(self):
        with pytest.raises(SchemaValidationError):
            ComplianceItem(name="", description="x", regulation="x", severity="low", requirement="x")
```

**Step 2: 实现**

插入 ComplianceItem dataclass（字段: name, description, regulation, severity, requirement, id, created_at, VALID_SEVERITIES, `__post_init__`）。

**验证:**
```bash
uv run pytest tests/unit/test_compliance_item.py -v  # 3 PASS
uv run pytest tests/unit/test_schema.py -v -x        # 无回归
```

**Commit:**
```bash
git add tests/unit/test_compliance_item.py src/ontoagent/domain/schema.py
git commit -m "feat(schema): add ComplianceItem entity with validation"
```

---

### Task 4: entity_to_dict 序列化 + Neo4j 标签

**Files:**
- Modify: `src/ontoagent/store/neo4j_store.py` — 找 `ENTITY_LABELS`，追加 `"DataAsset"`, `"ComplianceItem"`
- Modify: `src/ontoagent/pipeline/builder_utils.py` — 新增 2 个序列化函数

**builder_utils.py 新增:**

```python
def data_asset_to_dict(entity: DataAsset) -> dict[str, object]:
    d = {"id": entity.id, "name": entity.name, "description": entity.description,
         "sensitivity": entity.sensitivity, "data_type": entity.data_type,
         "created_at": entity.created_at}
    if entity.aliases:
        d["aliases"] = entity.aliases
    return d

def compliance_item_to_dict(entity: ComplianceItem) -> dict[str, object]:
    d = {"id": entity.id, "name": entity.name, "description": entity.description,
         "regulation": entity.regulation, "severity": entity.severity,
         "requirement": entity.requirement, "created_at": entity.created_at}
    return d
```

**验证:**
```bash
uv run pytest tests/unit/ -x -q  # 全量无回归
```

**Commit:**
```bash
git add src/ontoagent/store/neo4j_store.py src/ontoagent/pipeline/builder_utils.py
git commit -m "feat(store): add DataAsset/ComplianceItem Neo4j labels and serialization"
```

---

### Task 5: Schema Migration（Neo4j 约束创建）

**Files:**
- Create: `src/ontoagent/store/migrations/v1.1.0_add_business_entities.py`
- Modify: `src/ontoagent/store/migrations/registry.py` — 注册新 migration

**v1.1.0 迁移内容:**

```python
MIGRATION_110 = {
    "version": "1.1.0",
    "description": "Add DataAsset and ComplianceItem entities with constraints",
    "up": [
        "CREATE CONSTRAINT data_asset_id IF NOT EXISTS FOR (d:DataAsset) REQUIRE d.id IS UNIQUE",
        "CREATE CONSTRAINT compliance_item_id IF NOT EXISTS FOR (c:ComplianceItem) REQUIRE c.id IS UNIQUE",
    ],
    "down": [
        "DROP CONSTRAINT data_asset_id IF EXISTS",
        "DROP CONSTRAINT compliance_item_id IF EXISTS",
    ],
}
```

**验证:**
```bash
uv run pytest tests/unit/test_schema_version.py -v -x
```

**Commit:**
```bash
git add src/ontoagent/store/migrations/v1.1.0_add_business_entities.py src/ontoagent/store/migrations/registry.py
git commit -m "feat(migration): add v1.1.0 migration for DataAsset/ComplianceItem constraints"
```

---

### Task 6: YAML 业务配置 + 加载器

**Files:**
- Create: `src/ontoagent/pipeline/business_ontology.yaml`
- Create: `src/ontoagent/pipeline/business_loader.py`
- Create: `tests/unit/pipeline/test_business_loader.py`

**business_ontology.yaml:** 3 个 DataAsset（手机号/支付密码/交易金额）+ 3 个 ComplianceItem（GDPR-17/PCI-DSS 3.4/数据安全法）。

**business_loader.py 核心签名:**

```python
def load_business_ontology(yaml_path: str | Path) -> tuple[list[DataAsset], list[ComplianceItem]]:
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    assets = [DataAsset(**item) for item in data.get("data_assets", [])]
    items = [ComplianceItem(**item) for item in data.get("compliance_items", [])]
    return assets, items
```

**测试:** 验证 YAML 加载后 DataAsset.aliases / ComplianceItem.regulation 正确。

**验证:**
```bash
uv run pytest tests/unit/pipeline/test_business_loader.py -v  # 2-3 tests
```

**Commit:**
```bash
git add src/ontoagent/pipeline/business_ontology.yaml src/ontoagent/pipeline/business_loader.py tests/unit/pipeline/test_business_loader.py
git commit -m "feat(pipeline): add business ontology YAML config and loader"
```

---

### Task 7: 数据资产映射逻辑

**Files:**
- Create: `src/ontoagent/pipeline/data_mapper.py`
- Create: `tests/unit/pipeline/test_data_mapper.py`

**核心签名 + 算法:**

```python
def map_code_to_data_assets(
    code_entities: list[CodeEntity], data_assets: list[DataAsset]
) -> list[tuple[str, str]]:
    """返回 (code_entity_id, data_asset_id) 匹配对。

    策略: DataAsset.aliases 中的每个词在 CodeEntity.name 中做不区分大小写的子串匹配。
    """
    pairs = []
    for asset in data_assets:
        for alias in asset.aliases:
            alias_lower = alias.lower()
            for ce in code_entities:
                if alias_lower in ce.name.lower():
                    pairs.append((ce.id, asset.id))
                    break  # 一个 asset 对一个 entity 只匹配一次
    return pairs
```

**测试:** 3 个场景 — 精确匹配、无匹配、多个别名匹配。

**验证:**
```bash
uv run pytest tests/unit/pipeline/test_data_mapper.py -v
```

**Commit:**
```bash
git add src/ontoagent/pipeline/data_mapper.py tests/unit/pipeline/test_data_mapper.py
git commit -m "feat(pipeline): add data asset alias-to-code mapper"
```

---

### Task 8: 2 个新 Action + 2 个新 Function

**Files:**
- Modify: `src/ontoagent/pipeline/ontology_actions.yaml`
- Create: `src/ontoagent/execution/functions/check_compliance.py`
- Create: `src/ontoagent/execution/functions/trace_business_impact.py`

**ontology_actions.yaml 追加:** `compliance_check` + `business_impact_analysis`（trigger_hint + submission_criteria + functions 列表）。

**check_compliance.py 核心逻辑:**
- 输入: `ctx.match_data["target_id"]` (CodeEntity ID)
- 查询: `MATCH (c:CodeEntity {id: $id})-[:processes_data]->(d:DataAsset)-[:governed_by]->(ci:ComplianceItem) RETURN d.name, ci.name, ci.requirement`
- 输出: 合规风险列表（无匹配 = 无风险）

**trace_business_impact.py 核心逻辑:**
- 输入: `ctx.match_data["target_id"]`
- 查询: 反向 BFS CALLS → 追溯到有 entry_category 的入口 → 返回入口列表 + business_priority

**验证:**
```bash
uv run pytest tests/unit/execution/ -v -x
```

**Commit:**
```bash
git add src/ontoagent/pipeline/ontology_actions.yaml src/ontoagent/execution/functions/check_compliance.py src/ontoagent/execution/functions/trace_business_impact.py
git commit -m "feat(execution): add compliance_check and business_impact_analysis actions"
```

---

### Task 9: 全量回归 + 端到端测试

**Files:**
- Create: `tests/unit/test_phase2_integration.py`

**测试场景:**
1. YAML 加载 → map_code_to_data_assets → 验证匹配对
2. CodeEntity(business_owner="支付团队") → 正向调用链 → business_impact_analysis

**验证:**
```bash
uv run pytest tests/unit/ -x -q  # ~1350+ tests
```

**Commit:**
```bash
git add tests/unit/test_phase2_integration.py && git commit -m "test: add Phase 2 integration tests"
```

---

## 最终质量门

```bash
uv run pytest tests/unit/ -v          # ~1350 tests
uv run ruff check src/ tests/          # clean
```

---

## 改动量

| 文件 | 新增行 | 类型 |
|------|--------|------|
| `domain/schema.py` | ~120 (注册表+2实体) | 修改 |
| `store/neo4j_store.py` | ~2 | 修改 |
| `pipeline/builder_utils.py` | ~25 | 修改 |
| `pipeline/business_ontology.yaml` | ~45 | 新建 |
| `pipeline/business_loader.py` | ~40 | 新建 |
| `pipeline/data_mapper.py` | ~45 | 新建 |
| `pipeline/ontology_actions.yaml` | ~20 | 修改 |
| `execution/functions/check_compliance.py` | ~50 | 新建 |
| `execution/functions/trace_business_impact.py` | ~60 | 新建 |
| `store/migrations/v1.1.0_...` | ~35 | 新建 |
| `tests/` (6 个新文件) | ~350 | 新建 |
| **合计** | **~792** | |
