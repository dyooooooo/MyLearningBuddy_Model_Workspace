import logging
from pathlib import Path

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routes import router
from app.companion.routes import router as companion_router
from app.studio.routes import router as studio_router
from app.studio.photo_import import router as studio_photo_router
from app.studio_auth import StudioAuthMiddleware
from app.openai_proxy import router as openai_proxy_router
from app.vllm_client import vllm_client
from app.buddy_client import aclose as buddy_aclose

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await vllm_client.aclose()
    await buddy_aclose()


app = FastAPI(
    title="Learning Buddy C2 Gateway",
    description="C2 gateway — vLLM vision + companion pipeline (Spark dual-Docker)",
    version="0.2.0-spark",
    lifespan=lifespan,
)
# /api/v1/studio/* requires browser Studio token (not the upstream admin token).
app.add_middleware(StudioAuthMiddleware)
app.include_router(router)
app.include_router(companion_router)
app.include_router(studio_router)
app.include_router(studio_photo_router, prefix="/api/v1/studio")
# WSL WTR etc.: OpenAI-compatible LAN entry (auth via VLLM_LAN_TOKEN)
app.include_router(openai_proxy_router, prefix="/v1")

STUDIO_DIR = STATIC_DIR / "studio"
if STATIC_DIR.is_dir():
    app.mount("/test", StaticFiles(directory=str(STATIC_DIR), html=True), name="test-ui")
if STUDIO_DIR.is_dir():
    app.mount("/studio", StaticFiles(directory=str(STUDIO_DIR), html=True), name="studio-ui")


@app.get("/", response_model=None)
async def root():
    if STATIC_DIR.is_dir():
        return RedirectResponse(url="/test/", status_code=302)
    return {
        "service": "c2-gateway",
        "health": "/api/v1/system/health",
        "backend": "vllm-vision",
    }


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
