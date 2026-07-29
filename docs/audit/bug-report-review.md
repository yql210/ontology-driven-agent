# 用户反馈 Bug 清单审核

**审核人**: Hermes Agent（代码实证核查）
**审核时间**: 2026-07-29
**审核方法**: 逐条读源码验证，不信报告原文

---

## 审核结论总览

| # | Bug | 报告判定 | 我的判定 | 置信度 |
|---|-----|---------|---------|--------|
| 1 | ModuleEntity 写入 `size` prop not found | 🔴严重 | ✅ 真 bug | 100% |
| 2 | Stage 4 聚类全连接图 O(n²) 爆炸 | 🔴严重 | ✅ 真 bug | 100% |
| 3 | CLI build 吞掉 aborted/errors | 🔴严重 | ✅ 真 bug (UX) | 100% |
| 4 | SchemaVersion ORDER BY 语法 | 🟡中等 | ⚠️ 半真（夸大） | 70% |
| 5 | index "Key not existed" | 🟡中等 | ❌ 非bug（已知设计） | 90% |
| 6 | 聚类 save_modules 逐个 merge_node | 🟡中等 | ✅ 真（性能） | 100% |
| 7 | Doc-Code Link 逐条写 | 🟡中等 | ✅ 真（性能） | 100% |
| 8 | _format_value double-escape | 🟡中等 | ✅ 真 bug（数据正确性） | 100% |
| 9 | test_dockerfile_contains_uv 过时 | 🟡测试 | ❌ 假阳性 | 100% |
| 10 | test_all_test_modules_importable 连DB | 🟡测试 | ✅ 真 bug | 100% |
| 11 | TestIntegrationWithRealFile marker错 | 🟡测试 | ✅ 真（低危） | 100% |

---

## 逐条详细分析

### Bug #1: ModuleEntity 写入 size prop not found — ✅ 真 bug

**代码证据:**
- `schema.py:223-242`: `ModuleEntity` dataclass 只有 `name/id/description/created_at`，**无 `size` 字段**
- `schema.py:549-569`: `_EXTRA_FIELDS` 只有 `CodeEntity`/`DataAsset`/`CapabilityEntity` 三个条目，**没有 `ModuleEntity`**
- `module_clustering.py:354`: `save_modules()` 写入 `"size": size`（来自 `cluster.entity_count`）
- `nebula_store.py:362-367`: merge_node UPSERT 失败时明确报 "Tag prop not found" → RuntimeError

**根因确认**: 聚类写入 `size` 字段，但 Nebula Tag DDL 里没有这个属性（因为 `_EXTRA_FIELDS["ModuleEntity"]` 不存在 → `entity_field_names()` 不含 `size` → `create_tags()` 生成的 DDL 没有 `size` 列）。

**修复方案**: 在 `_EXTRA_FIELDS` 中添加 `"ModuleEntity": {"size"}`

**影响**: Stage 4 聚类在 NebulaGraph 后端必失败（RuntimeError）。但 `builder.py` Stage 4 是可降级阶段，失败会被 catch + log warning，不阻塞整体 build。不过聚类结果确实无法写入。

---

### Bug #2: Stage 4 聚类全连接图 O(n²) 爆炸 — ✅ 真 bug

**代码证据:**
- `module_clustering.py:118-124`:
  ```python
  for _file_path, entities_in_file in file_to_entities.items():
      if len(entities_in_file) > 1:
          for e1, e2 in combinations(entities_in_file, 2):
              adj[e1].add(e2)
              adj[e2].add(e1)
  ```
- **没有任何阈值限制**——一个含 N 个实体的文件会生成 C(N,2) = N*(N-1)/2 条虚拟边
- 例: 一个 100 实体的文件 = 4950 条边；10 个这样的文件 = 49500 条边
- 这还只是 _load_graph 阶段的内存构建。Label Propagation 算法本身也有 O(iterations * nodes * avg_degree) 复杂度

**修复方案**: 加阈值，如 `if len(entities_in_file) > 50: continue` 或改用更聪明的策略（如只连同类实体）

---

### Bug #3: CLI build 吞掉 errors — ✅ 真 bug (UX)

**代码证据:**
- `cli.py:55-62`:
  ```python
  if verbose_build:
      click.echo(str(result))       # 完整 BuildResult（含 aborted/errors）
  else:
      click.echo(
          f"Build complete: {result.files_scanned} files scanned, "
          f"{result.entities_created} entities created, "
          f"{result.relations_created} relations created"
      )
  ```
- `builder.py:573-585`: Stage 2 失败时返回 `BuildResult(entities_created=0, aborted=True, errors=[msg])`
- 非 verbose 模式：用户看到 `Build complete: 42 files scanned, 0 entities created, 0 relations created`
- **不显示 `aborted=True` 和 `errors`**——"Build complete" 措辞误导用户以为成功

**修复方案**: 非 verbose 模式下，如果 `result.aborted` 为 True，额外打印 aborted 和 errors 信息

---

### Bug #4: SchemaVersion ORDER BY 语法 — ⚠️ 半真（严重度被夸大）

**代码证据:**
- `schema_version.py:93-99`: Nebula 分支用 `ORDER BY sv.applied_at DESC`
- `cypher_adapter.py:105`: `_build_var_tag_map` 正则 `\(([A-Za-z_]\w*):([A-Za-z_]\w*)` **不匹配反引号** `(sv:\`SchemaVersion\`)`
- 所以 `var_tag_map` 为空 → `_fix_property_access` 不补全 `sv.applied_at` → 原样下发

**但严重度被夸大的原因:**
1. NebulaGraph 3.x 的 openCypher 子集**实际上支持 `ORDER BY var.prop`**（用 RETURN alias 访问属性）
2. 即使查询失败，`schema_version.py:107-110` 有兜底：返回 `None` → `builder.py:531-532` 捕获异常 → debug 日志 → 不阻塞
3. 最坏情况是 schema 版本检查降级为 warning，不影响功能

**实际影响**: 可能导致 schema 版本查询返回空（降级为 warning），但不会阻塞构建。

---

### Bug #5: index "Key not existed" — ❌ 非bug（已知设计限制）

**代码证据:**
- `nebula_schema.py:198-214`: 已做**两阶段执行**：
  1. 先执行 Tag+Edge DDL
  2. `sleep(10)` 等待生效
  3. 再执行 Index DDL
- `nebula_schema.py:219-223`: 索引失败明确标记为 **non-blocking**（`logger.warning` 不 raise）
- 10s 可能临界（NebulaGraph heartbeat_interval 默认 10s），但这是 **NebulaGraph 异步 DDL 的已知特性**

**为什么不认为是 bug:**
1. 索引是性能优化，不影响功能正确性
2. 代码已正确处理失败（warning + 继续）
3. 可调性：用户可增加 heartbeat_interval 或等待时间

---

### Bug #6: 聚类 save_modules 逐个 merge_node — ✅ 真（性能）

**代码证据:**
- `module_clustering.py:339-374`: `for cluster in clusters: self._neo4j_store.merge_node(...)` 逐个写入
- `nebula_store.py:733`: 已有 `merge_nodes_batch()` 批量方法
- 没有使用 batch 方法

---

### Bug #7: Doc-Code Link 逐条写 — ✅ 真（性能）

**代码证据:**
- `builder.py:604-618`: `for rel in describes_rels: graph_store.merge_relation(...)` 逐条写入
- `nebula_store.py:812`: 已有 `merge_relations_batch()` 批量方法
- 没有使用 batch 方法

---

### Bug #8: _format_value double-escape — ✅ 真 bug（数据正确性）

**代码证据:**
- `nebula_store.py:106-108`:
  ```python
  s = _json.dumps(serializable, ensure_ascii=False)   # \n → \\n (2 chars)
  s = s.replace("\\", "\\\\").replace('"', '\\"')      # \\n → \\\\n (4 chars)
  ```
- **运行验证**: 输入 `["line1\nline2"]` → 存储为 `["line1\\nline2"]`
- json.dumps 先把换行符 `\n`（1 字符）编码为 `\\n`（2 字符：反斜杠+n）
- 然后 `.replace("\\", "\\\\")` 把每个反斜杠翻倍 → `\\\\n`（4 字符：2 个反斜杠+n）
- 读回时是字面 `\n` 文本而非换行符 → **语义损失**

**影响**: list/dict 类型属性中的换行符、制表符等特殊字符会变成字面文本。不阻塞写入，但数据语义受损。

**注意**: 这个 replace 是为了防止 nGQL 解析器把 `"` 当作字符串结束。但 json.dumps 已经处理了引号转义，二次 replace 是多余且有害的。

---

### Bug #9: test_dockerfile_contains_uv 过时 — ❌ 假阳性

**代码证据:**
- `Dockerfile:3`: `COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv` ← **仍然存在**
- `test_docker.py:23`: `assert "ghcr.io/astral-sh/uv" in content`
- **断言能通过**——Dockerfile 没有改过 uv 安装方式

**报告者可能看的是另一个版本的 Dockerfile，或者误报。**

---

### Bug #10: test_all_test_modules_importable 连DB — ✅ 真 bug

**代码证据:**
- `e2e/test_approval_lifecycle.py`: **无 `if __name__ == "__main__"` guard**
- L103: `neo4j = get_neo4j()` 在模块级执行（不是函数内）
- `test_final_validation.py:54-60`: `importlib.import_module(module_name)` 会导入所有 test_*.py
- 导入 `e2e/test_approval_lifecycle.py` → 触发模块级 `get_neo4j()` → 连接图数据库
- 无图数据库 → import 失败 → `test_all_test_modules_importable` 报错

**修复方案**: 给 e2e 模块的执行代码加 `if __name__ == "__main__":` guard，或排除 e2e 目录

---

### Bug #11: TestIntegrationWithRealFile marker错 — ✅ 真（低危）

**代码证据:**
- `test_ontology_loader.py:756`: `@pytest.mark.unit` ← 标了 unit
- 实际内容: 依赖 `/tmp/OntologyAutoGen/OntologyAutoGen/output/ontology.json` 外部文件
- fixture 有 `pytest.skip` 保护（文件不存在则跳过），不会 hard fail
- 但 marker 分类错误：应标 `@pytest.mark.integration`

---

## 汇总

**确认的真 bug（需修复）:**
1. **#1** ModuleEntity 缺 `size` 字段（1 行修复，优先级高）
2. **#2** 聚类全连接图爆炸（加阈值，优先级高）
3. **#3** CLI build 吞 errors（UX 修复，优先级中）
4. **#6** 聚类逐个写入（性能，优先级低）
5. **#7** Doc-Code Link 逐条写（性能，优先级低）
6. **#8** double-escape（数据正确性，优先级中）
7. **#10** e2e 模块导入连 DB（测试隔离，优先级中）
8. **#11** marker 分类错误（低优先级）

**报告中的问题:**
- **#4** 半真——严重度被夸大，有兜底不阻塞
- **#5** 非bug——已知设计限制，代码已正确处理
- **#9** 假阳性——Dockerfile 没改过，断言能通过
