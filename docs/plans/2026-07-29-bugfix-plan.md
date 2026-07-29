# Bug 修复方案 — 用户反馈 11 条

**时间**: 2026-07-29
**状态**: 待 CC 评审

---

## P0-1: Bug #8 — _format_value double-escape

**文件**: `src/ontoagent/store/nebula_store.py:85-112`

**问题**: `_format_value` 对 list/dict/set/tuple 先 `json.dumps` 再 `.replace("\\", "\\\\")`，导致 JSON 内部的转义序列被二次翻倍。json.dumps 已经正确处理了引号和反斜杠转义，不需要额外 replace。

**修复方案**:
- list/dict/set/tuple 分支：`json.dumps` 后直接包裹引号，不再做 `.replace`
- 需要验证：nGQL 字符串字面量中双引号是否需要转义

```python
# 修改前 (L102-108):
if isinstance(value, (list, dict, set, tuple)):
    import json as _json
    serializable = sorted(value) if isinstance(value, (set, frozenset)) else value
    s = _json.dumps(serializable, ensure_ascii=False)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'

# 修改后:
if isinstance(value, (list, dict, set, tuple)):
    import json as _json
    serializable = sorted(value) if isinstance(value, (set, frozenset)) else value
    s = _json.dumps(serializable, ensure_ascii=False)
    # json.dumps 已处理转义，只需包裹外层引号
    return f'"{s}"'
```

**测试**:
- 已有测试需确认: `tests/unit/test_nebula_store.py` 中 `_format_value` 相关测试
- 新增边界测试: 包含换行符的 list/dict、嵌套 dict、空容器

---

## P0-2: Bug #3 — CLI build 吞 errors + 缺 exit code

**文件**: `src/ontoagent/api/cli.py:35-62`

**问题**: 非 verbose 模式下，aborted 时只打印 "Build complete" 不显示 errors，且没有非零退出码。

**修复方案**:
```python
# 修改后 (cli.py L55-62):
if verbose_build:
    click.echo(str(result))
else:
    if result.aborted:
        click.echo(
            f"Build ABORTED: {result.files_scanned} files scanned, "
            f"0 entities created (Stage 2 failed)", err=True
        )
        for err in result.errors:
            click.echo(f"  ERROR: {err}", err=True)
        raise click.Abort()
    click.echo(
        f"Build complete: {result.files_scanned} files scanned, "
        f"{result.entities_created} entities created, "
        f"{result.relations_created} relations created"
    )
```

**测试**:
- 测试 aborted=True 时输出到 stderr
- 测试 aborted=True 时抛 click.Abort (exit code != 0)
- 测试 aborted=False 时正常输出

---

## P1-1: Bug #1 — ModuleEntity 加 size 字段

**文件**: `src/ontoagent/domain/schema.py:222-242`, `src/ontoagent/pipeline/module_clustering.py:303,341`

**问题**: save_modules 写入 size 但 ModuleEntity dataclass 没有此字段，_EXTRA_FIELDS 也没有。

**修复方案** (CC 建议: 加 dataclass 字段，不用 _EXTRA_FIELDS):
```python
# schema.py ModuleEntity — 加 size 字段:
@dataclass
class ModuleEntity:
    name: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str | None = None
    size: int | None = None  # 新增: 模块包含的实体数量
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
```

```python
# module_clustering.py:303 — 构造时传入 size:
module = ModuleEntity(name=module_name, size=len(entity_ids))
```

```python
# module_clustering.py:341 — save_modules 中从 entity 取值:
size = cluster.module.size if cluster.module.size is not None else cluster.entity_count
```

**注意**: 不需要改 _EXTRA_FIELDS，因为 entity_field_names() 会通过 dataclass 反射自动发现 `size` → `size`（snake_to_camel 后仍是 `size`）。

**测试**:
- 验证 ModuleEntity(size=5) 构造正常
- 验证 entity_field_names("ModuleEntity") 包含 "size"

---

## P1-2: Bug #2 — 聚类全连接图加阈值

**文件**: `src/ontoagent/pipeline/module_clustering.py:118-124`

**问题**: `combinations(entities_in_file, 2)` 同文件实体全连接，大文件 O(n²) 爆炸。

**修复方案**:
```python
# 同文件虚拟边阈值: 超过此值的文件只取前 N 个实体建边
_MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES = 30

# L118-124 修改:
for _file_path, entities_in_file in file_to_entities.items():
    if len(entities_in_file) > 1:
        # 大文件截断，避免 O(n²) 边爆炸
        capped = entities_in_file[:_MAX_FILE_ENTITIES_FOR_VIRTUAL_EDGES]
        for e1, e2 in combinations(capped, 2):
            adj[e1].add(e2)
            adj[e2].add(e1)
```

**测试**:
- 大文件 (>30 实体) 只生成 C(30,2)=435 条边
- 小文件 (<=30 实体) 行为不变

---

## P1-3: Bug #10 — e2e 导入连 DB

**文件**: `tests/unit/test_final_validation.py:47-50`

**问题**: test_all_test_modules_importable 导入所有 test_*.py，包括 e2e 模块，其模块级代码触发 NebulaGraph 连接。

**修复方案** (CC 建议: 过滤 e2e 目录):
```python
# test_final_validation.py L47-50 — 增加过滤条件:
test_files = [
    f
    for f in test_files
    if "__pycache__" not in f.parts
    and ".pytest_cache" not in f.parts
    and "conftest.py" not in f.name
    and "e2e" not in f.parts  # 新增: 排除 e2e 脚本式测试
]
```

**测试**: 无需新增，验证 test_all_test_modules_importable 不再报错即可

---

## P2-1: Bug #6 — 聚类 save_modules 改 batch

**文件**: `src/ontoagent/pipeline/module_clustering.py:339-374`

**问题**: 逐个 merge_node + merge_relation，非批量。

**修复方案**: 使用 merge_nodes_batch + merge_relations_batch
- ModuleEntity 节点收集后一次性 merge_nodes_batch
- contains 关系收集后一次性 merge_relations_batch

**注意**: merge_nodes_batch 和 merge_relations_batch 是 NebulaGraphStore 的扩展方法，GraphStore ABC 没有定义。需要用 hasattr 检测或 try/except 降级到逐个写入。

---

## P2-2: Bug #7 — Doc-Code Link 改 batch

**文件**: `src/ontoagent/pipeline/builder.py:604-618`

**问题**: for rel in describes_rels 逐条 merge_relation。

**修复方案**: 同 P2-1，收集后批量写入。

---

## P3-1: Bug #4 — SchemaVersion ORDER BY 裸列名

**文件**: `src/ontoagent/store/schema_version.py:93-99`

**问题**: Nebula 分支 ORDER BY sv.applied_at 可能语义错误。

**修复方案**: 使用 RETURN 别名（裸列名）:
```python
# 修改前:
"ORDER BY sv.applied_at DESC LIMIT 1;"

# 修改后:
"ORDER BY applied_at DESC LIMIT 1;"
```

---

## P3-2: Bug #11 — marker 改为 integration

**文件**: `tests/unit/test_ontology_loader.py:756`

**修复方案**:
```python
# 修改前:
@pytest.mark.unit
class TestIntegrationWithRealFile:

# 修改后:
@pytest.mark.integration
class TestIntegrationWithRealFile:
```
