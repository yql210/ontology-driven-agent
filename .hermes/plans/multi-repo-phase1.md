# Phase 1 实施计划：多仓库支持 + Web 构建

## 任务清单（按顺序执行）

### Task 1: Domain 层改造 (schema.py)
1. CodeEntity 加 `repo_id: str = ""` 字段
2. CodeEntity.__post_init__ 的 _stable_id 调用加入 repo_id 作为第一个参数
3. 新增 RepositoryEntity dataclass
4. RELATION_TYPE_TO_NEO4J 加 `belongs_to_repo` → `BELONGS_TO_REPO`
5. VALID_ENTITY_LABELS 加 RepositoryEntity
6. _LABEL_TO_DATACLASS 加 RepositoryEntity
7. _EXTRA_FIELDS 加 RepositoryEntity 的 repoId 字段到所有实体

### Task 2: Store 层改造
1. nebula_schema.py: create_indexes() 加 `idx_{label}_repoId` 索引
2. nebula_schema.py: common_fields 加 `repoId`
3. neo4j_store.py: ensure_constraints 加 repoId 索引（如果有）

### Task 3: Schema version 升级
1. schema_version.py: CURRENT_SCHEMA_VERSION = "2.3.0"
2. 测试文件中硬编码的版本号更新

### Task 4: Config 配置项
1. config.py 加 git_allowed_hosts, git_clone_timeout, git_work_dir

### Task 5: Git Clone Service
1. 新建 src/ontoagent/service/git_clone.py
2. 安全措施：URL 白名单校验、浅克隆、超时、临时目录、SSRF 防护

### Task 6: Pipeline 改造
1. builder.py: build() 加 `repo_id: str = "default"` 参数
2. builder.py: entity_to_dict 注入 repoId
3. builder.py: 写入 RepositoryEntity + BELONGS_TO_REPO

### Task 7: Web API
1. 新建 web/router/build.py: POST /api/build, GET /api/build/status/{task_id}
2. 新建 web/router/repo.py: GET /api/repos, POST /api/repos
3. SSE 进度推送
4. app.py 注册新 router

### Task 8: 测试
1. 新增 test_git_clone.py
2. 新增 test_build_router.py
3. 新增 test_repo_router.py
4. schema 测试更新（repo_id 字段、RepositoryEntity）
5. 全量 pytest 通过
