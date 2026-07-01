# Bug Fix: V4 Shape 系统三个缺陷

## Bug 1: 数值比较 operator 未实现

**文件**: `src/ontoagent/execution/shape_evaluator.py`
**位置**: `_should_trigger` 方法，exists 分支之后、else 分支之前

`shapes.yaml` 中 `shape:refactor_large_code_unit` 使用了 `operator: ">"` `value: 100`，
但 `_should_trigger` 只处理 `in/not_in/equals/not_equals/exists`，未知 operator 走 `logger.warning + return False`。

**修复**：在 exists 分支后、else 分支前，加四个数值比较分支：

```python
        if op == ">":
            return any(v > constraint.value for v in values)
        if op == "<":
            return any(v < constraint.value for v in values)
        if op == ">=":
            return any(v >= constraint.value for v in values)
        if op == "<=":
            return any(v <= constraint.value for v in values)
```

**测试**: 在 `tests/unit/test_shape_evaluator.py` 中加测试用例，验证四个新 operator 的 True/False 场景。
**验证命令**: `uv run pytest tests/unit/test_shape_evaluator.py -v`

---

## Bug 2: _intersect_suggestions 对中文无效

**文件**: `src/ontoagent/execution/decision_fuser.py`
**位置**: `_intersect_suggestions` 函数（行 121-157）

当前用 `str.split()` 取词级交集，对中文输出无意义的 `"(a) 的"`。
多条 BLOCK Shape 冲突时，融合后的建议是乱码。

**修复**：改为按行合并去重（保持原文语义完整）：

```python
def _intersect_suggestions(suggestions: list[str]) -> str:
    """对多条 suggestion 按行去重合并。"""
    if not suggestions:
        return ""
    lines: list[str] = []
    seen: set[str] = set()
    for s in suggestions:
        for line in s.split("\n"):
            stripped = line.strip()
            if stripped and stripped not in seen:
                lines.append(stripped)
                seen.add(stripped)
    return "\n".join(lines)
```

**测试**: 更新 `tests/unit/test_decision_fuser.py` 中对应测试。
**验证命令**: `uv run pytest tests/unit/test_decision_fuser.py -v`

---

## Bug 3: ESCALATE 当作 BLOCK 处理

**文件**: `src/ontoagent/execution/action_executor.py`
**位置**: `_check_with_shapes` 方法中 ESCALATE 分支（约行 157-164）

当前 ESCALATE 返回 `ActionResult(success=False)`，和 BLOCK 行为完全一样。
ESCALATE 语义是"升级人工审批"，不应直接阻断。

**修复**：ESCALATE 不阻断，而是把 triggered shapes 详情放进 warnings，标记 approval_required：

在 ESCALATE 分支改为：

```python
        if report.severity is Severity.ESCALATE:
            escalated = report.suggestion or "操作需要人工审批"
            triggered_info = [
                f"shape:{t['shape_id']}[{t['severity']}] {t.get('evidence', {}).get('field','')}"
                for t in report.triggered
            ]
            return None, [escalated, f"approval_required: {', '.join(triggered_info)}"]
```

这样 ESCALATE 不阻断执行（block_reason=None），但 warnings 中包含完整的审批信息供调用方处理。

**测试**: 更新 `tests/unit/execution/test_action_executor_shapes.py`。
**验证命令**: `uv run pytest tests/unit/execution/test_action_executor_shapes.py -v`

---

## 执行顺序

1. Bug 1 → 跑 `uv run pytest tests/unit/test_shape_evaluator.py -v`
2. Bug 2 → 跑 `uv run pytest tests/unit/test_decision_fuser.py -v`
3. Bug 3 → 跑 `uv run pytest tests/unit/execution/test_action_executor_shapes.py -v`
4. 全量: `uv run pytest --tb=short -q 2>&1 | tail -5`
