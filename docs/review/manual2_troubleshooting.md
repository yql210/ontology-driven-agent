---
title: LayerKG 运维排查手册
date: 2026-05-17T16:39:48+08:00
lastmod: 2026-05-17T16:39:48+08:00
---

# LayerKG 运维排查手册

# LayerKG 运维排查手册

## 1. 诊断命令速查

### 1.1 系统级检查

```bash
# 检查 LayerKG 版本和配置
uv run layerkg version
uv run layerkg info

# 全量测试
uv run pytest tests/ -v --tb=short

# 代码质量检查
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

### 1.2 外部服务检查

```bash
# Neo4j 状态（HTTP API）
curl -s http://localhost:7474 | head -5

# Neo4j Bolt 连接
python -c "
from neo4j import GraphDatabase
d = GraphDatabase.driver('bolt://localhost:7687', auth=('neo4j', 'your_password'))
with d.session() as s:
    print(s.run('RETURN 1 AS test').single())
d.close()
"

# Ollama 状态和模型列表
curl -s http://localhost:11434/api/tags | python -m json.tool

# 测试嵌入
curl -s http://localhost:11434/api/embed -d '{"model":"qwen2.5-coder:0.5b","input":["test"]}'
```

### 1.3 数据库状态检查

```bash
# Neo4j 节点统计（通过 CLI）
uv run python -c "
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
cfg = LayerKGConfig.from_env()
with Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password) as store:
    for label in ['CodeEntity','ConceptEntity','DocEntity','ModuleEntity','ChangeSetEntity']:
        r = store.query(f'MATCH (n:{label}) RETURN count(n) AS cnt')
        print(f'{label}: {r[0][\"cnt\"]}')
    r = store.query('MATCH ()-[r]->() RETURN count(r) AS cnt')
    print(f'Relations: {r[0][\"cnt\"]}')
"

# ChromaDB 统计
uv run python -c "
from layerkg.config import LayerKGConfig
from layerkg.chroma_store import ChromaStore
cfg = LayerKGConfig.from_env()
with ChromaStore(persist_dir=cfg.chroma_persist_dir, ollama_url=cfg.ollama_base_url, embedding_model=cfg.embedding_model) as cs:
    print(f'Total entities: {cs.count()}')
    for t in ['function','class','module','file','concept','readme']:
        print(f'  {t}: {cs.count(where={\"entity_type\": t})}')
"
```

## 2. 常见错误排查

### 2.1 Neo4j 相关

#### 错误: `neo4j.exceptions.ServiceUnavailable: Connection refused`

**原因**: Neo4j 未启动或地址配置错误。

**排查步骤**:

1. 检查 Neo4j 进程: `ps aux | grep neo4j`
2. 检查端口监听: `ss -tlnp | grep 7687`
3. 检查 `.env`​ 中 `LAYERKG_NEO4J_URI` 是否正确
4. 测试连通性: `curl http://localhost:7474`
5. 如使用远程服务器，检查防火墙和网络

**解决方案**:

```bash
# 启动 Neo4j
neo4j console
# 或 Docker
docker run -d -p 7474:7474 -p 7687:7687 neo4j:5
```

#### 错误: `neo4j.exceptions.AuthError: Unsupported authentication token`

**原因**: 用户名或密码错误。

**排查**:

1. 确认 `.env`​ 中 `LAYERKG_NEO4J_PASSWORD` 与 Neo4j 设置一致
2. 首次使用需设置密码: 访问 `http://localhost:7474` 初始化

#### 错误: `dictionary update sequence element #0 has length 1`

**原因**: `CodeEntity.parameters` 属性名与 Neo4j driver 内部参数冲突。

**解决方案**: 此问题已在代码中修复（属性名映射为 `code_parameters`）。如遇到，请确认使用最新代码。

#### Neo4j 约束初始化

```bash
# 手动创建约束（首次使用时）
uv run python -c "
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
cfg = LayerKGConfig.from_env()
with Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password) as store:
    store.ensure_constraints()
    print('Constraints created')
"
```

### 2.2 Ollama 相关

#### 错误: `httpx.ConnectError: Connection refused` (Ollama)

**原因**: Ollama 未启动。

**解决方案**:

```bash
# 启动 Ollama
ollama serve

# 拉取所需模型
ollama pull qwen2.5-coder:0.5b   # 嵌入模型
ollama pull qwen3.5:9b            # 语义提取 LLM
```

#### 错误: `EmbeddingError: Model 'xxx' not found`

**原因**: 模型未拉取。

**解决方案**:

```bash
ollama pull <model_name>
ollama list  # 查看已安装模型
```

#### 跳过 Ollama（离线模式）

构建时加 `--skip-semantic` 跳过 Stage 3（语义提取）:

```bash
uv run layerkg build . --skip-semantic
```

### 2.3 ChromaDB 相关

#### 错误: `chromadb.errors.InvalidCollection`

**原因**: ChromaDB 版本升级后数据格式不兼容。

**解决方案**:

```bash
# 删除旧数据重建
rm -rf .chroma/
uv run layerkg build . --clear
```

#### ChromaDB 数据损坏

```bash
# 备份后清理
cp -r .chroma/ .chroma.bak/
rm -rf .chroma/
uv run layerkg build .
```

### 2.4 构建流水线相关

#### 构建卡住不动

**可能原因**:

1. Ollama 嵌入请求超时（大文件 embedding 慢）
2. Neo4j 写入阻塞

**排查**:

```bash
# 加 -v 查看详细日志
uv run layerkg build . -v --verbose-build

# 跳过语义提取快速构建
uv run layerkg build . --skip-semantic --skip-clustering
```

#### 构建结果实体数为 0

**原因**: 目标路径没有支持的文件，或所有文件在跳过目录中。

**排查**:

1. 确认路径正确: `uv run layerkg build /absolute/path/to/repo`
2. 检查文件类型: LayerKG 支持 `.py`​、`.java`​、`.md`​、`.rst`
3. 检查 `LAYERKG_BUILD_SKIP_DIRS` 配置

#### `ImportError: tree_sitter_python`​ 或 `tree_sitter_java`

**原因**: tree-sitter 语言库未安装。

**解决方案**:

```bash
uv sync  # 重新同步依赖
uv add tree-sitter-python tree-sitter-java
```

### 2.5 Agent 对话相关

#### 错误: `401 Unauthorized`​ 或 `API key invalid`

**原因**: Agent LLM API 密钥无效。

**排查**:

1. 检查 `.env`​ 中 `LAYERKG_AGENT_API_KEY`
2. 确认 `LAYERKG_AGENT_BASE_URL` 正确
3. 验证 API 余额/配额

#### Agent 回答质量差

**可能原因**:

1. 知识图谱为空（未构建）
2. LLM 模型能力不足
3. 没有相关实体

**排查**:

```bash
# 检查图谱是否有数据
uv run layerkg info

# 测试搜索
uv run layerkg query "测试查询"

# 使用更好的模型
# 修改 LAYERKG_AGENT_LLM_MODEL 为更强的模型
```

#### DeepSeek reasoning_content 兼容问题

**注意**: DeepSeek 的 deepseek-v4-pro 模型有 `reasoning_content`​ 兼容问题（LangChain ChatOpenAI 不保留 reasoning_content，多轮报 400 错误）。推荐使用 `deepseek-chat`（映射到 deepseek-v4-flash）。

### 2.6 Web 服务相关

#### CORS 错误

**原因**: 前端地址不在 CORS 白名单。

**解决方案**:

```bash
# 设置 CORS 允许的源（逗号分隔）
export CORS_ORIGINS="http://localhost:5173,http://localhost:3000"
uv run layerkg web
```

#### 前端构建失败

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run build
```

### 2.7 Butler 相关

#### Butler 启动后不触发更新

**排查**:

1. 确认 Git 仓库有提交: `git log --oneline -1`
2. 确认 `poll_interval` 设置合理（默认 30 秒）
3. 加 `--log-level DEBUG` 查看详细日志:

   ```bash
   uv run layerkg butler serve --repo . --log-level DEBUG
   ```

#### Butler handler 执行失败

**排查**:

```bash
# 查看 Butler 状态
uv run layerkg butler status

# 手动触发测试
uv run layerkg butler update --repo . --since HEAD~1
```

## 3. 日志分析

### 3.1 启用详细日志

```bash
# CLI 详细模式
uv run layerkg build . -v

# Butler DEBUG 模式
uv run layerkg butler serve --log-level DEBUG

# Python 日志
export LOGLEVEL=DEBUG
uv run layerkg build .
```

### 3.2 关键日志含义

|日志关键词|含义|建议|
| ------------| ------------------| ----------------------|
|​`Stage 1: Parse`|开始 AST 解析|正常|
|​`Stage 2: Write`|写入 Neo4j|如卡住检查 Neo4j|
|​`Stage 3: Semantic`|语义提取|如跳过检查 Ollama|
|​`Ollama unavailable`|Ollama 连接失败|可忽略，Stage 3 降级|
|​`Error in batch embedding`|嵌入失败|检查 Ollama 模型|
|​`ConsistencyGuard audit`|Butler 审计日志|正常操作记录|
|​`handler.completed`|Handler 执行成功|正常|
|​`handler.failed`|Handler 执行失败|检查具体错误信息|

## 4. 数据维护

### 4.1 完全重置

```bash
# 1. 清空 Neo4j
uv run python -c "
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
cfg = LayerKGConfig.from_env()
with Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password) as store:
    print(f'Deleted {store.clear_all()} nodes')
"

# 2. 清空 ChromaDB
rm -rf .chroma/

# 3. 清空 Butler 审计日志
rm -f .traces.db .butler_audit.db .skills.db .sha256_cache.json

# 4. 重建
uv run layerkg build .
```

### 4.2 数据一致性检查

```bash
# 检查孤立节点（无关系的 CodeEntity）
uv run python -c "
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
cfg = LayerKGConfig.from_env()
with Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password) as store:
    orphans = store.cleanup_orphan_nodes()
    print(f'Cleaned {orphans} orphan nodes')
"
```

### 4.3 备份

```bash
# Neo4j 备份
neo4j-admin database dump neo4j --to=/backup/neo4j-$(date +%Y%m%d).dump

# ChromaDB 备份
tar czf /backup/chroma-$(date +%Y%m%d).tar.gz .chroma/
```

## 5. 性能优化

### 5.1 构建加速

|优化项|命令|效果|
| --------------| ------| -------------------------------|
|跳过语义提取|​`--skip-semantic`|省去 LLM 调用，速度提升 5-10x|
|跳过模块聚类|​`--skip-clustering`|省去社区检测计算|
|两者都跳过|​`--skip-semantic --skip-clustering`|最快，只有结构化数据|

### 5.2 Ollama 性能

```bash
# 使用 GPU 加速
OLLAMA_GPU=1 ollama serve

# 使用更小的嵌入模型
# qwen2.5-coder:0.5b 约 390MB，推荐
# 如需更快可用更小模型，但精度下降
```

### 5.3 Neo4j 调优

```cypher
-- 增加内存配置（neo4j.conf）
dbms.memory.heap.max_size=2G
dbms.memory.pagecache.size=1G

-- 确认索引存在
SHOW CONSTRAINTS
```
