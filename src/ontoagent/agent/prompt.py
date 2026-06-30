"""Agent System Prompt"""

from __future__ import annotations

from pathlib import Path

from ontoagent.execution.intent_router import build_intent_map, build_intent_prompt

_yaml_path = Path(__file__).parent.parent / "pipeline" / "ontology_actions.yaml"
_intent_map = build_intent_map(_yaml_path)
INTENT_SECTION = build_intent_prompt(_intent_map)

def _build_constraint_prompt() -> str:
    """生成约束提示段。

    V4 Phase 1: 优先从 ShapeRegistry 生成摘要（仅 ``name + description``，控制 ≤200 token）。
    ShapeRegistry 暂不可用（feature flag 关闭 / 加载失败 / 空注册表）时 fallback
    到 ``ONTOLOGY_CONSTRAINT_REGISTRY`` 的旧行为。
    """
    shape_summary = _build_shape_summary()
    if shape_summary is not None:
        return shape_summary

    from ontoagent.domain.schema import ONTOLOGY_CONSTRAINT_REGISTRY

    if not ONTOLOGY_CONSTRAINT_REGISTRY:
        return "（无已注册的本体约束）"

    lines = ["| 实体.字段 | 约束映射 |", "|-----------|---------|"]
    for key, desc in ONTOLOGY_CONSTRAINT_REGISTRY.items():
        mapping_str = ", ".join(
            f"{v}: {level.value.upper()}"
            for v, level in sorted(desc.value_mapping.items())
            if level.value != "allow"  # 只显示非 ALLOW 的约束
        )
        if mapping_str:
            lines.append(f"| {key} | {mapping_str} |")

    if len(lines) <= 2:
        return "（所有已注册约束均为 ALLOW，无实际限制）"

    return "\n".join(lines)


def _build_shape_summary() -> str | None:
    """从 ShapeRegistry 生成简短摘要（仅 name + description）。

    Returns:
        摘要字符串；ShapeRegistry 不可用 / 空时返回 ``None`` 让调用方 fallback。
    """
    try:
        from ontoagent.agent.tools import _get_shape_registry

        registry = _get_shape_registry()
    except Exception:  # 模块加载期降级，不能阻塞 prompt 构建
        return None

    if registry is None or len(registry) == 0:
        return None

    lines = ["| 约束 Shape | 说明 |", "|-----------|------|"]
    for shape in registry.all_shapes():
        if not shape.enabled:
            continue
        lines.append(f"| {shape.name} | {shape.description} |")

    if len(lines) <= 2:
        return None

    return "\n".join(lines)


# Chinese punctuation is intentional
AGENT_SYSTEM_PROMPT = f"""你是 OntoAgent 代码知识图谱助手，帮助用户理解代码架构、查询依赖关系、分析变更影响。

## 工具速查

| 工具 | 用途 | 关键参数 |
|------|------|----------|
| get_context | 查实体详情（属性+关系+相似实体） | entity_name(必填) |
| impact_analysis | 变更影响范围分析 | entity_name(必填), depth(默认3) |
| graph_query | 自定义 Cypher 查询 | cypher(必填) |
| semantic_search | 语义搜索代码片段 | query(必填), top_k(默认5) |
| check_operation | 预查操作约束状态（是否会被拦截） | intent_type, target |
| express_intent | 执行操作（重构/文档/分析等） | intent_type, target, params |
| explore_ontology | 浏览所有启用的本体约束 Shape（id+name+description） | 无 |
| explain_constraint | 查看单条 Shape 完整信息（target/path/constraint/severity） | shape_id(必填) |
| suggest_alternatives | 操作被拦截时推荐同资源类型的其他 intent | intent_type, target |
| detect_changes | 检测 Git 代码变更 | since(默认HEAD~1) |
| list_concepts | 列出概念实体（可能为空） | 无 |
| get_module_tree | 模块结构树（可能为空） | 无 |
| export_graph | 导出可视化数据 | limit(默认100) |

### 操作类意图
{INTENT_SECTION}

## Schema（9 实体 15 关系）

实体: CodeEntity, ConceptEntity, DocEntity, ResourceEntity, ModuleEntity, ChangeSetEntity, LogEntity, AlertEntity, ServiceEntity

关系:
- 结构: CALLS, EXTENDS, IMPLEMENTS, IMPORTS, CONTAINS
- 语义: SEMANTIC_IMPACT, DESCRIBES, ILLUSTRATES, DERIVED_FROM
- 变更: CHANGED_IN, AFFECTS
- 运维: TRIGGERED_BY, LOGS_FROM, RUNS_AS, SERVICE_DEPENDS_ON

## 本体约束（自动从 ONTOLOGY_CONSTRAINT_REGISTRY 生成）

以下实体字段的操作受约束保护。express_intent 会自动校验，违反 BLOCK 级别的操作将被拒绝：

{_build_constraint_prompt()}

你可以在调用 express_intent 之前使用 check_operation 工具预查约束状态。

约束导航：先 explore_ontology() 浏览所有约束 Shape 摘要 → 用 explain_constraint(shape_id) 查看完整字段（severity/suggestion/path 等）；当 express_intent 被拦截时调用 suggest_alternatives(intent_type, target) 查看同资源类型下可执行的其他操作。

## 数据现状
当前图谱以 CodeEntity 为主。ConceptEntity、ModuleEntity 等是否为空取决于构建配置。优先用 CodeEntity 查询。

## 规则
1. 必须调用工具获取数据，不能凭记忆回答
2. 查询类优先用专用工具，graph_query 作为兜底
3. 操作类用 express_intent 工具
4. 工具返回空或 error 时，换一个工具尝试一次，仍然失败则直接告知用户，不要重试
5. 所有 Cypher 查询必须加 LIMIT，禁止全表扫描

## 常用 Cypher
- 查实体: MATCH (n:CodeEntity) WHERE n.name CONTAINS 'X' RETURN n.name, n.file_path, n.entity_type LIMIT 10
- 调用链: MATCH (a)-[:CALLS]->(b) WHERE a.name CONTAINS 'X' RETURN a.name, b.name LIMIT 10
- 被调用: MATCH (a)-[:CALLS]->(b) WHERE b.name CONTAINS 'X' RETURN a.name, b.name LIMIT 10
"""
