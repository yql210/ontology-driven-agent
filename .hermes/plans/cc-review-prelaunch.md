# 上线准备代码审查请求

请审查以下改动，这是 OntoAgent v0.2.0 上线准备的工程化完善。重点关注：安全缺陷、架构合理性、潜在 bug、遗漏项。

## 改动范围（5 个 commit, c7c3b3f → aef28eb）

### 1. 安全加固
- `src/ontoagent/api/web/rate_limit.py` — slowapi 限流单例
- `src/ontoagent/api/web/app.py` — 限流注册 + CORS 收窄 + 请求计时中间件 + /metrics 端点
- `src/ontoagent/api/web/router/chat.py` — chat 端点 @limiter.limit("10/minute")

### 2. 可观测性
- `src/ontoagent/observability/__init__.py` — Prometheus metrics (7指标) + JSON 结构化日志

### 3. CI/CD
- `.github/workflows/ci.yml` — lint + unit test (push/PR) + integration test (手动/tag)

### 4. 部署加固
- `Dockerfile` — non-root user + HEALTHCHECK + graceful shutdown env
- `docker-compose.yml` — healthcheck + stop_grace_period + env 传递
- `deploy/nginx.conf` — 限流 zone + HTTPS 模板 + metrics 访问控制

### 5. 配置
- `.env.example` — 新增 RATE_LIMIT / CHAT_RATE_LIMIT / LOG_FORMAT / CORS_ALLOW_CREDENTIALS
- `pyproject.toml` — per-file-ignores + slowapi/prometheus-client 依赖

## 审查重点

1. **安全**: 限流能否被绕过？API Key 中间件和限流中间件的执行顺序是否正确？CORS 配置是否有漏洞？
2. **架构**: observability 模块的 setup_logging() 在模块导入时执行，会不会影响 CLI 和测试？
3. **可靠性**: slowapi 的 @limiter.limit 装饰器要求 request: Request 参数，和 FastAPI 的依赖注入是否有兼容风险？
4. **运维**: Docker HEALTHCHECK 用 python -c urllib 而非 curl，镜像里有没有 curl？non-root 用户能否访问 /data？
5. **遗漏**: 上线前还有什么没做的？

请运行 `git diff c7c3b3f..HEAD --stat` 查看完整改动范围，逐文件审查。给出评分（0-100）和具体改进建议。
