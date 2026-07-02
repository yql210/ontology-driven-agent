修改 src/ontoagent/agent/tools.py:

1. 删除 L959-1011 (Guard Pipeline 构建块)
2. 替换为:
   shape_registry = _get_shape_registry()
   _action_executor = ActionExecutor(graph_store, function_runner=_get_function_runner(), shape_registry=shape_registry)
3. 删除不再需要的 imports: ActionGuardPipeline, ConstraintEngine, ConstraintPropagator, EntityExistsGuard, EntityPropertyGuard, OntologyPropagationGuard, OntologyTraversalGuard, WhitelistGuard, OntologyConstraintLoader
4. 保留审批 gate wiring + shape_registry 逻辑
