import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from ontoagent.agent.trace import TraceCollector
from ontoagent.api.web.router import chat as chat_router
from ontoagent.api.web.router.graph import router as graph_router
from ontoagent.config import OntoAgentConfig
from ontoagent.store.factory import create_graph_store

logger = logging.getLogger(__name__)

# TraceCollector 单例
_trace_collector = TraceCollector()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = OntoAgentConfig.from_env()
    store = create_graph_store(config)
    app.state.graph_store = store
    yield
    store.close()


def create_app() -> FastAPI:
    app = FastAPI(title="OntoAgent Agent", version="0.1.0", lifespan=lifespan)

    # CORS origins from env, default to localhost:5173
    cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    cors_origins = [origin.strip() for origin in cors_origins_str.split(",") if origin.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API Key 认证（设置 ONTOAGENT_API_KEY 后生效）
    api_key = os.getenv("ONTOAGENT_API_KEY", "")
    if api_key:
        from starlette.middleware.base import BaseHTTPMiddleware

        class APIKeyMiddleware(BaseHTTPMiddleware):
            """验证 Authorization: Bearer <key> 或 X-API-Key: <key>。

            /health 端点免认证（K8s probe 需要）。
            """

            async def dispatch(self, request, call_next):
                # /health 免认证
                if request.url.path == "/health":
                    return await call_next(request)
                # 检查 Bearer token 或 X-API-Key header
                auth = request.headers.get("Authorization", "")
                xkey = request.headers.get("X-API-Key", "")
                token = ""
                if auth.startswith("Bearer "):
                    token = auth[7:]
                elif xkey:
                    token = xkey
                if token != api_key:
                    from fastapi.responses import JSONResponse

                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or missing API key"},
                    )
                return await call_next(request)

        app.add_middleware(APIKeyMiddleware)
        logger.info("API Key authentication enabled")

    # 注入 TraceCollector 到 chat router
    chat_router.collector = _trace_collector
    app.include_router(chat_router.router, prefix="/api")
    app.include_router(graph_router, prefix="/api")

    # 挂载 trace router
    from ontoagent.api.web.router import trace as trace_router

    trace_router.collector = _trace_collector
    app.include_router(trace_router.router, prefix="/api")

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

    return app
