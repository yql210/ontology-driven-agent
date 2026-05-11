# Phase 3 Day 5 实施计划

> 基于 v2 方案（8.8/10 审核），分 4 个 Block 实施

## Block 1：评分逻辑重构（P0）

### Task 1.1：修改 calculate_tool_match 返回 float
- 文件：`tests/evaluation/run_eval.py`
- 修改：第 45-49 行，`calculate_tool_match()` 从 `bool` 改为 `float`（覆盖率）
- 新签名：`def calculate_tool_match(expected: list[str], actual: list[str]) -> float:`
- 逻辑：`return len(expected_set & actual_set) / len(expected_set) if expected_set else 1.0`
- 同步修改 JSON 输出中 `tool_match` 字段（第 162 行）改为 `round(tool_match, 2)`

### Task 1.2：修改 exact 匹配逻辑
- 文件：`tests/evaluation/run_eval.py`
- 修改：第 57-59 行 `calculate_answer_match()` 的 `exact` 分支
- 新逻辑：先尝试子串匹配，如果匹配则检查 expected 是否是某个更长 token 的子串（反向包含检查），如果是则降为 0.0
- 代码：
```python
if answer_type == "exact":
    expected_value = str(expected.get("value", "")).lower().strip()
    if expected_value not in actual_lower:
        return 0.0
    # 检查假阳性：expected 是 actual 中某个更长词的子串
    import re
    words = re.findall(r'[a-z_]+', actual_lower)
    for w in words:
        if expected_value in w and w != expected_value:
            return 0.0
    return 1.0
```

### Task 1.3：更新 test_run_eval.py
- 文件：`tests/evaluation/test_run_eval.py`
- 修改：所有断言 `tool_match` 为 `bool` 的地方改为 `float`
- 添加新测试：`test_tool_match_coverage`（覆盖率为 0.5, 1.0 等）
- 添加新测试：`test_exact_match_no_false_positive`

### Task 1.4：验证 Block 1
- `uv run pytest tests/evaluation/test_run_eval.py -v`
- 确认所有测试通过

## Block 2：Prompt + 工具优化

### Task 2.1：优化 prompt.py
- 文件：`src/layerkg/agent/prompt.py`
- 修改：在现有 prompt 末尾（第 52 行之后）追加以下内容：
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

### Task 2.2：修改 get_module_tree 返回名称
- 文件：`src/layerkg/agent/tools.py`
- 修改：第 197-199 行 `get_module_tree()` 函数体
- 新逻辑：
```python
clustering = get_clustering()
tree = clustering.get_module_tree()

# 将 entity_ids (UUID) 转换为实体名称
neo4j = get_neo4j()
enriched_tree = {}
for module_name, info in tree.items():
    entity_names = []
    for eid in info.get("entities", [])[:10]:  # 最多 10 个
        try:
            node = neo4j.get_node(eid)
            if node and node.get("name"):
                entity_names.append(node["name"])
        except Exception:
            pass
    enriched_tree[module_name] = {
        "entity_count": info.get("entity_count", 0),
        "cohesion": round(info.get("cohesion", 0.0), 3),
        "entity_sample": entity_names,
    }

return json.dumps(enriched_tree, ensure_ascii=False, indent=2)
```

### Task 2.3：验证 Block 2
- `uv run pytest tests/ -v`
- `uv run ruff check src/ tests/`

## Block 3：评估集扩展（+10 题）

### Task 3.1：Hermes 验证 Neo4j 数据并设计新题
- Hermes 用 Cypher 查询验证数据存在性
- 设计 10 道新题，写入 JSON 文件
- 每题必须有 expected_tools（最少必要）和 expected_answer（fuzzy/contains 为主）

### Task 3.2：Claude Code 将新题添加到 eval_set.json
- 保持现有 25 题不变
- 在 questions 数组末尾添加 10 道新题

### Task 3.3：重跑全部 35 题评估
- `uv run python -m tests.evaluation.run_eval`
- 分析结果，确认准确率 ≥ 80%

## Block 4：收尾

### Task 4.1：全量验证
- `uv run pytest tests/ -v`
- `uv run ruff check src/ tests/`

### Task 4.2：Git commit
- `git add -A && git commit -m "feat(phase3-day5): 评分重构 + Prompt优化 + 工具修复 + 评估扩展 (80%+ accuracy)"`

### Task 4.3：更新记录
- 追加思源笔记进展记录
- 更新 layerkg-dev-workflow skill 状态
