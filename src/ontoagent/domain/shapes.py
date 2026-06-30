from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

# ============================================================
# 枚举
# ============================================================


class Operation(StrEnum):
    """Agent 意图触发的操作类型。"""

    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    EXECUTE = "EXECUTE"
    EXPORT = "EXPORT"


class Severity(StrEnum):
    """约束触发后的处置级别。"""

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"
    ESCALATE = "escalate"


class ShapeKind(StrEnum):
    """Shape 分类：结构约束 vs 操作约束。"""

    STRUCTURAL = "structural"
    OPERATIONAL = "operational"


# ============================================================
# Path 表达式
# ============================================================


@dataclass(frozen=True)
class PathToken:
    """路径上的一个 token：关系跳跃或自身引用。

    Attributes:
        kind: "rel" 表示关系跳跃，"self" 表示零跳。
        value: 关系名（UPPER_SNAKE）或 "SELF"。
        quantifier: 量词，可选 ""、"+"、"*"、"{m,n}"。
        reverse: 是否反向遍历（SHACL `^`）。
    """

    kind: str
    value: str
    quantifier: str = ""
    reverse: bool = False


@dataclass
class PathExpression:
    """SHACL 风格路径表达式。

    Attributes:
        raw: 原始字符串，仅用于调试与回显。
        tokens: 解析后的 token 列表。
        target_label: 路径终点实体标签（PascalCase）。
        max_depth: 全局跳数上限，默认 3。
    """

    raw: str
    tokens: list[PathToken]
    target_label: str
    max_depth: int = 3

    def is_self(self) -> bool:
        """是否为零跳 SELF 路径。"""
        return len(self.tokens) == 1 and self.tokens[0].kind == "self"

    @staticmethod
    def parse(raw: str, max_depth: int = 3) -> PathExpression:
        """解析 SHACL 路径语法。

        支持示例:
            "PROCESSES_DATA -> DataAsset"
            "CALLS+ -> CodeEntity"
            "^CALLS -> CodeEntity"
            "CALLS / IMPLEMENTS -> CodeEntity"
            "SELF"
            "CALLS{1,3} -> CodeEntity"

        Args:
            raw: 待解析的原始字符串。
            max_depth: 全局跳数上限。

        Returns:
            解析后的 PathExpression。

        Raises:
            ValueError: 当语法无法识别时。
        """
        text = raw.strip()
        if not text:
            raise ValueError(f"路径表达式为空: {raw!r}")

        # SELF 零跳
        if text == "SELF":
            return PathExpression(
                raw=raw,
                tokens=[PathToken(kind="self", value="SELF")],
                target_label="",
                max_depth=max_depth,
            )

        if "->" not in text:
            raise ValueError(f"路径表达式必须包含 '->' 终点指示符或为 'SELF': {raw!r}")

        path_part, target_part = text.split("->", 1)
        target_label = target_part.strip()
        if not target_label:
            raise ValueError(f"路径终点标签为空: {raw!r}")

        # 用 `/` 或空白切分多跳
        hop_chunks = [c.strip() for c in re.split(r"\s*/\s*", path_part.strip()) if c.strip()]
        if not hop_chunks:
            raise ValueError(f"路径缺少关系跳跃: {raw!r}")

        tokens: list[PathToken] = []
        token_re = re.compile(
            r"^(?P<reverse>\^)?"
            r"(?P<name>[A-Z][A-Z0-9_]*)"
            r"(?P<quant>\+|\*|\{\d+(?:,\d*)?\})?$"
        )
        for chunk in hop_chunks:
            m = token_re.match(chunk)
            if not m:
                raise ValueError(f"无法识别的路径 token: {chunk!r} (in {raw!r})")
            tokens.append(
                PathToken(
                    kind="rel",
                    value=m.group("name"),
                    quantifier=m.group("quant") or "",
                    reverse=bool(m.group("reverse")),
                )
            )

        if len(tokens) > max_depth:
            raise ValueError(f"路径跳数 {len(tokens)} 超过 max_depth={max_depth}: {raw!r}")

        return PathExpression(raw=raw, tokens=tokens, target_label=target_label, max_depth=max_depth)


# ============================================================
# Constraint 表达式
# ============================================================


@dataclass(frozen=True)
class ConstraintExpr:
    """单条字段的约束表达式。

    Attributes:
        field: Neo4j 属性名（camelCase），如 "sensitivity"。
        operator: 比较算子，in | not_in | equals | not_equals | exists。
        value: 比较值。operator 为 exists 时可为 None。
        unless_field: 可选的豁免字段名。
        unless_value: 豁免字段的值；命中则跳过约束。
    """

    field: str
    operator: str = "in"
    value: str | list[str] | bool | None = None
    unless_field: str | None = None
    unless_value: str | list[str] | None = None


# ============================================================
# Shape 主体
# ============================================================


@dataclass(frozen=True)
class ShapeTarget:
    """Shape 的目标：实体标签 × 操作类型 × 可选字段过滤。

    Attributes:
        resource_type: Neo4j 实体标签，如 "CodeEntity"。
        operation: 触发该 Shape 的 Operation。
        field_filter: 可选的实体属性过滤，键为属性名、值为期望值。
    """

    resource_type: str
    operation: Operation
    field_filter: dict[str, str] | None = None


@dataclass
class ConstraintShape:
    """单条约束 Shape（V4 形状约束模型）。

    Attributes:
        id: 全局唯一标识，如 "shape:sensitive_data"。
        name: 人类可读名称。
        description: 用途说明。
        kind: STRUCTURAL 或 OPERATIONAL。
        target: 目标实体与操作。
        path: 关系路径表达式（或 SELF）。
        constraint: 路径终点的字段约束。
        severity: 触发后的处置级别。
        priority: 优先级，数值越大越优先。
        tags: 自由标签集合，便于检索。
        version: Shape 模型版本，默认 "2"。
        enabled: 是否启用。
        suggestion: 触发后给 Agent 的人类可读建议。
        max_depth: 路径跳数上限。
    """

    id: str
    name: str
    description: str
    kind: ShapeKind
    target: ShapeTarget
    path: PathExpression
    constraint: ConstraintExpr
    severity: Severity
    priority: int = 0
    tags: list[str] = field(default_factory=list)
    version: str = "2"
    enabled: bool = True
    suggestion: str = ""
    max_depth: int = 3

    @classmethod
    def from_yaml_dict(cls, data: dict) -> ConstraintShape:
        """从 shapes.yaml 的字典结构构造 ConstraintShape。

        Args:
            data: YAML 解析后的单条 shape 字典。

        Returns:
            构造完成的 ConstraintShape。

        Raises:
            ValueError: 当缺少必填字段或枚举值非法时。
        """
        try:
            shape_id = data["id"]
            name = data["name"]
            description = data["description"]
        except KeyError as exc:
            raise ValueError(f"Shape 缺少必填字段: {exc}") from exc

        target_data = data.get("target") or {}
        if "resource_type" not in target_data or "operation" not in target_data:
            raise ValueError(f"Shape {shape_id!r} 缺少 target.resource_type / target.operation")

        operation = Operation(target_data["operation"])
        field_filter = target_data.get("field_filter")
        target = ShapeTarget(
            resource_type=target_data["resource_type"],
            operation=operation,
            field_filter=dict(field_filter) if field_filter else None,
        )

        max_depth = int(data.get("max_depth", 3))
        path = PathExpression.parse(data.get("path", ""), max_depth=max_depth)

        constraint_data = data.get("constraint") or {}
        if "field" not in constraint_data:
            raise ValueError(f"Shape {shape_id!r} 缺少 constraint.field")
        constraint = ConstraintExpr(
            field=constraint_data["field"],
            operator=constraint_data.get("operator", "in"),
            value=constraint_data.get("value"),
            unless_field=constraint_data.get("unless_field"),
            unless_value=constraint_data.get("unless_value"),
        )

        return cls(
            id=shape_id,
            name=name,
            description=description,
            kind=ShapeKind(data.get("kind", "operational")),
            target=target,
            path=path,
            constraint=constraint,
            severity=Severity(data.get("severity", "warn")),
            priority=int(data.get("priority", 0)),
            tags=list(data.get("tags", [])),
            version=str(data.get("version", "2")),
            enabled=bool(data.get("enabled", True)),
            suggestion=str(data.get("suggestion", "")),
            max_depth=max_depth,
        )
