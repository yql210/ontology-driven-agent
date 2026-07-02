# Task 5.0: shapes.yaml 扩展 + ShapeRegistry allow_set

## Context

OntoAgent V5 Phase 5 — Guard Pipeline 退役，Shape 成为唯一约束入口。需要补充等价于被替代 Guards 的 Shape 定义。

**关键发现:**
- `submission_criteria` 全部是 `"entity exists"` → 已被 `ActionExecutor._resolve_entity()` 覆盖，无需新 Shape
- `constraint_overrides.yaml` 中 `allow_all` 全被注释 → 无活跃白名单条目。但需保留 allow_set 机制

**PathCompiler 已验证支持 quantifier:** `^CALLS{1,5}` 生成 `<-[:CALLS*1..5]-`

## 实施（强制 TDD）

### 1. shapes.yaml 末尾追加 Shape

```yaml
  - id: shape:upstream_call_chain_risk
    name: 上游调用链入口点检查
    description: "反向遍历CALLS调用链，检查上游是否有HTTP/RPC入口点"
    kind: operational
    target:
      resource_type: CodeEntity
      operation: UPDATE
    path: "^CALLS{1,5} -> CodeEntity"
    constraint:
      field: entryCategory
      operator: in
      value: [http_api, rpc_service]
    severity: warn
    priority: 6
    tags: [call_chain, entry_point, upstream]
    suggestion: |
      调用链上游存在HTTP/RPC入口点。建议:
      (a) 确认修改不影响API契约
      (b) 检查下游调用方兼容性
```

文件: `src/ontoagent/pipeline/shapes.yaml`

### 2. ShapeRegistry 新增 allow_set

文件: `src/ontoagent/execution/shape_registry.py`

```python
class ShapeRegistry:
    def __init__(self, valid_labels: set[str], allow_set: set[str] | None = None) -> None:
        self._labels = valid_labels
        self._shapes: dict[str, dict[Operation, list[ConstraintShape]]] = {}
        self._allow_set: set[str] = allow_set or set()

    def is_allowed(self, entity_label: str, entity_name: str) -> bool:
        """Check if entity is whitelisted (short-circuit ALLOW)."""
        return f"{entity_label}:{entity_name}" in self._allow_set
```

### 3. ShapeEvaluator 开头调用 allow_set

文件: `src/ontoagent/execution/shape_evaluator.py`

在 `evaluate()` 方法最开头，遍历 labels 之前：

```python
def evaluate(self, entity, capabilities):
    entity_id = str(entity.get("id", ""))
    labels = entity.get("labels") or []
    entity_name = entity.get("name", "")

    # Allow-set short circuit: whitelisted entities skip all shape checks
    for label in labels:
        if self._registry.is_allowed(label, entity_name):
            return []

    results = []
    ...
```

### 4. 测试（RED 先写）

文件: `tests/unit/test_shape_evaluator.py`（追加）

```python
def test_allow_set_short_circuit():
    """Whitelisted entity returns empty results regardless of shapes."""
    ...

def test_upstream_risk_multihop():
    """Shape with ^CALLS{1,5} path evaluates multi-hop correctly."""
    ...
```

### 5. 验证

```bash
uv run pytest tests/unit/test_shape_evaluator.py -v
# 全部通过
```

### 6. Commit

```bash
git add -A && git commit -m "feat: Phase 5.0 — propagation Shapes + allow_set in ShapeRegistry" && git push
```
