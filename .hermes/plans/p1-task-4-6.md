# Phase 1 — Task 4-6: Git Clone Service + Pipeline + entity_to_dict

## Task 4: Git Clone Service

新建 `src/ontoagent/service/__init__.py`（空文件）和 `src/ontoagent/service/git_clone.py`：

```python
class GitCloneService:
    """安全的 Git clone 服务。
    
    安全措施：
    - URL 域名白名单（防 SSRF）
    - 浅克隆 --depth 1 --single-branch
    - 超时控制
    - 临时目录用 uuid4 命名（防路径穿越）
    - URL 经 urllib.parse 校验（防命令注入）
    """
    ALLOWED_SCHEMES = {"https", "git", "ssh"}
    
    def __init__(self, config: OntoAgentConfig): ...
    
    async def clone(self, repo_url: str, branch: str = "main", token: str | None = None) -> Path:
        """安全 clone Git 仓库到临时目录。"""
        # 1. 校验 URL (scheme + hostname 白名单)
        # 2. 创建临时目录 (uuid4 命名)
        # 3. 构建 git clone 命令 (--depth 1 --single-branch --branch branch)
        # 4. 如果有 token，注入到 URL (https://token@host/...)
        # 5. asyncio.to_thread 执行 subprocess.run, timeout=config.git_clone_timeout
        # 6. 返回 clone 后的目录路径
        
    def _validate_url(self, url: str) -> None:
        """校验 URL scheme 和 hostname。"""
        
    def _get_head_commit(self, repo_path: Path) -> str:
        """获取仓库当前 HEAD commit hash。"""
```

新建测试 `tests/unit/test_git_clone.py`：
- test_validate_url_rejects_unsupported_scheme
- test_validate_url_rejects_host_not_in_whitelist
- test_validate_url_accepts_github
- test_validate_url_accepts_gitee
- test_clone_calls_git_with_correct_args (mock subprocess.run)
- test_clone_uses_depth_1_and_single_branch
- test_clone_creates_temp_dir_under_work_dir

## Task 5: Pipeline 改造 (builder.py + builder_utils.py)

### 5.1 builder_utils.py entity_to_dict 注入 repoId

在 entity_to_dict(entity) 函数中，在返回 dict 里加：
```python
if hasattr(entity, "repo_id") and entity.repo_id:
    d["repo_id"] = entity.repo_id
```
注意：_keys_to_camel_case 会把 repo_id 转成 repoId。

### 5.2 builder.py build() 加 repo_id 参数

在 OntoAgentBuilder.build() 签名加 `repo_id: str = "default"`：

```python
def build(self, repo_path: Path, *, repo_id: str = "default", skip_semantic=False, skip_clustering=False, clear=False) -> BuildResult:
```

在 build() 方法体开头，设置 `self._repo_id = repo_id`。

### 5.3 builder.py _stage_write_structural 注入 repo_id

在 _stage_write_structural 中，构造 CodeEntity 时需要把 repo_id 传进去。
关键是：在 _stage_parse 返回的 entities 上，设置 entity.repo_id = self._repo_id。
最简单的方式：在 build() 中 _stage_parse 之后，遍历 all_entities 设置 repo_id。

在 build() 中 `_stage_parse(repo_path)` 之后加：
```python
# 注入 repo_id 到所有实体（多仓库隔离）
for entity in all_entities:
    entity.repo_id = self._repo_id
for entity in doc_entities:
    entity.repo_id = self._repo_id
```

注意：CodeEntity 已有 repo_id 字段（Task 1 已加）。DocEntity 也需要加 repo_id 字段。
在 DocEntity dataclass 中加 `repo_id: str = ""` 字段（在 name/entity_type 之后）。

### 5.4 builder.py 写入 RepositoryEntity

在 build() 方法中，Stage 2 之后（或 Stage 2 开头），写入 RepositoryEntity + BELONGS_TO_REPO：

```python
from ontoagent.domain.schema import RepositoryEntity

# 写入仓库元数据节点
repo_entity = RepositoryEntity(
    name=repo_id,
    url=str(repo_path) if not repo_path.is_absolute() else "",
    status="building",
)
repo_props = add_provenance(
    {"id": repo_entity.id, "name": repo_entity.name, "url": repo_entity.url, 
     "branch": "main", "status": "building"},
    source="builder", confidence=1.0, extracted_at=batch_time,
)
graph_store.merge_node("RepositoryEntity", repo_props)
```

构建完成后更新状态为 success。可以在 build() 最后加：
```python
graph_store.update_node_property(repo_entity.id, "status", "success")
```

## Task 6: 新增测试

### tests/unit/pipeline/test_builder_repo_id.py

- test_build_accepts_repo_id_param: 验证 build() 接受 repo_id 参数
- test_repo_id_injected_into_entities: 验证 entities 有 repo_id 属性
- test_repository_entity_written: 验证 merge_node 被调用时 label="RepositoryEntity"

## 约束

- 不要改 API 层（web/）、前端（frontend/）
- 保持 from __future__ import annotations
- DocEntity 需要加 repo_id 字段
- ServiceEntity 也需要加 repo_id 字段（已有的 dataclass）
- entity_to_dict 注入的 key 是 "repo_id"（snake_case），store 层 _keys_to_camel_case 会转成 "repoId"
- ruff check + ruff format 必须通过
- 验收: uv run pytest tests/unit/test_git_clone.py tests/unit/test_schema.py tests/unit/test_nebula_schema.py tests/unit/test_migrations.py -v --tb=short
