# Phase 3 Day 5 方案 v2（修订版）

> 基于 Claude Code 审核（8.2/10）修订，解决 3 个关键问题

## 一、审核反馈与修订

### 审核评分：8.2/10 → 目标 9.5+

### 关键修订项

| # | 审核问题 | 修订方案 |
|---|---------|---------|
| 1 | `calculate_tool_match` 过于刚性（布尔值） | 改为 **覆盖率评分**：`expected_set ∩ actual_set / expected_set` |
| 2 | L3 根因分析有误（实际调了工具） | 不再归因为"未调工具"，改为优化 expected_tools 设计 |
| 3 | Prompt 优化缺具体文案 | 补充完整 prompt 文案 |

## 二、代码修改详情

### 2.1 修改 `tests/evaluation/run_eval.py`（评分逻辑重构）

**当前**（第 45-49 行）：
```python
def calculate_tool_match(expected: list[str], actual: list[str]) -> bool:
    expected_set = set(expected)
    actual_set = set(actual)
    return expected_set.issubset(actual_set)
```

**改为覆盖率评分**：
```python
def calculate_tool_match(expected: list[str], actual: list[str]) -> float:
    """计算工具调用覆盖率（0.0-1.0）"""
    if not expected:
        return 1.0
    expected_set = set(expected)
    actual_set = set(actual)
    covered = expected_set & actual_set
    return len(covered) / len(expected_set)
```

**同步修改 `total_score` 计算**（第 155-160 行），因为 `tool_match` 从 `bool` 变为 `float`：
```python
# 原代码已兼容 float，无需额外修改
# tool_match * 0.3 + answer_score * 0.7  # L1
# tool_match * 0.4 + answer_score * 0.6  # L2
# tool_match * 0.5 + answer_score * 0.5  # L3
```

**同步修改 `report["details"]` 的 `tool_match` 字段**：从 `bool` 改为 `float`，report JSON 中保留两位小数。

**修复 `exact` 匹配假阳性**（第 57-59 行）：
```python
if answer_type == "exact":
    expected_value = str(expected.get("value", "")).lower().strip()
    # 改为：去掉 Agent 回答中的标点后精确匹配
    import re
    clean_actual = re.sub(r'[^\w\s]', '', actual_lower)
    clean_expected = re.sub(r'[^\w\s]', '', expected_value)
    return 1.0 if clean_expected == clean_actual.split()[0] if len(clean_actual.split()) <= 3 else clean_expected in clean_actual else 0.0
```

**问题**：上面的正则方案太复杂。更简单的方案：
```python
if answer_type == "exact":
    expected_value = str(expected.get("value", "")).lower().strip()
    # 精确匹配：expected 应作为独立 token 出现
    tokens = actual_lower.replace(',', ' ').replace('.', ' ').replace('，', ' ').replace('。', ' ').split()
    return 1.0 if expected_value in tokens else 0.0
```

### 2.2 修改 `src/layerkg/agent/tools.py`（get_module_tree 返回名称）

**当前**（第 197-199 行）：
```python
clustering = get_clustering()
tree = clustering.get_module_tree()
return json.dumps(tree, ensure_ascii=False, indent=2)
```

**改为**：后处理 tree，将 entity_ids 转换为实体名称
```python
clustering = get_clustering()
tree = clustering.get_module_tree()

# 将 entity_ids (UUID) 转换为实体名称
neo4j = get_neo4j()
enriched_tree = {}
for module_name, info in tree.items():
    entity_names = []
    for eid in info.get("entities", [])[:10]:  # 最多显示 10 个
        node = neo4j.get_node(eid)
        if node and node.get("name"):
            entity_names.append(node["name"])
    enriched_tree[module_name] = {
        "entity_count": info.get("entity_count", 0),
        "cohesion": round(info.get("cohesion", 0.0), 3),
        "entity_sample": entity_names,  # 名称列表，非 UUID
    }

return json.dumps(enriched_tree, ensure_ascii=False, indent=2)
```

### 2.3 修改 `src/layerkg/agent/prompt.py`（Prompt 优化）

**新增内容**（在现有 prompt 末尾追加）：

```
【复杂问题处理策略】
- 遇到"分析"、"关系"、"流程"、"影响"类问题，先用 graph_query 或 get_context 收集实体和关系
- 如果一个工具返回空结果，立即换用其他工具（如 semantic_search → graph_query）
- 同一个工具连续失败 2 次，不要再重试，改用其他策略
- 收集到足够信息后，用中文综合总结，不要继续调用工具

【工具选择决策树】
- 想找代码片段/文件 → semantic_search
- 想查关系/依赖/调用链 → graph_query
- 想分析变更影响 → impact_analysis（需要实体名）+ detect_changes
- 想了解某个实体的全部信息 → get_context
- 想看项目整体结构 → get_module_tree 或 list_concepts
- 想导出可视化数据 → export_graph

【更多 Cypher 查询模板】
6. 继承关系：MATCH (a:CodeEntity)-[:EXTENDS]->(b:CodeEntity) WHERE a.name CONTAINS '关键词' RETURN a.name, b.name
7. 导入关系：MATCH (a:CodeEntity)-[:IMPORTS]->(b:CodeEntity) WHERE a.name CONTAINS '关键词' RETURN a.name, b.name LIMIT 20
8. 概念派生：MATCH (c:ConceptEntity)<-[:DERIVED_FROM]-(e:CodeEntity) WHERE c.name CONTAINS '关键词' RETURN c.name, e.name LIMIT 20
9. 实体统计：MATCH (n:CodeEntity) RETURN n.entity_type AS type, count(n) AS count
10. 路径查询：MATCH path=(a:CodeEntity)-[:CALLS*1..3]->(b:CodeEntity) WHERE a.name = '起始名' RETURN path LIMIT 10
```

### 2.4 修改 `tests/evaluation/eval_set.json`（新增 10 道题）

**新增题目设计标准**：
1. 每题必须先用 Cypher 验证 Neo4j 中有对应数据
2. `expected_tools` 改为"最少必要工具"（Agent 多调工具不扣分）
3. `expected_answer` 避免硬编码精确数量，用 fuzzy/contains 匹配

**新增 10 题分布**：
- L1: +2 题（L1-011, L1-012）— EXTENDS 关系查询、IMPORTS 关系查询
- L2: +4 题（L2-011 ~ L2-014）— 跨模块分析、概念-代码关联、路径查询
- L3: +4 题（L3-006 ~ L3-009）— 架构分析、多跳推理

**具体题目待验证 Neo4j 数据后设计**（实施阶段 Hermes 验证 + Claude Code 编码）

### 2.5 更新 `tests/evaluation/test_run_eval.py`（适配 tool_match 改为 float）

现有测试断言 `tool_match` 为 `bool`，需更新为 `float`。

## 三、不改什么

- LangGraph 状态图结构（graph.py）
- Agent 8 个工具的底层调用逻辑
- Neo4j/ChromaDB 数据
- Day 1-4 已提交的代码
- 不引入新依赖

## 四、实施计划（4 个 Block）

### Block 1：评分逻辑重构（run_eval.py + 测试）
1. `calculate_tool_match()` 改为返回 float（覆盖率）
2. `calculate_answer_match()` 的 exact 分支改为 token 级匹配
3. 更新 test_run_eval.py 适配新返回类型
4. 验证：运行 `uv run pytest tests/evaluation/test_run_eval.py -v`

### Block 2：Prompt + 工具优化（prompt.py + tools.py）
1. prompt.py 追加复杂问题处理策略 + 工具选择决策树 + 更多 Cypher 模板
2. tools.py 的 get_module_tree 后处理（UUID→名称）
3. 验证：`uv run pytest tests/ -v`

### Block 3：评估集扩展（eval_set.json）
1. Hermes 用 Cypher 验证 Neo4j 数据，设计 10 道新题
2. Claude Code 将新题写入 eval_set.json
3. 重跑全部 35 题评估

### Block 4：收尾
1. 全量测试 + ruff check
2. Git commit
3. 思源笔记进展记录
4. 更新 layerkg-dev-workflow skill

## 五、预期效果

| 指标 | 当前 | 优化后预期 |
|------|------|-----------|
| 总准确率 | 72% | 80%+ |
| L1 | 100% | 100% |
| L2 | 60% | 70%+ |
| L3 | 40% | 55%+ |
| 评估题目数 | 25 | 35 |
| tool_match 公平性 | bool（刚性） | float（覆盖率） |

## 六、风险与缓解

| 风险 | 缓解 |
|------|------|
| exact token 匹配太严格 | 先跑分对比，如果不降就用严格版 |
| 35 题评估 ~25min | 可接受 |
| get_module_tree 多次 Neo4j 查询慢 | 限制 10 个实体 + 缓存 |
| 新题数据不存在 | 每题先 Cypher 验证 |
