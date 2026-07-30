# Phase 1 — Task 1-3: Domain + Store + Migration

## 概述

为 OntoAgent 添加多仓库支持。本批任务只改 Domain 层、Store 层（NebulaGraph schema）、Migration 和 Schema Version。不改 Pipeline、API、前端。

## Task 1: Domain 层 (src/ontoagent/domain/schema.py)

### 1.1 CodeEntity 加 repo_id 字段

在 CodeEntity dataclass 中，在 `entity_type` 后、`id` 前加：
```python
repo_id: str = ""
```

在 CodeEntity.__post_init__ 中，把 `_stable_id` 调用改为：
```python
if not self.id:
    self.id = _stable_id(
        self.repo_id,           # 新增：消除跨仓库 VID 碰撞
        self.name,
        self.entity_type,
        self.file_path,
        str(self.start_line),
        str(self.end_line),
    )
```

### 1.2 新增 RepositoryEntity

在 ComplianceItem 之后（V5 Phase 0 注释之前）新增：
```python
@dataclass
class RepositoryEntity:
    """仓库实体：Git 仓库元数据，用于多仓库管理和打标。"""
    name: str
    url: str = ""
    branch: str = "main"
    commit_hash: str = ""
    status: str = "pending"
    id: str = ""
    built_at: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    VALID_STATUSES = {"pending", "building", "success", "failed"}
    def __post_init__(self) -> None:
        if not self.id:
            self.id = _stable_id(self.name, self.url)
        if not self.name or not self.name.strip():
            raise SchemaValidationError("RepositoryEntity.name cannot be empty")
        if self.status not in self.VALID_STATUSES:
            raise SchemaValidationError(...)
```

### 1.3 更新 VALID_ENTITY_LABELS

加 "RepositoryEntity"。

### 1.4 更新 _LABEL_TO_DATACLASS

加 "RepositoryEntity": RepositoryEntity。

### 1.5 更新 _EXTRA_FIELDS

给所有已有的实体条目加 "repoId"。新增 "RepositoryEntity": {"repoId"}。
注意：CodeEntity 已有条目，在它的 set 里加 "repoId"。不要创建重复 key。

### 1.6 RELATION_TYPE_TO_NEO4J 加两个关系

在 dict 末尾（equivalent_to 之后）加：
```python
"belongs_to_repo": "BELONGS_TO_REPO",
"depends_on_repo": "DEPENDS_ON_REPO",
```

### 1.7 RELATION_CONSTRAINTS 加约束

在 equivalent_to 之后加：
```python
"belongs_to_repo": RelationConstraint(
    domain={"CodeEntity", "DocEntity", "ModuleEntity", "ResourceEntity", "ServiceEntity"},
    range="RepositoryEntity",
    description="实体属于某仓库",
),
"depends_on_repo": RelationConstraint(
    domain="RepositoryEntity",
    range="RepositoryEntity",
    description="仓库间依赖关系",
),
```

## Task 2: Store 层 (src/ontoagent/store/nebula_schema.py)

### 2.1 common_fields 加 repoId

在 create_tags() 方法的 common_fields set 中加 `"repoId"`。

### 2.2 create_indexes() 加 repoId 索引

在 create_indexes() 方法中，对每个 label 除了建 name 索引外，额外建：
```python
ddl_repo = f"CREATE TAG INDEX IF NOT EXISTS `idx_{label}_repoId` ON `{label}`(`repoId`(64));"
ddl_list.append(ddl_repo)
```

## Task 3: Migration + Schema Version

### 3.1 CURRENT_SCHEMA_VERSION 升级

src/ontoagent/store/schema_version.py: 改为 "2.3.0"

### 3.2 新建迁移文件

src/ontoagent/store/migrations/v2_3_0_multi_repo.py:
- 类 MultiRepoMigration(MigrationBase)
- version_from = "2.2.0", version_to = "2.3.0"
- upgrade: 如果是 NebulaGraph，对所有 VALID_ENTITY_LABELS 做 ALTER TAG ADD repoId string（用 contextlib.suppress 保证幂等）
- downgrade: 如果是 NebulaGraph，ALTER TAG DROP repoId

### 3.3 注册迁移

src/ontoagent/store/migrations/registry.py:
- _BUILTIN_MIGRATIONS 加 "2.3.0"
- _load_migration 加 if version == "2.3.0" 分支

### 3.4 Config 加 Git 配置

src/ontoagent/config.py:
- OntoAgentConfig 加三个字段: git_allowed_hosts (list, default github.com/gitee.com/gitlab.com), git_clone_timeout (int, 300), git_work_dir (str, /tmp/ontoagent-repos)
- from_env() 读 ONTOAGENT_GIT_ALLOWED_HOSTS, ONTOAGENT_GIT_CLONE_TIMEOUT, ONTOAGENT_GIT_WORK_DIR

### 3.5 测试更新

tests/unit/test_migrations.py:
- test_current_schema_version: 断言改为 "2.3.0"
- test_get_latest_version: 断言改为 "2.3.0"
- test_get_migration_path_full: 路径长度从 6 改为 7

## 约束

- 不要改 Pipeline (builder.py)、API (web/)、前端 (frontend/)
- 不要改 builder_utils.py
- 保持 from __future__ import annotations
- 所有新方法必须有类型注解 + docstring
- ruff check 和 ruff format 必须通过
- 验收命令: uv run ruff check src/ontoagent/domain/schema.py src/ontoagent/store/nebula_schema.py src/ontoagent/store/schema_version.py src/ontoagent/store/migrations/ src/ontoagent/config.py && uv run ruff format src/ontoagent/domain/schema.py src/ontoagent/store/nebula_schema.py src/ontoagent/store/schema_version.py src/ontoagent/store/migrations/ src/ontoagent/config.py
- 测试验收: uv run pytest tests/unit/test_schema.py tests/unit/test_migrations.py tests/unit/test_nebula_schema.py -v --tb=short
