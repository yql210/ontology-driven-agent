# Stage 1: Build
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev

# Stage 2: Runtime
FROM python:3.13-slim

# 安装运行时系统依赖：git（用于 GitCloneService clone 远端仓库 + 增量更新的 git diff）
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN groupadd -r ontoagent && useradd -r -g ontoagent -d /app -s /sbin/nologin ontoagent

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src/
COPY pyproject.toml /app/

# 运行时环境变量
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Graceful shutdown: uvicorn 收到 SIGTERM 后等待活跃请求完成的最长时间
ENV UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=10
# 默认日志格式（生产环境设为 json）
ENV ONTOAGENT_LOG_FORMAT=console

# 数据目录：/data/chroma（向量数据库）+ /tmp/ontoagent-repos（Git clone 工作目录）
RUN mkdir -p /data/chroma /tmp/ontoagent-repos && chown -R ontoagent:ontoagent /app /data /tmp/ontoagent-repos

USER ontoagent

# 健康检查（/health 端点返回 200=健康, 503=不健康）
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

# 默认启动 Web API；docker-compose 可通过 command 覆盖：
#   docker run image web --host 0.0.0.0
#   docker run image serve --transport stdio
#   docker run image butler serve
#   docker run image build ./repo
ENTRYPOINT ["ontoagent"]
CMD ["web", "--host", "0.0.0.0", "--port", "8000"]
