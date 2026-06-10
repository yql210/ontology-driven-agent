# LayerKG V3.4 Phase E — 反思审查 + Gap Audit

## 目标
设计稿 vs 实际代码全面对账，修复偏差，清理残留。

## Gap 清单

### Gap 1: 旧 OntologyEngine 测试未清理（严重）
- `tests/unit/test_ontology_engine.py` — 仍在测试 OntologyEngine（已 deprecated）
- `tests/unit/test_approval.py` — 仍在测试 OntologyEngine 审批
- `tests/integration/test_ontology_integration.py` — 仍在测试 OntologyEngine 生命周期
- `tests/unit/test_actions_code.py` — 仍在 import layerkg.actions.code
- `tests/unit/test_actions_alert.py` — 仍在 import layerkg.actions.alert
- **风险：** 旧测试通过不代表新代码正确，且维护成本高

### Gap 2: ontology_actions_legacy.yaml 残留
- test_ontology_engine.py 和 test_ontology_integration.py 引用 ontology_actions_legacy.yaml
- 这个文件应该已被 ontology_actions.yaml 替代

### Gap 3: FunctionRunner 未注入 ActionExecutor（功能缺失）
- ActionExecutor 构造函数接受 function_runner 参数
- 但 tools.py 中 express_intent 创建 ActionExecutor 时未注入 FunctionRunner
- 导致 Function 执行不经过重试/熔断/fallback
- **风险：** Phase B 的基础设施没有被使用

### Gap 4: 通用 Function 未注册到 builtin
- functions/general.py 有 register_all() 但未被 import
- ActionExecutor 执行时可能找不到 query_entity 等通用 Function

### Gap 5: Connector 与 FunctionRunner 未对接
- ConnectorRegistry 未注入到 FunctionRunner
- 设计稿中 Connector 是 Function 访问外部系统的通道
- 当前断裂

---

## Task 1: 清理旧测试文件
- 删除 tests/unit/test_ontology_engine.py
- 删除 tests/unit/test_approval.py
- 删除 tests/integration/test_ontology_integration.py
- 删除 tests/unit/test_actions_code.py
- 删除 tests/unit/test_actions_alert.py
- 删除 src/layerkg/ontology_actions_legacy.yaml（如果存在）
- 跑全量测试确认无断裂

## Task 2: express_intent 注入 FunctionRunner
- 在 tools.py 中 express_intent 创建 ActionExecutor 时注入 FunctionRunner
- FunctionRunner 需要单例（避免每次请求重建熔断器状态）
- 确认 Function 执行经过重试/熔断路径
- 测试：mock FunctionRunner 验证被调用

## Task 3: 确保通用 Function 注册
- 在应用启动时（graph.py 或 tools.py）import general.py 的 register_all()
- 测试：验证 query_entity 等在 ActionExecutor 中可调用

## Task 4: Connector 注入 FunctionRunner（可选）
- FunctionRunner 构造函数接受 connector_registry
- 暂不实现具体对接，只确认接口预留
- 非阻塞项

## Task 5: 全量验证 + 文档更新
- uv run pytest tests/ -v 全量通过
- uv run ruff check/format clean
- 更新思源笔记进展记录

---

## 执行批次

| 批次 | Tasks | 内容 | max-turns |
|------|-------|------|-----------|
| Batch 1 | 1 | 清理旧测试文件 | 30 |
| Batch 2 | 2-3 | 注入 FunctionRunner + 注册通用 Function | 40 |
| Batch 3 | 4-5 | 验证 + 文档 | 30 |
