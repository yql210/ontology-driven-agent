# OntoAgent 部署指南

本文档涵盖从开发环境到生产环境的完整部署流程。

## 架构概览

```
用户 → Nginx (80/443) → ontoagent-web (8000)
                         ├── NebulaGraph (metad + storaged + graphd)
                         └── ChromaDB (8001)
```

## 1. 环境准备

### 必需软件
- Docker 24+
- Docker Compose v2+
- 2GB+ 内存（NebulaGraph 集群占约 1.5GB）

### 最低配置
- CPU: 2 核
- 内存: 4 GB
- 磁盘: 20 GB（SSD 推荐）

## 2. 快速部署（Docker Compose）

### 2.1 克隆 + 配置

```bash
git clone <repo-url> ontoagent
cd ontoagent
cp .env.example .env
```

### 2.2 编辑 `.env` — 生产必改项

```bash
# ⚠️ 生产环境必须修改以下项：

# 1. API Key 认证（生成强随机 key）
python -c "import secrets; print(secrets.token_urlsafe(32))"
ONTOAGENT_API_KEY=<上面生成的key>

# 2. NebulaGraph 密码（不要用默认 nebula）
ONTOAGENT_NEBULA_PASSWORD=<强密码>

# 3. CORS 精确域名（不要用 *）
CORS_ORIGINS=https://your-domain.com

# 4. Agent LLM API Key
ONTOAGENT_AGENT_API_KEY=<your-llm-key>

# 5. 日志格式（生产用 JSON 适配 ELK/Loki）
ONTOAGENT_LOG_FORMAT=json
```

### 2.3 启动服务

```bash
# 构建并启动核心服务（NebulaGraph + ChromaDB + Web API）
docker compose up -d

# 启动前端（可选）
docker compose --profile full up -d

# 启动 Butler 常驻服务（可选）
docker compose --profile butler up -d
```

### 2.4 验证

```bash
# 健康检查
curl http://localhost:8000/health
# 期望: {"connected": true, "space": "ontoagent", ...}

# Metrics
curl http://localhost:8000/metrics
# 期望: Prometheus 格式指标

# 前端（如启动）
curl http://localhost:3000
```

## 3. 生产环境加固

### 3.1 TLS/HTTPS

**方案 A: Nginx 终止 TLS（推荐）**

```bash
# 1. 获取证书（Let's Encrypt）
certbot certonly --standalone -d your-domain.com

# 2. 修改 deploy/nginx.conf，取消 HTTPS 配置段的注释
# 3. 挂载证书目录到 frontend 容器
```

**方案 B: 负载均衡终止 TLS**

在云厂商 LB（如阿里云 SLB）上配置 HTTPS 监听，转发 HTTP 到 Nginx 容器。

### 3.2 Nginx 限流

编辑 `deploy/nginx.conf`，取消以下注释：

```nginx
limit_req_zone $binary_remote_addr zone=ontoagent_api:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=ontoagent_chat:10m rate=2r/s;
```

### 3.3 Metrics 访问控制

生产环境 `/metrics` 端点应限制为内网或 Prometheus IP：

```nginx
location /metrics {
    allow 10.0.0.0/8;  # 内网 CIDR
    deny all;
    proxy_pass http://ontoagent-web:8000;
}
```

### 3.4 Docker 镜像安全

- ✅ 非 root 用户运行（Dockerfile 已配置 `ontoagent` 用户）
- ✅ 多阶段构建（builder 中间产物不进入 runtime 镜像）
- ✅ Healthcheck 内置（30s 间隔自动探测）

## 4. 可观测性

### 4.1 日志

```bash
# 开发模式（console 格式，默认）
ONTOAGENT_LOG_FORMAT=console

# 生产模式（JSON 格式，适配 ELK/Loki）
ONTOAGENT_LOG_FORMAT=json
ONTOAGENT_LOG_LEVEL=INFO
```

JSON 日志示例：
```json
{"timestamp": "2026-07-28 11:00:00", "level": "INFO", "logger": "ontoagent.api.web.app", "message": "API Key authentication enabled"}
```

### 4.2 Prometheus Metrics

| 指标 | 类型 | 说明 |
|------|------|------|
| `ontoagent_http_requests_total` | Counter | HTTP 请求总数 (method, endpoint, status) |
| `ontoagent_http_request_duration_seconds` | Histogram | HTTP 请求耗时分布 |
| `ontoagent_graph_operations_total` | Counter | 图数据库操作计数 (operation, status) |
| `ontoagent_graph_operation_duration_seconds` | Histogram | 图数据库操作耗时 |
| `ontoagent_active_sessions` | Gauge | 活跃 Agent 会话数 |
| `ontoagent_llm_calls_total` | Counter | LLM API 调用计数 (provider, status) |
| `ontoagent_app_info` | Gauge | 应用元信息 (version) |

**Prometheus scrape 配置示例：**

```yaml
scrape_configs:
  - job_name: "ontoagent"
    metrics_path: "/metrics"
    static_configs:
      - targets: ["ontoagent-web:8000"]
```

### 4.3 健康检查

```bash
# K8s liveness/readiness probe
GET /health
# 200: 服务正常，DB 连通
# 503: DB 不可达
```

## 5. 数据管理

### 5.1 备份

```bash
# NebulaGraph 数据卷
docker run --rm -v $(docker volume inspect ontology-driven-agent_nebula_storage_data -f '{{.Mountpoint}}'):/data -v $(pwd):/backup alpine tar czf /backup/nebula-backup-$(date +%Y%m%d).tar.gz /data

# ChromaDB 数据卷
docker run --rm -v ontology-driven-agent_chroma_data:/data -v $(pwd):/backup alpine tar czf /backup/chroma-backup-$(date +%Y%m%d).tar.gz /data
```

### 5.2 恢复

```bash
# 停止服务
docker compose down

# 解压备份
docker run --rm -v ontology-driven-agent_nebula_storage_data:/data -v $(pwd):/backup alpine tar xzf /backup/nebula-backup-YYYYMMDD.tar.gz -C /

# 重新启动
docker compose up -d
```

## 6. 服务编排清单

| 服务 | 端口 | 依赖 | 说明 |
|------|------|------|------|
| nebula-metad | 9559, 19559 | - | NebulaGraph 元数据 |
| nebula-storaged | 9779, 19779 | nebula-metad | NebulaGraph 存储 |
| nebula-graphd | 9669, 19669 | metad + storaged | NebulaGraph 查询 |
| chromadb | 8001 | - | 向量数据库 |
| ontoagent-web | 8000 | nebula-graphd, chromadb | Web API |
| ontoagent-frontend | 3000 | ontoagent-web | Nginx 前端（profile: full） |
| ontoagent-butler | - | nebula-graphd | 事件驱动引擎（profile: butler） |

## 7. 故障排查

| 症状 | 排查 |
|------|------|
| `/health` 返回 503 | 检查 NebulaGraph 是否 healthy: `docker compose ps` |
| 启动超时 | NebulaGraph 首次启动需 30-60s，检查 start_period |
| API 401 | 检查 `ONTOAGENT_API_KEY` 是否设置且请求头匹配 |
| API 429 | 触发限流，检查 `ONTOAGENT_RATE_LIMIT` 配置 |
| chat 422 | slowapi 需要 `Request` 参数，确保前端发 POST + JSON body |
| ChromaDB 连接失败 | 检查 `ONTOAGENT_CHROMA_DIR` 是否有写权限 |
