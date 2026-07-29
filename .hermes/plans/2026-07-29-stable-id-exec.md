# 执行任务：实体 ID 从随机 UUID 改为内容派生稳定哈希

## CC 评审后修订的哈希策略

| Entity | 哈希字段 | CC 审查修正 |
|--------|---------|------------|
| CodeEntity | name + entity_type + file_path + str(start_line) + str(end_line) | 加 end_line 防 Java enum 同行碰撞 |
| ConceptEntity | name + entity_type | 不变 |
| DataAsset | name + data_type + sensitivity | 不变 |
| DocEntity | name + entity_type + file_path | 不变 |
| ResourceEntity | name + entity_type + file_path | 不变 |
| ModuleEntity | name | 不变 |
| ChangeSetEntity | commit_hash | 不变 |
| LogEntity | name + level + message | CC 要求加 message |
| AlertEntity | name + level + message | CC 要求加 message |
| ServiceEntity | name | 不变 |
| ComplianceItem | name + entity_type | 不变 |
| CapabilityEntity | name | 不变 |
| ProcessEntity | name | 不变 |

## 哈希函数

```python
import hashlib

def _stable_id(*parts: str) -> str:
    """从内容字段生成 32 字符 hex 稳定 ID。"""
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
```

## 改造模式

每个 dataclass:
1. `id` 字段默认值从 `field(default_factory=lambda: str(uuid.uuid4()))` 改为 `""`
2. `__post_init__` 中检测：`if not self.id: self.id = _stable_id(...)`
3. 已有的 `__post_init__` 校验逻辑（如 name 非空校验）保留

示例（CodeEntity）:
```python
@dataclass
class CodeEntity:
    name: str
    entity_type: str
    id: str = ""
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    # ...其他不变

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _stable_id(
                self.name, self.entity_type, self.file_path,
                str(self.start_line), str(self.end_line)
            )
        if not self.name or not self.name.strip():
            raise SchemaValidationError("CodeEntity.name cannot be empty")
        # ...其他校验不变
```

## 不改的 uuid.uuid4

- `api/cli.py:197` thread_id — 会话随机 ID
- `domain/approval.py:71` — 审批单随机 ID
- `incremental_updater.py:511` changeset_id — 变更集随机 ID
- `schema_version.py` 的 register_schema_version VID — 用固定字符串

## CURRENT_SCHEMA_VERSION

升级到 "2.2.0"。不需要 ALTER TAG（VID 长度变化不影响 Tag schema），迁移只注册版本号。

## 测试修正

1. `tests/unit/test_schema.py` 和 `test_schema_extra.py` 中断言 UUID 格式的 ~4 处改为断言：
   - len(id) == 32
   - 同实体两次构造 id 相同
   - 不同实体 id 不同

2. 显式传 `id="xxx"` 的测试用例不需要改（保留传入 ID）。

## 执行约束
- 只改 schema.py 中的 id 字段和 __post_init__
- 不要改 nebula_store.py / neo4j_store.py / builder.py
- 不要改 conftest.py
- 完成后运行全量 unit test
