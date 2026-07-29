# 第二轮 Bug 报告审查 — 真实部署反馈

**审查人**: Hermes Agent（代码实证 + 运行时验证）
**时间**: 2026-07-29
**基线**: d68ae67

---

## 审查结论总览

| # | 问题 | 报告者判定 | Hermes 判定 | 置信度 |
|---|------|-----------|------------|--------|
| 1 | _format_value str 分支换行未转义 | 真 bug | ✅ **真 bug（P0 必现）** | 100% |
| 2 | ensure_space 共享集群权限 | 真 bug | ✅ **真 bug（P1）** | 100% |
| 3 | VID 36 vs FIXED_STRING(32) | 适配问题 | ⚠️ **非 upstream bug（设计自洽）** | 100% |
| 4 | index 建立时序 Key not existed | 真 bug(轻度) | ⚠️ **已知限制（非代码 bug）** | 90% |
| 5 | test_docker 断言 ghcr | 本地 JDOS 问题 | ✅ **非 upstream bug** | 100% |

---

## 问题 1: _format_value str 分支换行未转义 — ✅ 真 bug（P0）

**代码位置**: `nebula_store.py:110-112`（str 分支）

**与上次 Bug #8 的关键区别**:
- Bug #8（已关闭）: list/dict 分支的 double-escape → round-trip 正确，非 bug
- **本条**: str 分支的**字面换行符未转义** → nGQL SyntaxError

**代码证据**:
```python
# str 分支 (L110-112):
s = str(value)                                     # 保留原始 \n（chr(10) 字面换行）
s = s.replace("\\", "\\\\").replace('"', '\\"')   # 只转义 \\ 和 \"，不转义 \n/\r/\t
return f'"{s}"'
```

**运行验证**:
- 输入: `"根据ID查询用户。\n@param id 用户ID"`（含 chr(10) 换行）
- 输出: `"根据ID查询用户。\n@param id 用户ID"`（**包含字面换行符 chr(10)**）
- nGQL parser 遇到 INSERT VERTEX 中的字面换行 → SyntaxError

**对比 list 分支**: json.dumps 已将 `\n` 编码为 `\\n`（2字符），再 replace 翻倍 → 无字面换行。所以 list 分支无此问题。

**影响**: NebulaGraph 后端 + 任何含多行 docstring 的字符串字段 → merge_nodes_batch 整批失败 → RuntimeError → Stage 2 中止 → 构建 aborted。

**触发频率**: 极高。Java/Python 源代码的 docstring 普遍含多行文本。

**修复方案**: str 分支补转义 `\n`/`\r`/`\t`（反斜杠转义必须最先）:
```python
s = str(value)
s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
return f'"{s}"'
```

**为何 upstream 单测没发现**: 单测全程 mock session，不实际执行 Nebula INSERT VERTEX。

---

## 问题 2: ensure_space 共享集群权限 — ✅ 真 bug（P1）

**代码位置**: `nebula_schema.py:84-102`

**代码证据**:
```python
ddl = f"CREATE SPACE IF NOT EXISTS `{self._space_name}` ..."
result = self._session.execute(ddl)
if not result.is_succeeded():
    return False  # ← 无权限直接失败
```

无任何 "先 SHOW SPACES 检测 space 是否已存在" 的逻辑。

**影响**: 共享 Nebula 集群 + 无 CREATE SPACE 权限的用户 → PermissionError → ensure_space 返回 False → initialize() 中止 → tag/edge/index 全没建 → 后续所有操作报 "Tag not existed"。

**修复方案**: ensure_space 先 SHOW SPACES 检测目标 space 是否已存在，已存在则跳过 CREATE 返回 True:
```python
def ensure_space(self, vid_type="FIXED_STRING(36)") -> bool:
    # 先检测 space 是否已存在
    result = self._session.execute("SHOW SPACES;")
    if result.is_succeeded():
        spaces = [row.values[0].get_sVal().value for row in result.rows]
        if self._space_name in spaces:
            logger.info("[NebulaSchema] space '%s' already exists", self._space_name)
            return True
    # 不存在才 CREATE
    ddl = f"CREATE SPACE IF NOT EXISTS ..."
    ...
```

---

## 问题 3: VID 36 vs FIXED_STRING(32) — ⚠️ 非 upstream bug

upstream 设计自洽: UUID v4 = 36 字符 + FIXED_STRING(36) space = 完全匹配。

如果要适配外部 FIXED_STRING(32) space，可以:
- 方案 A: `id = uuid.uuid4().hex`（32 字符无横线，无损可逆）
- 方案 B: `vid_type` 可配置（环境变量）

**这是灵活性增强，不是 bug 修复。** 报告者同意。

---

## 问题 4: index 建立时序 Key not existed — ⚠️ 已知限制

**与上次审查结论一致**: NebulaGraph DDL 异步生效 + heartbeat_interval 10s 的已知特性。代码已做两阶段执行 + sleep(10) + non-blocking warning。

可改进（增加 retry 或 sleep 时间），但不是代码 bug。

---

## 问题 5: test_docker 断言 — ❌ 非 upstream bug

upstream Dockerfile 用 `ghcr.io/astral-sh/uv`，测试断言也查 `ghcr.io/astral-sh/uv`，完全自洽。报告者本地 JDOS 改了 Dockerfile 但没改测试。
