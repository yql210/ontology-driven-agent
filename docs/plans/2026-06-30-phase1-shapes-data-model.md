# Phase 1 实施计划：数据模型 + 配置注册表

> **方案日期**: 2026-06-30 | **来源**: Claude Code 架构审查 | **审查通过**: 一次性切换，不双存

---

## 设计决策（Claude Code 强制纠正）

| # | 纠正 | 原方案 | 修正后 |
|---|------|--------|--------|
| 1 | **双存→一次性切换** | ShapeRegistry 同时加载新旧 | 完成后立即冻结旧 registry → 空 dict |
| 2 | 补 `ConstraintExpr` dataclass | 遗漏 | Phase 1 必须创建 |
| 3 | 补 `suggestion` 字段 | ConstraintShape 遗漏 | Phase 1 必须加入 |
| 4 | 补 `SELF` 路径关键字 | PathExpression 无零跳 | 用于自身属性评估（CodeEntity.entryCategory） |
| 5 | 校验 fail-fast + 批量报告 | 遇错即抛 | 累积所有错误一次性列出 |

---

## 任务清单

### Task 1: 创建枚举类

**文件**: `src/ontoagent/domain/shapes.py`（新建）

```python
class Operation(StrEnum):
    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    EXECUTE = "EXECUTE"
    EXPORT = "EXPORT"

class Severity(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"
    ESCALATE = "escalate"

class ShapeKind(StrEnum):
    STRUCTURAL = "structural"
    OPERATIONAL = "operational"
```

### Task 2: 创建 PathExpression 相关类

**文件**: `src/ontoagent/domain/shapes.py`（追加）

```python
@dataclass(frozen=True)
class PathToken:
    kind: str                      # "rel" | "self"
    value: str                     # 关系名，或 "SELF"
    quantifier: str = ""           # "" | "+" | "*" | "{1,3}"
    reverse: bool = False

@dataclass
class PathExpression:
    raw: str                       # 原始字符串，仅调试用
    tokens: list[PathToken]        # 解析后的 token 列表
    target_label: str              # 路径终点实体标签
    max_depth: int = 3             # 全局跳数上限

    def is_self(self) -> bool:
        return len(self.tokens) == 1 and self.tokens[0].kind == "self"
    
    @staticmethod
    def parse(raw: str, max_depth: int = 3) -> "PathExpression":
        """解析 SHACL 路径语法。
        
        支持: "PROCESSES_DATA -> DataAsset"  /  "CALLS+ -> CodeEntity"
              "^CALLS -> CodeEntity"  /  "CALLS / IMPLEMENTS -> CodeEntity"
              "SELF"  /  "CALLS{1,3} -> CodeEntity"
        """
        ...
```

### Task 3: 创建 ConstraintExpr

**文件**: `src/ontoagent/domain/shapes.py`（追加）

```python
@dataclass(frozen=True)
class ConstraintExpr:
    field: str                     # Neo4j 属性名（camelCase）
    operator: str = "in"          # in | not_in | equals | not_equals | exists
    value: str | list[str] | bool | None = None
    unless_field: str | None = None
    unless_value: str | list[str] | None = None
```

### Task 4: 创建 ShapeTarget + ConstraintShape

**文件**: `src/ontoagent/domain/shapes.py`（追加）

```python
@dataclass(frozen=True)
class ShapeTarget:
    resource_type: str             # 实体标签，如 "CodeEntity"
    operation: Operation           # 6 种枚举
    field_filter: dict[str, str] | None = None

@dataclass
class ConstraintShape:
    id: str                        # "shape:sensitive_data"
    name: str
    description: str
    kind: ShapeKind                # STRUCTURAL | OPERATIONAL
    target: ShapeTarget
    path: PathExpression
    constraint: ConstraintExpr
    severity: Severity
    priority: int = 0
    tags: list[str] = field(default_factory=list)
    version: str = "2"
    enabled: bool = True
    suggestion: str = ""           # ← Claude Code 强制补充
    max_depth: int = 3

    @classmethod
    def from_yaml_dict(cls, data: dict) -> "ConstraintShape": ...
```

### Task 5: 创建 shapes.yaml

**文件**: `src/ontoagent/pipeline/shapes.yaml`（新建）

迁移 3 条旧映射到新格式：

```yaml
version: "2.0"

shapes:
  - id: shape:sensitive_data
    name: 敏感数据保护
    description: "操作 restricted/confidential 数据的 CodeEntity 需要额外审查"
    kind: operational
    target:
      resource_type: CodeEntity
      operation: UPDATE
    path: "PROCESSES_DATA -> DataAsset"
    constraint:
      field: sensitivity
      operator: in
      value: [restricted, confidential]
    severity: block
    priority: 10
    tags: [data_privacy, pii]
    suggestion: |
      该代码处理敏感数据。可选:
      (a) 降级关联 DataAsset 的 sensitivity 标签
      (b) 申请临时豁免（24h TTL）
      (c) 寻找不涉及敏感数据的替代方案

  - id: shape:compliance_check
    name: 合规要求检查
    description: "操作 critical/high 级合规要求关联的 CodeEntity"
    kind: operational
    target:
      resource_type: CodeEntity
      operation: UPDATE
    path: "SUBJECT_TO -> ComplianceItem"
    constraint:
      field: severity
      operator: in
      value: [critical, high]
    severity: block
    priority: 8
    tags: [compliance, regulatory]
    suggestion: |
      该代码关联高严重性合规要求。可选:
      (a) 降级 ComplianceItem 的 severity
      (b) 申请合规豁免
      (c) 在合规沙箱环境中执行操作

  - id: shape:http_entry_point
    name: HTTP/RPC 入口点保护
    description: "修改 http_api/rpc_service 入口点需发出警告"
    kind: operational
    target:
      resource_type: CodeEntity
      operation: UPDATE
    path: "SELF"
    constraint:
      field: entryCategory
      operator: in
      value: [http_api, rpc_service]
    severity: warn
    priority: 5
    tags: [api_surface, entry_point]
    suggestion: |
      修改系统对外接口入口。建议:
      (a) 确认不破坏 API 契约
      (b) 检查下游调用方
      (c) 更新 API 文档
```

### Task 6: 创建 ShapeRegistry

**文件**: `src/ontoagent/execution/shape_registry.py`（新建）

```python
class ShapeRegistry:
    """约束形状注册表 — 倒排索引 {(resource_type, operation): [ConstraintShape]}"""

    def __init__(self, valid_labels: frozenset[str]):
        self._valid_labels = valid_labels
        self._shapes: dict[str, ConstraintShape] = {}
        self._index: dict[tuple[str, Operation], list[ConstraintShape]] = defaultdict(list)
        self._warnings: list[str] = []

    def load_from_yaml(self, yaml_path: Path) -> list[ConstraintShape]:
        """加载 shapes.yaml → 校验 → 建立索引"""

    def find(self, resource_type: str, operation: Operation) -> list[ConstraintShape]:
        """O(1) 查找，返回按 priority 降序的 Shape 列表"""

    def get(self, shape_id: str) -> ConstraintShape | None:
        """按 ID 获取（用于 explain_constraint 工具）"""

    def list_all(self) -> list[ConstraintShape]:
        """返回所有 Shape（用于 explore_ontology 工具）"""

    def validate(self) -> list[str]:
        """校验 resource_type ∈ valid_labels，批量报告所有错误。fail-fast 但全部报告完才抛异常。"""
```

### Task 7: 冻结旧注册表 + 接入新入口

**文件**: `src/ontoagent/domain/schema.py:693`（修改）

```python
# ⚠️ FROZEN — V4 Phase 1 迁移到 shapes.yaml + ShapeRegistry
# 保留为空 dict 供旧代码兼容，不再接受新增条目。
ONTOLOGY_CONSTRAINT_REGISTRY: dict[str, ConstraintFieldDescriptor] = {}
```

**文件**: `src/ontoagent/agent/tools.py`（修改，约行 721-760）

在 `_get_action_executor()` 中改为：加载 ShapeRegistry → 注入 ActionExecutor。

**文件**: `src/ontoagent/agent/prompt.py`（修改，约行 13-31）

`_build_constraint_prompt()` 改为从 ShapeRegistry 生成摘要（仅 name + description，不超过 200 token）。

### Task 8: 常量清单 + CLI 校验命令

**文件**: `src/ontoagent/domain/schema.py`（追加）

```python
ONTOLOGY_ENTITY_LABELS: frozenset[str] = frozenset({"CodeEntity", "ConceptEntity", "DocEntity", ...})
ONTOLOGY_RELATION_TYPES: frozenset[str] = frozenset({"CALLS", "EXTENDS", "PROCESSES_DATA", ...})
```

**CLI**: 新增 `ontoagent validate-shapes` 命令（给 CI 用），`--strict-shapes` flag 控制 fail-fast。

---

## 过渡策略（修正后）

```
Phase 1 完成 → ONTOLOGY_CONSTRAINT_REGISTRY = {}（冻结）
           → prompt / tools / loader 全部走 ShapeRegistry
           → 旧 ConstraintEngine 不删但不再加载新规则
Phase 3     → 删除旧 Guard Pipeline
```

---

## 交付物汇总

| # | 文件 | 操作 |
|---|------|------|
| 1 | `domain/shapes.py` | 新建：Operation, Severity, ShapeKind 枚举 + PathToken, PathExpression, ConstraintExpr, ShapeTarget, ConstraintShape |
| 2 | `execution/shape_registry.py` | 新建：ShapeRegistry（load/validate/find/get/list_all） |
| 3 | `pipeline/shapes.yaml` | 新建：3 条 Shape（sensitive_data, compliance_check, http_entry_point） |
| 4 | `domain/schema.py` | 修改：ONTOLOGY_CONSTRAINT_REGISTRY 冻结为 {} + 新增 ONTOLOGY_ENTITY_LABELS / ONTOLOGY_RELATION_TYPES 常量 |
| 5 | `agent/tools.py` | 修改：_get_action_executor() 接入 ShapeRegistry |
| 6 | `agent/prompt.py` | 修改：_build_constraint_prompt() 从 ShapeRegistry 生成 |
| 7 | `api/cli.py` | 修改：新增 validate-shapes 命令 |
| 8 | `tests/unit/test_shapes.py` | 新建：PathExpression.parse() 测试 + ShapeRegistry 单元测试 |
