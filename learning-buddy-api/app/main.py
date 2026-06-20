import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import AuthMiddleware
from app.config import settings
from app.db.session import get_engine
from app.rag.corpus.pipeline import verify_and_index
from app.routes.admin import router as admin_router
from app.routes.graph_runs import router as graph_runs_router
from app.routes.health import router as health_router
from app.routes.homework import router as homework_router
from app.routes.ingest import router as ingest_router
from app.routes.mastery import router as mastery_router
from app.routes.questions import router as questions_router
from app.routes.rag import router as rag_router
from app.routes.students import router as students_router

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_engine()
    try:
        s, q = verify_and_index(force=False)
        logger.info("Corpus ready: syllabus=%s question_bank=%s", s, q)
    except Exception as e:
        logger.warning("Corpus index deferred: %s", e)
    yield


app = FastAPI(
    title="Learning Buddy API",
    description="A / B / A+ pilot service (≤5 students)",
    version="0.2.0-pilot",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)

app.include_router(health_router)
app.include_router(admin_router)
app.include_router(graph_runs_router)
app.include_router(students_router)
app.include_router(ingest_router)
app.include_router(homework_router)
app.include_router(questions_router)
app.include_router(mastery_router)
app.include_router(rag_router)


@app.get("/metrics")
async def metrics_placeholder():
    """Prometheus scrape placeholder (instrumentator disabled: FastAPI nested-router compat)."""
    return {"status": "metrics disabled in pilot"}


@app.get("/")
async def root():
    return {
        "service": "learning-buddy-api",
        "health": "/api/v1/system/health",
        "max_students": settings.max_students,
    }


def main() -> None:
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
