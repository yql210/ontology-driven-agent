# 执行任务：P0 + P1 Bug Fixes（5 个任务）

严格按照以下顺序逐任务执行。每个任务先写测试（RED），再改代码（GREEN），再验证。

## 任务约束
- 只修改下面列出的文件，不要碰其他文件
- 不要修改 conftest.py
- 每个任务改完后运行相关测试验证

---

## 任务 1: P0-#8 — 修正 _format_value 测试断言（非 bug，测试有误）

**背景**: _format_value 的 double-escape 是 nGQL 必需的转义行为（round-trip 正确）。但现有测试 test_list_becomes_json_string 有一个错误的 or 分支掩盖了正确行为。

**文件**: `tests/unit/test_nebula_store.py`

**操作**: 找到 `test_list_becomes_json_string` 测试函数。当前断言有两个 or 分支：
```python
assert result == '"["a", "b"]"' or result == '"[\\"a\\", \\"b\\"]"'
```
第一个分支 `'"["a", "b"]"'` 是错误的（双引号未转义，nGQL parser 会提前关闭字符串）。去掉错误的第一个分支，只保留正确的第二个分支。

**新增测试**: 添加一个 round-trip 验证测试 `test_format_value_list_round_trip`，验证 _format_value 对 `["line1\nline2", "hello"]` 的输出经过 nGQL 转义解析后能还原回原值。测试逻辑：
1. 调用 _format_value(["line1\nline2", "hello"])
2. 对输出做 nGQL 反转义（.replace("\\\\", "\\").replace('\\"', '"').replace("\\n", "\n")）
3. json.loads 还原
4. assert 还原后的值 == 原始值

---

## 任务 2: P0-#3 — CLI build 吞 errors + 缺 exit code

**文件**: `src/ontoagent/api/cli.py`（L55-62 的 build 命令输出部分）

**改动**: 在非 verbose 模式下，如果 result.aborted 为 True：
1. 输出到 stderr: `f"Build ABORTED: {result.files_scanned} files scanned, {result.entities_created} entities created"`
2. 逐条输出 errors 到 stderr: `f"  ERROR: {err}"`
3. 调用 `raise click.Abort()`

如果 aborted 为 False，保持原有输出逻辑不变。

**文件**: `tests/unit/`（新建或已有的 CLI 测试文件）

**测试**: 使用 CliRunner 测试：
- test_build_aborted_shows_errors: mocked builder 返回 aborted=True + errors，验证输出包含 "ABORTED" 和 error 内容，exit_code != 0
- test_build_success_normal_output: mocked builder 返回 aborted=False，验证正常输出 "Build complete"

---

## 任务 3: P1-#1 — ModuleEntity 加 size 到 _EXTRA_FIELDS + schema 迁移

### 3a: 修改 _EXTRA_FIELDS
**文件**: `src/ontoagent/domain/schema.py`（L549-569 的 _EXTRA_FIELDS）

在 _EXTRA_FIELDS 中添加 ModuleEntity 条目：
```python
"ModuleEntity": {
    "size",
},
```

### 3b: 创建 schema 迁移
**文件**: 新建 `src/ontoagent/store/migrations/v2_1_0_add_module_size.py`

参考已有的迁移文件模板（如 v2_0_0_add_capability_entities.py），创建迁移：
- version_from = "2.0.0"
- version_to = "2.1.0"
- upgrade: 对 NebulaGraph 执行 `ALTER TAG \`ModuleEntity\` ADD (\`size\` string);`（用 try/except 包裹保证幂等）；对 Neo4j 无需操作
- downgrade: 不需要（或 ALTER TAG DROP）

### 3c: 注册迁移
**文件**: `src/ontoagent/store/migrations/registry.py`

将新迁移注册到 _BUILTIN_MIGRATIONS 列表。

### 3d: 更新 CURRENT_SCHEMA_VERSION
**文件**: `src/ontoagent/store/schema_version.py`

将 CURRENT_SCHEMA_VERSION 从当前值升级到 "2.1.0"。

### 3e: 测试
- 验证 entity_field_names("ModuleEntity") 包含 "size"
- 验证迁移文件的 version_from/version_to 正确

**注意**: CURRENT_SCHEMA_VERSION 升级后需同步检查测试中的硬编码版本字符串（可能有测试断言旧版本号）。

---

## 任务 4: P1-#2 — 聚类虚拟边阈值（全或无策略）

**文件**: `src/ontoagent/pipeline/module_clustering.py`（L118-124）

**改动**: 添加常量 `_MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES = 30`。修改虚拟边生成逻辑为"全或无"策略：
- 文件内实体数 <= 30: 正常生成全连接虚拟边（combinations）
- 文件内实体数 > 30: 不生成虚拟边（跳过），并记录 warning 日志

```python
_MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES = 30

for _file_path, entities_in_file in file_to_entities.items():
    if len(entities_in_file) <= 1:
        continue
    if len(entities_in_file) > _MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES:
        self._logger.warning(
            "File has %d entities (> %d threshold), skipping virtual edges: %s",
            len(entities_in_file), _MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES, _file_path,
        )
        continue
    for e1, e2 in combinations(entities_in_file, 2):
        adj[e1].add(e2)
        adj[e2].add(e1)
```

**测试**（在 tests/unit/pipeline/test_module_clustering.py 中）：
- test_large_file_skips_virtual_edges: 文件有 35 个实体，验证不生成虚拟边
- test_small_file_keeps_virtual_edges: 文件有 10 个实体，验证生成 C(10,2)=45 条边

---

## 任务 5: P1-#10 — test_final_validation 过滤 e2e 目录

**文件**: `tests/unit/test_final_validation.py`（L47-50 的 test_files 列表过滤）

**改动**: 在已有的过滤条件中增加 `"e2e" not in f.parts`：
```python
test_files = [
    f
    for f in test_files
    if "__pycache__" not in f.parts
    and ".pytest_cache" not in f.parts
    and "conftest.py" not in f.name
    and "e2e" not in f.parts  # 新增
]
```

同样修改 test_all_source_modules_importable（L18-20）不需要——它只扫 src/ 目录。只改 test_all_test_modules_importable。

**验证**: 运行 `uv run pytest tests/unit/test_final_validation.py -v` 确认通过。
