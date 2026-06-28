# Phase 4A Plan: 精度打磨（三合一）

## 问题汇总

| # | 问题 | 严重度 | 根因 |
|---|------|--------|------|
| 1 | `derived_from` LLM输出被schema拒绝 | ⚠️ 3 errors | prompt要求LLM生成 Code→Concept 的 derived_from，但schema要求 source=ConceptEntity |
| 2 | Neo4j属性名 snake_case vs 文档/代码 camelCase | ⚠️ 不一致 | store层自动转snake_case，但CLAUDE.md/schema dataclass用camelCase |
| 3 | processes_data 关系始终为0条 | 🔴 功能缺失 | data_mapper匹配+PROCESSES_DATA关系写入未集成到pipeline |

---

## Fix 1: derived_from 约束修复

**方案：** 放宽 schema 约束，允许 CodeEntity → derived_from → ConceptEntity

**文件：** `src/ontoagent/domain/schema.py` 第575-578行

**改动：**
```python
# 改前：
"derived_from": RelationConstraint(
    domain="ConceptEntity",
    range="ConceptEntity",
    description="概念派生关系",
),

# 改后：
"derived_from": RelationConstraint(
    domain={"ConceptEntity", "CodeEntity"},
    range="ConceptEntity",
    description="概念派生关系（代码→概念 或 概念→概念）",
),
```

**理由：** "此代码派生自某设计模式"是合理的语义。prompt不变（已正确引导LLM输出Code→Concept）。schema放宽即可。

---

## Fix 2: Neo4j属性统一为camelCase

**方案：** 修改 Neo4j store 层，写入时使用camelCase（与schema dataclass字段名一致）

**文件：** `src/ontoagent/store/neo4j_store.py`

**改动点：**
- 找到属性名转换逻辑（dataclass field → Neo4j property key），当前用snake_case
- 改为直接使用field name（camelCase）
- 注意：已有Neo4j数据需migration或重建

**验证：** 重新build demo-service后，查询 `MATCH (c:CodeEntity) RETURN c.entityType` 应正常返回

---

## Fix 3: processes_data 关系补齐

**问题链：**
1. `map_code_to_data_assets()` 用 `aliases` 子串匹配 `CodeEntity.name` — 逻辑正确
2. 但 builder 中未调用此函数，或调用后未写入 PROCESSES_DATA 关系

**文件：** `src/ontoagent/pipeline/builder.py`

**改动点：**
1. 在 Stage 2.6 (Business Ontology) 中调用 `map_code_to_data_assets()`
2. 将返回的 pairs 写入为 `PROCESSES_DATA` 关系
3. demo-service 验证：DataAsset aliases (phone/mobile/credit_card) 应匹配到 payment_handler 中的相关函数

**预期：** 3 DataAsset 各有 ≥1 条 PROCESSES_DATA 关系

---

## 验证清单

1. `uv run ruff check src/` — 通过
2. `uv run pytest tests/ -q` — 1353+ passed
3. `uv run ontoagent build /opt/data/workspace/demo-service/ --clear` — 0 errors
4. Neo4j查询确认:
   - `derived_from` 不再报约束错误
   - `c.entityType` 属性存在
   - `PROCESSES_DATA` 关系数 > 0
