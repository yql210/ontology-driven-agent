# 方案：实体 ID 从随机 UUID 改为内容派生稳定哈希

## 问题

`schema.py` 中 14 个实体 dataclass 的 `id` 字段用 `uuid.uuid4()` 随机生成。同一实体每次构建得到不同 ID → NebulaGraph 的 INSERT VERTEX 按 VID 覆盖语义失效 → 重建累积重复节点（实测 k2 仓库构建 4 次后 CodeEntity 从 44828 膨胀到 181717）。

## 方案：内容派生稳定哈希

### 核心函数

在 `schema.py` 顶部新增：

```python
import hashlib

def _stable_id(*parts: str) -> str:
    """从内容字段生成 32 字符 hex 稳定 ID。

    同一实体（相同字段值）始终得到相同 ID。
    用于 NebulaGraph INSERT VERTEX 的覆盖语义，确保重建幂等。

    Args:
        *parts: 用于区分实体的字段值（None → 空字符串）。

    Returns:
        32 字符 hex 字符串（sha256 截断，匹配 FIXED_STRING(32)）。
    """
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
```

### 各实体哈希策略

| Entity | 哈希字段 | 理由 |
|--------|---------|------|
| CodeEntity | name + entity_type + file_path + start_line | 同文件同名不同行号=重载方法 |
| ConceptEntity | name + entity_type | 语义概念没有 file_path |
| DataAsset | name + data_type + sensitivity | 业务数据资产 |
| DocEntity | name + entity_type + file_path | 文档按路径区分 |
| ResourceEntity | name + entity_type + file_path | 资源按路径区分 |
| ModuleEntity | name | 聚类名称已唯一 |
| ChangeSetEntity | commit_hash | commit hash 本身唯一 |
| LogEntity | name + level | 日志实体 |
| AlertEntity | name + level | 告警实体 |
| ServiceEntity | name | 微服务名唯一 |
| ComplianceItem | name + entity_type | 合规项 |
| CapabilityEntity | name | 能力名唯一 |
| ProcessEntity | name | 流程名唯一 |

### 实现方式

**不用 `default_factory`**（因为需要访问其他字段），改用 `__post_init__` 计算：

```python
@dataclass
class CodeEntity:
    name: str
    entity_type: str
    id: str = ""  # 空=待 __post_init__ 计算
    file_path: str | None = None
    start_line: int | None = None
    # ...其他字段不变

    def __post_init__(self) -> None:
        if not self.id:  # 只在未显式传 id 时计算（兼容外部传入固定 ID）
            self.id = _stable_id(self.name, self.entity_type, self.file_path, str(self.start_line))
        # 已有的校验逻辑不变
```

**关键设计点**：
1. `id` 默认值改为 `""`（空字符串），在 `__post_init__` 中检测：空则计算，非空则保留（兼容外部传入固定 ID 的场景）
2. 不用 `default_factory` 因为它无法访问 `self.name` 等字段
3. 哈希输出 32 hex chars，**同时匹配 FIXED_STRING(32) 和 FIXED_STRING(36)** — 一举解决 VID 长度适配问题

### 不改的部分

- `api/cli.py:197` thread_id — 运行时会话 ID，需随机
- `domain/approval.py:71` — 审批单 ID，需随机
- `incremental_updater.py:511` changeset_id — 变更集 ID，需随机
- `Relation` 类 — 关系靠 source_id/target_id/rel_type 定位，不靠 id

## 影响面

### 需要改的
1. `schema.py` — 14 个 dataclass 的 id 字段 + __post_init__
2. `schema_version.py` — CURRENT_SCHEMA_VERSION 升级到 2.2.0
3. 新增 v2.2.0 schema 迁移（对 NebulaGraph 不需要 ALTER TAG，因为 VID 变化不影响 Tag schema）
4. 测试中断言 UUID 格式的用例需调整

### 不需要改的
- `nebula_store.py` merge_node/merge_nodes_batch — INSERT VERTEX 按 VID 覆盖，ID 稳定后自然幂等
- `neo4j_store.py` — MERGE 按 id 属性匹配，ID 稳定后自然幂等
- `incremental_updater.py` 的 git diff 逻辑 — 不依赖 ID 随机性
- `builder.py` — 构建 pipeline 不变
- `chroma_store.py` — ChromaDB 用 id 做主键，稳定 ID 后重建自然覆盖

## 风险

1. **哈希碰撞**: sha256 截 32 hex = 128 位，碰撞概率 ~2^-128，可忽略
2. **历史数据**: 已写入的随机 ID 节点不会消失，需配套清理脚本（已有 scripts/cleanup_ontoagent_nebula.py）
3. **start_line 为 None 的实体**: 函数/方法的 start_line 通常有值，但 file-level 实体可能为 None。hash 中 str(None) = "None"，仍可区分
4. **ID 长度变化**: 从 36 字符 → 32 字符。FIXED_STRING(36) 能容纳 32 字符。但 FIXED_STRING(32) 空间正好（无余量）
5. **SchemaVersion tag 的 SchemaVersion 节点**: 由 `register_schema_version` 用 `schema_version_{version}` 做 VID，不走 entity dataclass，不受影响
