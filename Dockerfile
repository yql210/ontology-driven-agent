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
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src/
COPY pyproject.toml /app/
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
# 默认启动 Web API；docker-compose 可通过 command 覆盖
# docker run image web --host 0.0.0.0
# docker run image serve --transport stdio
# docker run image butler serve
# docker run image build ./repo
ENTRYPOINT ["ontoagent"]
CMD ["web", "--host", "0.0.0.0", "--port", "8000"]
