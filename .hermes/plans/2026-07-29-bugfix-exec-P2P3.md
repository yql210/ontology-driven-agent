# 执行任务：P2 + P3 Bug Fixes

## 任务 6: P2-#6+#7 — batch 写入提升到 GraphStore ABC + 调用方改造

### 6a: GraphStore ABC 添加默认 batch 实现

**文件**: `src/ontoagent/store/graph_store.py`

在 GraphStore ABC 中添加两个带有默认实现的方法（循环调单条），这样所有子类自动获得正确性，NebulaGraphStore/Neo4jStore 已有的覆写不受影响：

```python
def merge_nodes_batch(self, label: str, properties_list: list[dict], batch_size: int = 200) -> int:
    """批量写入节点。默认实现：循环调 merge_node。子类可覆写以提供真正的批量优化。"""
    for props in properties_list:
        self.merge_node(label, props)
    return len(properties_list)

def merge_relations_batch(self, relations: list[dict], batch_size: int = 200) -> int:
    """批量写入关系。默认实现：循环调 merge_relation。子类可覆写以提供真正的批量优化。
    
    relations 中每个 dict 含: source_id, target_id, rel_type, properties(可选), 
    source_label(可选), target_label(可选)
    """
    for rel in relations:
        self.merge_relation(
            source_id=rel["source_id"],
            target_id=rel["target_id"],
            rel_type=rel["rel_type"],
            properties=rel.get("properties"),
            source_label=rel.get("source_label"),
            target_label=rel.get("target_label"),
        )
    return len(relations)
```

**注意**: 先检查 NebulaGraphStore 和 Neo4jStore 已有的 merge_nodes_batch / merge_relations_batch 的签名是否兼容（参数名、返回值），如果不兼容需要调整 ABC 的签名。

### 6b: module_clustering.py save_modules 改用 batch

**文件**: `src/ontoagent/pipeline/module_clustering.py`（save_modules 方法）

当前 save_modules 逐个调 merge_node + merge_relation。改为：
1. 先收集所有 ModuleEntity 的 properties dict 到一个 list
2. 调用 `self._neo4j_store.merge_nodes_batch("ModuleEntity", module_props_list)`
3. 收集所有 contains 关系到 relations list
4. 调用 `self._neo4j_store.merge_relations_batch(relations_list)`

**注意**: relations list 中每个 dict 的格式必须与 ABC 定义一致。

### 6c: builder.py Doc-Code Link 改用 batch

**文件**: `src/ontoagent/pipeline/builder.py`（L604-618 的 Doc-Code Link 循环）

当前逐条调 `graph_store.merge_relation`。改为收集后批量写入。

**测试**: 已有的 save_modules 测试和 builder 测试应该能覆盖。确保不破坏现有行为。

---

## 任务 7: P3-#4 — SchemaVersion ORDER BY 改裸列名

**文件**: `src/ontoagent/store/schema_version.py`（L93-99 Nebula 分支）

将 `ORDER BY sv.applied_at DESC` 改为 `ORDER BY applied_at DESC`（使用 RETURN 别名）。

**注意**: 这个改动风险低——有 try/except 兜底。如果原来能工作，改后也应该能工作（RETURN 别名在 ORDER BY 中是 SQL 标准）。

---

## 任务 8: P3-#11 — marker 改为 integration

**文件**: `tests/unit/test_ontology_loader.py`（L756）

将 `@pytest.mark.unit` 改为 `@pytest.mark.integration`。

---

执行约束：
- 先做 6a（ABC），验证签名兼容后再做 6b/6c
- P3 的两个任务很简单，最后做
- 不要碰方案外的文件
- 每个任务后运行相关测试
