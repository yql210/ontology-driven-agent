from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from layerkg.agent.trace import TraceCollector
from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.web.router import chat as chat_router
from layerkg.web.router.graph import router as graph_router

# TraceCollector 单例
_trace_collector = TraceCollector()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = LayerKGConfig.from_env()
    store = Neo4jGraphStore(
        uri=config.neo4j_uri, user=config.neo4j_user, password=config.neo4j_password
    )
    app.state.graph_store = store
    yield
    store.close()


def create_app() -> FastAPI:
    app = FastAPI(title="LayerKG Agent", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注入 TraceCollector 到 chat router
    chat_router.collector = _trace_collector
    app.include_router(chat_router.router, prefix="/api")
    app.include_router(graph_router, prefix="/api")

    # 挂载 trace router
    from layerkg.web.router import trace as trace_router

    trace_router.collector = _trace_collector
    app.include_router(trace_router.router, prefix="/api")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
