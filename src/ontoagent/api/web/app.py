import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from ontoagent.agent.trace import TraceCollector
from ontoagent.api.web.rate_limit import limiter
from ontoagent.api.web.router import chat as chat_router
from ontoagent.api.web.router.graph import router as graph_router
from ontoagent.api.web.router.service_graph_eval import router as service_graph_eval_router
from ontoagent.auth import RepoAccessControl, RepoAuthMiddleware
from ontoagent.config import OntoAgentConfig
from ontoagent.observability import (
    record_http_request,
    render_metrics,
    reset_request_id,
    set_request_id,
    setup_logging,
)
from ontoagent.store.factory import create_graph_store

logger = logging.getLogger(__name__)

# TraceCollector 单例
_trace_collector = TraceCollector()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = OntoAgentConfig.from_env()
    store = create_graph_store(config)
    app.state.graph_store = store
    # 后台构建任务状态：task_id -> BuildStatusResponse（内存级，重启丢失）
    app.state.build_tasks = {}
    app.state.build_asyncio_tasks = {}
    # ACL：SQLite 持久化（默认文件 ontoagent_acl.db，可通过 env 覆盖）
    acl_db_path = os.getenv("ONTOAGENT_ACL_DB", "ontoagent_acl.db")
    app.state.acl = RepoAccessControl(acl_db_path)
    app.state.acl_enabled = os.getenv("ONTOAGENT_ACL_ENABLED", "").lower() == "true"
    yield
    # ---- shutdown: 逐个清理资源（独立 try/except，一个失败不阻塞其余）----
    # 未来若 app.state 增加 httpx / embed client，在此追加独立 try/except
    try:
        store.close()  # Neo4j driver / NebulaGraph 连接池
    except Exception:
        logger.warning("graph store close failed", exc_info=True)
    try:
        app.state.acl.close()  # SQLite
    except Exception:
        logger.warning("acl close failed", exc_info=True)


def create_app() -> FastAPI:
    # 只在 Web API 启动时配置日志（不影响 CLI 和测试）
    setup_logging()

    app = FastAPI(title="OntoAgent Agent", version="0.2.0", lifespan=lifespan)

    # ===== Rate Limiter 注册 =====
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ===== CORS（生产环境通过 env 配置精确域名，禁止通配符） =====
    cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    cors_origins = [origin.strip() for origin in cors_origins_str.split(",") if origin.strip()]
    allow_credentials = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "DELETE", "HEAD", "OPTIONS"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
    )

    # ===== API Key 认证（设置 ONTOAGENT_API_KEY 后生效） =====
    api_key = os.getenv("ONTOAGENT_API_KEY", "")
    if api_key:
        from starlette.middleware.base import BaseHTTPMiddleware

        class APIKeyMiddleware(BaseHTTPMiddleware):
            """验证 Authorization: Bearer *** 或 X-API-Key: ***

            /health 和 /metrics 端点免认证。
            使用 hmac.compare_digest 防止 timing attack。
            """

            _exempt_paths = {"/health", "/metrics"}

            async def dispatch(self, request, call_next):
                # /health 和 /metrics 免认证
                if request.url.path in self._exempt_paths:
                    return await call_next(request)
                # 检查 Bearer token 或 X-API-Key header
                auth = request.headers.get("Authorization", "")
                xkey = request.headers.get("X-API-Key", "")
                token = ""
                if auth.startswith("Bearer "):
                    token = auth[7:]
                elif xkey:
                    token = xkey
                # 常量时间比较，防止 timing attack
                if not hmac.compare_digest(token, api_key):
                    from fastapi.responses import JSONResponse

                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing API key"},
                    )
                return await call_next(request)

        app.add_middleware(APIKeyMiddleware)
        logger.info("API Key authentication enabled")

    # ===== 仓库权限中间件：从 X-User-ID 提取 user_id 注入 request.state =====
    # 始终注入 user_id；具体拦截由路由层 require_access 触发（受 ONTOAGENT_ACL_ENABLED 控制）。
    app.add_middleware(RepoAuthMiddleware)

    # ===== 请求追踪 + 计时中间件（request_id + Prometheus metrics） =====
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        # 生成 request_id 并注入日志上下文
        request_id = request.headers.get("X-Request-ID") or uuid4().hex[:16]
        token = set_request_id(request_id)
        # 在响应头中回传 request_id（客户端可追踪）
        start_time = time.time()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            # call_next 抛异常时 response 为 None：按 500 记录，避免 UnboundLocalError 掩盖真实错误。
            # 嵌套 finally 保证 reset_request_id 始终执行，即使 record_http_request 自身抛错。
            try:
                duration = time.time() - start_time
                # 用路由模板（如 /api/graph/node/{node_id}）而非实际路径，避免 Prometheus label 基数爆炸
                route = request.scope.get("route")
                endpoint = route.path_format if route and hasattr(route, "path_format") else request.url.path
                status = response.status_code if response is not None else 500
                record_http_request(
                    method=request.method,
                    endpoint=endpoint,
                    status=status,
                    duration=duration,
                )
                if response is not None:
                    response.headers["X-Request-ID"] = request_id
            finally:
                reset_request_id(token)

    # ===== 注册路由 =====
    chat_router.collector = _trace_collector
    app.include_router(chat_router.router, prefix="/api")
    app.include_router(graph_router, prefix="/api")
    app.include_router(service_graph_eval_router, prefix="/api")

    # 挂载 trace router
    from ontoagent.api.web.router import trace as trace_router

    trace_router.collector = _trace_collector
    app.include_router(trace_router.router, prefix="/api")

    # 挂载 build / repo router
    from ontoagent.api.web.router import build as build_router
    from ontoagent.api.web.router import repo as repo_router

    app.include_router(build_router.router, prefix="/api")
    app.include_router(repo_router.router, prefix="/api")

    @app.get("/health")
    async def health(request: Request):
        """健康检查：验证 graph_store 连通性。

        返回 connected/space/tag_count/edge_count。
        若 graph_store 不可达，HTTP 503 + connected=False。
        """
        from fastapi.responses import JSONResponse

        store = getattr(request.app.state, "graph_store", None)
        if store is None:
            result = {"connected": False, "error": "graph_store not initialized"}
        elif hasattr(store, "health_check"):
            result = store.health_check()
        else:
            try:
                store.query("RETURN 1 AS ok")
                result = {"connected": True}
            except Exception:
                result = {"connected": False}

        status_code = 200 if result.get("connected") else 503
        return JSONResponse(status_code=status_code, content=result)

    @app.get("/metrics")
    async def metrics():
        """Prometheus metrics 端点。"""
        from fastapi import Response

        data, content_type = render_metrics()
        return Response(content=data, media_type=content_type)

    return app


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """自定义 429 响应，不依赖 slowapi 私有 API。"""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded"},
        headers={"Retry-After": str(getattr(exc, "retry_after", 60))},
    )
