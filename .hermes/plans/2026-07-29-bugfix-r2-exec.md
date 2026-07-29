# 执行任务：第二轮 Bug 修复（3 个任务）

## 任务 1: P0 — _format_value str 分支补 \n/\r/\t 转义

**文件**: `src/ontoagent/store/nebula_store.py`（L110-112 str 分支）

**问题**: str 分支只转义 \\ 和 \"，未转义字面换行符 chr(10)/chr(13)/chr(9)。nGQL 不允许字符串字面量内含字面换行。

**当前代码**:
```python
    s = str(value)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'
```

**修改后**（注意转义顺序：反斜杠必须最先）:
```python
    s = str(value)
    s = (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{s}"'
```

**文件**: `tests/unit/test_nebula_store.py`

**新增测试**（在 TestFormatValueSerialization 类中）:
1. `test_string_with_newline_escaped`: 验证含换行符的字符串输出不含字面 chr(10)
2. `test_format_value_str_round_trip`: 验证含多行文本的 str round-trip（类似已有的 list round-trip 测试）

---

## 任务 2: P1 — ensure_space 先 SHOW SPACES 检测

**文件**: `src/ontoagent/store/nebula_schema.py`（L84-102 ensure_space 方法）

**问题**: 直接 CREATE SPACE IF NOT EXISTS，共享集群无权限用户即使 space 已存在也会 PermissionError。

**修改方案**: 在 CREATE 之前先 SHOW SPACES 检测 space 是否已存在。已存在直接返回 True。

```python
def ensure_space(self, vid_type: str = "FIXED_STRING(36)") -> bool:
    """创建或确认 Space 存在。

    共享集群友好：先 SHOW SPACES 检测是否已存在，已存在则跳过 CREATE。
    """
    # 先检测 space 是否已存在（SHOW SPACES 只需普通用户权限）
    check_result = self._session.execute("SHOW SPACES;")
    if check_result.is_succeeded():
        existing_spaces = set()
        for row in check_result.rows:
            if row.values and row.values[0].get_sVal().value:
                existing_spaces.add(row.values[0].get_sVal().value)
        if self._space_name in existing_spaces:
            logger.info("[NebulaSchema] space '%s' already exists, skipping CREATE", self._space_name)
            return True

    # space 不存在，执行 CREATE
    ddl = (
        f"CREATE SPACE IF NOT EXISTS `{self._space_name}` "
        f"(vid_type={vid_type}, partition_num=10, replica_factor=1);"
    )
    result = self._session.execute(ddl)
    if not result.is_succeeded():
        logger.error("[NebulaSchema] create space failed: %s", _safe_error_msg(result))
        return False
    logger.info("[NebulaSchema] space '%s' ensured (vid_type=%s)", self._space_name, vid_type)
    return True
```

**测试**: 在已有的 nebula_schema 测试中添加（如果有 mock session 的话）:
- test_ensure_space_skips_create_if_exists: mock SHOW SPACES 返回包含目标 space → 验证不执行 CREATE
- test_ensure_space_creates_if_not_exists: mock SHOW SPACES 返回空 → 验证执行 CREATE

**注意**: SHOW SPACES 的返回值解析需要参考 nebula3 python SDK 的 ResultSet 结构。如果不确定结构，用 try/except 包裹解析逻辑，失败则降级到直接 CREATE。

---

## 任务 3: 增强 — vid_type 可配置

**文件**: `src/ontoagent/config.py`（或 OntoAgentConfig 所在文件）

添加配置项 `nebula_vid_type`，默认 `"FIXED_STRING(36)"`，通过环境变量 `ONTOAGENT_NEBULA_VID_TYPE` 控制。

**文件**: `src/ontoagent/store/nebula_schema.py`

`NebulaSchemaInitializer.__init__` 接收 vid_type 参数，ensure_space 和 initialize 使用传入的 vid_type。

**注意**: 这个是增强功能，保持默认行为不变。只在有环境变量时生效。

---

执行约束:
- 任务 1 是 P0，最高优先
- 任务 2 是 P1
- 任务 3 是增强，最后做
- 不要碰方案外的文件
- 每个任务后运行测试
