from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from layerkg.web.router.chat import router as chat_router


def create_app() -> FastAPI:
    app = FastAPI(title="LayerKG Agent", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat_router, prefix="/api")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
