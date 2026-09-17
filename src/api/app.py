"""
FastAPI Application Entrypoint for IDX Statement & Annual Report Extraction.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import redis

from src.api.routes import router
from src.api.schemas import HealthResponse
from src.config import DB_PATH, REDIS_URL
from src.storage.database import DatabaseStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting IDX Extraction Gateway API...")
    # Optional DB readiness check
    try:
        db = DatabaseStore(read_only=True)
        db.close()
        logger.info(f"DuckDB accessible at {DB_PATH}")
    except Exception as e:
        logger.warning(f"DuckDB check during startup note: {e}")
    yield
    logger.info("Shutting down IDX Extraction Gateway API...")


app = FastAPI(
    title="IDX Statement & Annual Report Extraction Service",
    description="Enterprise-grade distributed financial extraction pipeline with Redis, Celery, Parquet Staging, and DuckDB single-writer.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routes
app.include_router(router)


@app.get("/", tags=["Health"])
def root():
    return {
        "service": "IDX Financial Extraction Pipeline",
        "status": "ONLINE",
        "docs_url": "/docs",
        "endpoints": {
            "extract_fs": "POST /api/v1/extract/fs",
            "extract_ar": "POST /api/v1/extract/ar",
            "extract_universal": "POST /api/v1/extract",
            "task_status": "GET /api/v1/tasks/{task_id}",
            "queue_status": "GET /api/v1/tasks",
            "gold_summary": "GET /api/v1/gold/summary",
        },
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health():
    # Check Redis
    redis_ok = False
    try:
        r = redis.Redis.from_url(REDIS_URL, socket_timeout=2)
        redis_ok = bool(r.ping())
    except Exception:
        redis_ok = False

    # Check DuckDB
    duckdb_ok = False
    try:
        db = DatabaseStore(read_only=True)
        duckdb_ok = True
        db.close()
    except Exception:
        duckdb_ok = False

    return HealthResponse(
        status="HEALTHY" if (redis_ok and duckdb_ok) else "DEGRADED",
        redis=redis_ok,
        duckdb=duckdb_ok,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.app:app", host="0.0.0.0", port=8000, reload=True)
