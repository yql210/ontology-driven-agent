# 执行任务：P1 剩余任务（2 个）

## 任务 4: P1-#2 — 聚类虚拟边阈值（全或无策略）

**文件**: `src/ontoagent/pipeline/module_clustering.py`（L110-124 的虚拟边生成逻辑）

**改动**: 添加常量 `_MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES = 30`。修改虚拟边生成逻辑为"全或无"策略。

当前代码（L110-124）:
```python
        # 添加同文件虚拟边：同一文件内的实体两两互连
        # 按 file_path 分组
        file_to_entities: dict[str, list[str]] = {}
        for entity_id, data in entity_data.items():
            file_path = data.get("file_path")
            if file_path:
                file_to_entities.setdefault(file_path, []).append(entity_id)

        # 为每个文件内的实体添加全连接虚拟边
        for _file_path, entities_in_file in file_to_entities.items():
            if len(entities_in_file) > 1:
                # 同文件内的实体两两互连
                for e1, e2 in combinations(entities_in_file, 2):
                    adj[e1].add(e2)
                    adj[e2].add(e1)
```

修改后:
```python
        # 添加同文件虚拟边：同一文件内的实体两两互连
        # 按 file_path 分组
        file_to_entities: dict[str, list[str]] = {}
        for entity_id, data in entity_data.items():
            file_path = data.get("file_path")
            if file_path:
                file_to_entities.setdefault(file_path, []).append(entity_id)

        # 为每个文件内的实体添加全连接虚拟边
        for _file_path, entities_in_file in file_to_entities.items():
            if len(entities_in_file) <= 1:
                continue
            if len(entities_in_file) > _MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES:
                self._logger.warning(
                    "File has %d entities (> %d threshold), skipping virtual edges: %s",
                    len(entities_in_file),
                    _MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES,
                    _file_path,
                )
                continue
            for e1, e2 in combinations(entities_in_file, 2):
                adj[e1].add(e2)
                adj[e2].add(e1)
```

同时在类的上方（ModuleCluster dataclass 之后、ModuleClustering 类之前）添加常量:
```python
_MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES = 30
```

**测试**: 在 `tests/unit/pipeline/test_module_clustering.py` 中添加：
- `test_large_file_skips_virtual_edges`: 模拟一个文件有 35 个实体，验证不生成虚拟边（邻接表中这些实体之间无边）
- `test_small_file_keeps_virtual_edges`: 模拟一个文件有 5 个实体，验证生成 C(5,2)=10 条边

---

## 任务 5: P1-#10 — test_final_validation 过滤 e2e 目录

**文件**: `tests/unit/test_final_validation.py`（L47-50）

找到 test_all_test_modules_importable 函数中的 test_files 列表过滤逻辑，在现有条件中增加 `"e2e" not in f.parts`。

当前代码:
```python
    test_files = [
        f
        for f in test_files
        if "__pycache__" not in f.parts and ".pytest_cache" not in f.parts and "conftest.py" not in f.name
    ]
```

修改后:
```python
    test_files = [
        f
        for f in test_files
        if "__pycache__" not in f.parts
        and ".pytest_cache" not in f.parts
        and "conftest.py" not in f.name
        and "e2e" not in f.parts
    ]
```

验证: `uv run pytest tests/unit/test_final_validation.py -v`
