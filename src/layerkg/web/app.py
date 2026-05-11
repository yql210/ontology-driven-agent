from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from layerkg.config import LayerKGConfig
from layerkg.neo4j_store import Neo4jGraphStore
from layerkg.web.router.chat import router as chat_router
from layerkg.web.router.graph import router as graph_router


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

    app.include_router(chat_router, prefix="/api")
    app.include_router(graph_router, prefix="/api")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
