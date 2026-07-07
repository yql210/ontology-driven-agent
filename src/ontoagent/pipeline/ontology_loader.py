"""OntologyAutoGen 输出的 ontology.json → shapes.yaml 格式转换器。

将 OntologyAutoGen 自动构建的本体文件转换为 OntoAgent 运行时的约束 Shape 定义，
串联两个项目的接口层。

用法:
    python -m ontoagent.pipeline.ontology_loader ./ontology.json [shapes.yaml]

    from ontoagent.pipeline.ontology_loader import (
        load_ontology_to_shapes,
        write_shapes_yaml,
    )
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------
# 常量
# ---------------------------------------------------------------

SEVERITY_ORDER: list[str] = ["allow", "warn", "block", "escalate"]

# confidence 阈值
MIN_CONFIDENCE = 0.7  # 低于此值丢弃
MEDIUM_CONFIDENCE = 0.9  # 低于此值降级

# axiom_type → (severity, path, direction)
AXIOM_TYPE_MAP: dict[str, tuple[str, str, str]] = {
    "DOMAIN": ("warn", "SELF", "forward"),
    "RANGE": ("warn", "SELF", "forward"),
    "DISJOINT_WITH": ("block", "SELF", "bidirectional"),
    "EQUIVALENT_CLASS": ("escalate", "SELF", "bidirectional"),
}

# 本体数据来源 → Neo4j 实体标签（图遍历入口）
_DOMAIN_TO_ENTRY_TYPE: dict[str, str] = {
    "rdb": "ResourceEntity",
    "doc": "DocEntity",
    "code": "CodeEntity",
    "unknown": "ResourceEntity",
}


def _resolve_entry_type(source: str) -> str:
    """根据数据来源决定图入口标签。"""
    return _DOMAIN_TO_ENTRY_TYPE.get(source, "ResourceEntity")


# DDL 外键关系名 → OntoAgent 标准关系类型映射
# 规则：所有 has*/belongs_to*/references* 类外键关系统一映射到 CONTAINS
# 因为 DDL 外键语义本质是"从属/包含"，CONTAINS 是标准关系白名单中最接近的
_RELATION_NAME_MAP: dict[str, str] = {
    "HAS_CUSTOMER": "CONTAINS",
    "HAS_ORDER": "CONTAINS",
    "HAS_PRODUCT": "CONTAINS",
}


def _resolve_relation_type(rel_upper: str) -> str:
    """将 DDL 生成的关系名映射到 OntoAgent 标准关系类型。

    Args:
        rel_upper: UPPER_SNAKE 关系名（如 HAS_CUSTOMER）。

    Returns:
        标准关系类型（如 CONTAINS）。未映射的原样返回。
    """
    return _RELATION_NAME_MAP.get(rel_upper, rel_upper)


# ---------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------


def _camel_to_upper_snake(name: str) -> str:
    """将 camelCase 转为 UPPER_SNAKE_CASE。

    >>> _camel_to_upper_snake("hasCustomer")
    'HAS_CUSTOMER'
    >>> _camel_to_upper_snake("orderId")
    'ORDER_ID'
    >>> _camel_to_upper_snake("HAS_CUSTOMER")
    'HAS_CUSTOMER'
    """
    # 已经是全大写 + 下划线形式，直接返回
    if re.match(r"^[A-Z][A-Z0-9_]*$", name):
        return name
    # 在小写→大写 或 数字→大写 或 小写→数字 的边界插入下划线
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([a-zA-Z])(\d)", r"\1_\2", s)
    return s.upper()


def _lower_severity(severity: str, levels: int = 1) -> str:
    """将 severity 降 `levels` 级，不低于最低级。

    >>> _lower_severity("block", 1)
    'warn'
    >>> _lower_severity("warn", 2)
    'allow'
    """
    if severity not in SEVERITY_ORDER:
        return severity
    idx = SEVERITY_ORDER.index(severity)
    return SEVERITY_ORDER[max(0, idx - levels)]


def _build_concept_map(data: dict) -> dict[str, dict]:
    """从 ontology.json 构建 concept_id → concept 的查找表。"""
    concept_map: dict[str, dict] = {}
    for entity in data.get("entity_types", []):
        concept_map[entity["id"]] = entity
    return concept_map


# ---------------------------------------------------------------
# 各来源的转换函数
# ---------------------------------------------------------------


def _convert_entity_types(data: dict) -> list[dict]:
    """每个 entity_type → 一条 Shape。

    规则:
    - is_entity_type=False → 跳过
    - entry_type 由 entity.source 经 _resolve_entry_type 映射得出
    - ontology_ref = entity.name（细粒度语义引用）
    """
    shapes: list[dict] = []
    _build_concept_map(data)
    for entity in data.get("entity_types", []):
        if not entity.get("is_entity_type", True):
            continue
        eid = entity["id"]
        ename = entity.get("name", eid)
        desc = entity.get("description") or f"实体类型: {ename}"
        confidence = float(entity.get("confidence", 0.5))

        shapes.append(
            {
                "id": f"shape:entity_{eid}",
                "name": f"实体约束: {ename}",
                "description": desc,
                "kind": "structural",
                "target": {
                    "entry_type": _resolve_entry_type(entity.get("source", "unknown")),
                    "operation": "UPDATE",
                    "ontology_ref": ename,
                },
                "path": "SELF",
                "constraint": {
                    "field": "name",
                    "operator": "in",
                    "value": [ename],
                },
                "severity": "warn",
                "suggestion": f"操作实体类型 '{ename}'（来源: {entity.get('source', 'unknown')}）",
                "confidence": min(confidence, 1.0),
                "source": "imported",
                "rationale": f"源自 OntologyAutoGen 实体提取，来源: {entity.get('source', 'unknown')}",
            }
        )

    return shapes


def _make_axiom_shape(
    axiom_type: str,
    path_expr: str,
    severity: str,
    confidence: float,
    suggestion: str,
    rationale: str,
    suffix: str,
    from_name: str,
    to_name: str,
) -> dict:
    """构建单条公理对应的 Shape 字典。"""
    return {
        "id": f"shape:axiom_{axiom_type.lower()}_{suffix}",
        "name": f"公理约束: {axiom_type}",
        "description": f"{axiom_type} 约束: {from_name} → {to_name}",
        "kind": "operational",
        "target": {
            "entry_type": _resolve_entry_type("code"),
            "operation": "UPDATE",
            "ontology_ref": from_name or None,
        },
        "path": path_expr,
        "constraint": {
            "field": "name",
            "operator": "in",
            "value": [f"{axiom_type}:{to_name}" if to_name else axiom_type],
        },
        "severity": severity,
        "suggestion": suggestion or f"{axiom_type} 约束检查",
        "confidence": min(confidence, 1.0),
        "source": "imported",
        "rationale": rationale,
    }


def _convert_axioms(data: dict) -> list[dict]:
    """每个 axiom → 一条或多条 Shape。

    规则:
    - confidence < 0.7 → 丢弃
    - confidence 0.7-0.9 → severity 降一级
    - DOMAIN / RANGE → severity=WARN, path=SELF
    - DISJOINT_WITH → severity=BLOCK, 双向各一条
    - EQUIVALENT_CLASS → severity=ESCALATE
    - rationale → suggestion
    """
    shapes: list[dict] = []
    concept_map = _build_concept_map(data)

    for axiom in data.get("axioms", []):
        axiom_type = axiom.get("axiom_type", "")
        confidence = float(axiom.get("confidence", 1.0))

        # 低置信度丢弃
        if confidence < MIN_CONFIDENCE:
            continue

        mapping = AXIOM_TYPE_MAP.get(axiom_type)
        if mapping is None:
            # 未知 axiom_type → 跳过
            continue

        base_severity, path_expr, direction = mapping
        rationale = axiom.get("rationale", "")
        suggestion = rationale  # rationale → suggestion
        subject = axiom.get("subject", {})
        obj = axiom.get("obj", {})

        # 提取概念名
        subj_id = subject.get("concept_id", "") if isinstance(subject, dict) else str(subject)
        obj_id = obj.get("concept_id", "") if isinstance(obj, dict) else str(obj)

        subj_name = concept_map.get(subj_id, {}).get("name", subj_id) if subj_id else ""
        obj_name = concept_map.get(obj_id, {}).get("name", obj_id) if obj_id else ""

        # 根据 confidence 调整 severity
        severity = base_severity
        if confidence < MEDIUM_CONFIDENCE:
            severity = _lower_severity(severity, 1)

        if direction == "bidirectional" and subj_name and obj_name:
            shapes.append(
                _make_axiom_shape(
                    axiom_type, path_expr, severity, confidence, suggestion, rationale, "forward", subj_name, obj_name
                )
            )
            shapes.append(
                _make_axiom_shape(
                    axiom_type, path_expr, severity, confidence, suggestion, rationale, "reverse", obj_name, subj_name
                )
            )
        elif subj_name:
            shapes.append(
                _make_axiom_shape(
                    axiom_type, path_expr, severity, confidence, suggestion, rationale, "single", subj_name, obj_name
                )
            )
        else:
            shapes.append(
                _make_axiom_shape(axiom_type, path_expr, severity, confidence, suggestion, rationale, "single", "", "")
            )

    return shapes


def _convert_properties(data: dict) -> list[dict]:
    """每个 enum 类型的 property → 一条 Shape（constraint.operator=in）。

    规则:
    - value_type 为 "enum" 且有 enum_values 才生成
    - constraint.field = property.name
    - constraint.operator = "in"
    - constraint.value = enum_values
    """
    shapes: list[dict] = []
    concept_map = _build_concept_map(data)

    for prop in data.get("properties", []):
        value_type = prop.get("value_type", "")
        enum_values = prop.get("enum_values", [])
        if value_type != "enum" or not enum_values:
            continue

        domain_concept_id = prop.get("domain_concept_id", "")
        concept = concept_map.get(domain_concept_id, {})
        concept_name = concept.get("name", domain_concept_id) if concept else domain_concept_id

        prop_name = prop["name"]
        desc = prop.get("description") or f"枚举属性: {prop.get('name_cn', prop_name)}"
        confidence = float(prop.get("confidence", 1.0))

        shapes.append(
            {
                "id": f"shape:enum_{prop.get('id', prop_name)}",
                "name": f"枚举约束: {concept_name}.{prop_name}",
                "description": desc,
                "kind": "operational",
                "target": {
                    "entry_type": _resolve_entry_type(prop.get("source", "rdb")),
                    "operation": "UPDATE",
                    "ontology_ref": f"{concept_name}.{prop_name}",
                },
                "path": "SELF",
                "constraint": {
                    "field": "name",
                    "operator": "in",
                    "value": list(enum_values),
                },
                "severity": "block",
                "suggestion": f"'{prop_name}' 仅允许以下值: {', '.join(enum_values)}",
                "confidence": min(confidence, 1.0),
                "source": "imported",
                "rationale": f"属性 '{prop_name}' 的枚举值来源于 {prop.get('source', 'rdb')}",
            }
        )

    return shapes


def _convert_relations(data: dict) -> list[dict]:
    """每个 relation → 一条 Shape（path 表达式）。

    规则:
    - path = "RELATION_NAME -> TargetConcept"
      relation_name 从 camelCase 转为 UPPER_SNAKE
    - constraint.field = _relation
    - constraint.operator = equals
    - constraint.value = relation_name
    - 低置信度 (< 0.7) 的 relation 降 severity
    """
    shapes: list[dict] = []
    concept_map = _build_concept_map(data)

    for rel in data.get("relations", []):
        domain_id = rel.get("domain_concept_id", "")
        range_id = rel.get("range_concept_id", "")

        domain_concept = concept_map.get(domain_id, {})
        range_concept = concept_map.get(range_id, {})

        domain_name = domain_concept.get("name", domain_id) if domain_concept else domain_id
        range_name = range_concept.get("name", range_id) if range_concept else range_id

        rel_name = rel.get("name", "RELATED_TO")
        rel_upper = _camel_to_upper_snake(rel_name)
        rel_standard = _resolve_relation_type(rel_upper)

        confidence = float(rel.get("confidence", 0.5))
        cardinality = rel.get("cardinality", "1:N")

        severity = "warn"
        if confidence < MIN_CONFIDENCE:
            severity = "allow"

        shapes.append(
            {
                "id": f"shape:rel_{rel.get('id', rel_name)}",
                "name": f"关系约束: {rel_name}",
                "description": (f"{domain_name} --[{rel_upper}]--> {range_name} (基数: {cardinality})"),
                "kind": "structural",
                "target": {
                    "entry_type": _resolve_entry_type(rel.get("source", "rdb")),
                    "operation": "UPDATE",
                    "ontology_ref": f"{domain_name} --[{rel_upper}]--> {range_name}",
                },
                "path": f"{rel_standard} -> ResourceEntity",
                "constraint": {
                    "field": "name",
                    "operator": "in",
                    "value": [rel_upper],
                },
                "severity": severity,
                "suggestion": f"实体 '{domain_name}' 与 '{range_name}' 通过关系 '{rel_upper}' 关联",
                "confidence": min(confidence, 1.0),
                "source": "imported",
                "rationale": f"关系 '{rel_name}' 源自 {rel.get('source', 'rdb')}: {rel.get('source_ref', '')}",
            }
        )

    return shapes


# ---------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------


def load_ontology_to_shapes(
    ontology_path: str | Path,
    *,
    include_entity_types: bool = True,
    include_axioms: bool = True,
    include_properties: bool = True,
    include_relations: bool = True,
) -> list[dict]:
    """加载 ontology.json 并转换为 shapes 字典列表。

    Args:
        ontology_path: OntologyAutoGen 输出的 ontology.json 文件路径。
        include_entity_types: 是否包含 entity_type → shape 转换。
        include_axioms: 是否包含 axiom → shape 转换。
        include_properties: 是否包含 property(enum) → shape 转换。
        include_relations: 是否包含 relation → shape 转换。

    Returns:
        shapes 字典列表，每个元素可被 ConstraintShape.from_yaml_dict() 解析。
    """
    path = Path(ontology_path)
    if not path.exists():
        raise FileNotFoundError(f"ontology.json 不存在: {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    shapes: list[dict] = []

    if include_entity_types:
        shapes.extend(_convert_entity_types(data))

    if include_axioms:
        shapes.extend(_convert_axioms(data))

    if include_properties:
        shapes.extend(_convert_properties(data))

    if include_relations:
        shapes.extend(_convert_relations(data))

    return shapes


def write_shapes_yaml(
    shapes: list[dict],
    output_path: str | Path | None = None,
) -> str:
    """将 shapes 列表写入 YAML 文件或返回 YAML 字符串。

    Args:
        shapes: 由 load_ontology_to_shapes() 返回的 shapes 字典列表。
        output_path: 可选输出文件路径；为 None 则返回 YAML 字符串。

    Returns:
        YAML 格式的 shapes 字符串。
    """
    output = {
        "version": "2.0",
        "shapes": shapes,
    }
    yaml_str = yaml.dump(output, allow_unicode=True, default_flow_style=False, sort_keys=False)

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(yaml_str)

    return yaml_str


# ---------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """命令行入口: python -m ontoagent.pipeline.ontology_loader <input> [output]。

    输入: ontology.json (OntologyAutoGen 输出)
    输出: shapes.yaml 文件路径或 stdout
    """
    args = argv or sys.argv[1:]
    if not args:
        print("用法: python -m ontoagent.pipeline.ontology_loader <ontology.json> [output.yaml]", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args[0])
    if not input_path.exists():
        print(f"错误: 文件不存在: {input_path}", file=sys.stderr)
        sys.exit(2)

    shapes = load_ontology_to_shapes(input_path)
    output_path = args[1] if len(args) > 1 else None
    yaml_output = write_shapes_yaml(shapes, output_path)
    if output_path is None:
        sys.stdout.write(yaml_output)
    else:
        print(f"已生成 {len(shapes)} 条 Shape → {output_path}")


if __name__ == "__main__":
    main()
