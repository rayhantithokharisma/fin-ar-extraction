"""
API Route Handlers for Extraction, Task Status, and Analytical Queries.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, Query, status
import redis

from src.api.schemas import (
    ExtractionRequest,
    ExtractionResponse,
    HealthResponse,
    QueueStatusResponse,
    TaskStatusResponse,
)
from src.celery_app import app as celery_app
from src.config import (
    CELERY_QUEUE_DUCKDB,
    CELERY_QUEUE_EXTRACTION,
    DB_PATH,
    REDIS_URL,
)
from src.storage.database import DatabaseStore
from src.tasks import extract_document_task, ingest_staging_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Extraction & Pipeline"])


def _resolve_file_path(input_path: str) -> Path:
    """Resolve file path across host and Docker container filesystems."""
    p = Path(input_path)
    if p.exists():
        return p

    from src.config import PROJECT_ROOT
    if (PROJECT_ROOT / input_path).exists():
        return PROJECT_ROOT / input_path

    for anchor in ("complete-ticker", "examples", "data"):
        if anchor in input_path:
            rel_part = input_path[input_path.index(anchor):]
            candidate = PROJECT_ROOT / rel_part
            if candidate.exists():
                return candidate
    return p


def _enqueue_extraction(req: ExtractionRequest, doc_type: Optional[str] = None) -> ExtractionResponse:
    path = _resolve_file_path(req.file_path)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Filing PDF not found at path: {req.file_path} (resolved: {path})",
        )

    task = extract_document_task.apply_async(
        kwargs={
            "file_path": str(path.resolve()),
            "target_sections": req.sections,
            "model": req.model,
            "doc_type": doc_type,
            "auto_ingest": req.auto_ingest,
        },
        queue=CELERY_QUEUE_EXTRACTION,
    )

    return ExtractionResponse(
        task_id=task.id,
        status="QUEUED",
        doc_type=doc_type,
        file_path=str(path),
        file_name=path.name,
        poll_url=f"/api/v1/tasks/{task.id}",
    )


@router.post(
    "/extract/fs",
    response_model=ExtractionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue Financial Statement (LK) Extraction",
)
def extract_financial_statement(req: ExtractionRequest) -> ExtractionResponse:
    """
    Enqueue an Audited or Interim Financial Statement (Laporan Keuangan / LK) for extraction.
    Task is dispatched to the 6-worker Celery pool via Redis.
    """
    return _enqueue_extraction(req, doc_type="fs_annual")


@router.post(
    "/extract/ar",
    response_model=ExtractionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue Annual Report (AR) Extraction",
)
def extract_annual_report(req: ExtractionRequest) -> ExtractionResponse:
    """
    Enqueue a 400+ page Annual Report (Laporan Tahunan / AR) for extraction.
    Task is dispatched to the 6-worker Celery pool via Redis.
    """
    return _enqueue_extraction(req, doc_type="annual_report")


@router.post(
    "/extract",
    response_model=ExtractionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue Universal Document Extraction",
)
def extract_universal(req: ExtractionRequest) -> ExtractionResponse:
    """
    Enqueue any supported filing (LK or AR). The pipeline will automatically sniff metadata.
    """
    return _enqueue_extraction(req, doc_type=None)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="Get Celery Task Status & Extraction Progress",
)
def get_task_status(task_id: str) -> TaskStatusResponse:
    """
    Poll the execution status of an extraction or ingestion job.
    Possible states: PENDING, STARTED, PROGRESS, SUCCESS, FAILURE.
    """
    res = AsyncResult(task_id, app=celery_app)
    state = res.state

    response = TaskStatusResponse(task_id=task_id, state=state)

    if state == "PROGRESS":
        response.progress = res.info if isinstance(res.info, dict) else {}
    elif state == "SUCCESS":
        response.result = res.result if isinstance(res.result, dict) else {}
    elif state == "FAILURE":
        response.error = str(res.result)
    elif state == "STARTED":
        response.progress = res.info if isinstance(res.info, dict) else {}

    return response


@router.get(
    "/tasks",
    response_model=QueueStatusResponse,
    summary="Inspect Worker Pool & Queue Health",
)
def get_queue_status() -> QueueStatusResponse:
    """
    Inspect registered Celery workers, active task counts, and Redis connectivity.
    """
    redis_ok = False
    try:
        r = redis.Redis.from_url(REDIS_URL, socket_timeout=2)
        redis_ok = bool(r.ping())
    except Exception:
        redis_ok = False

    inspector = celery_app.control.inspect(timeout=1.0)
    workers = []
    active_count = 0
    scheduled_count = 0
    reserved_count = 0

    if inspector:
        try:
            active_dict = inspector.active() or {}
            scheduled_dict = inspector.scheduled() or {}
            workers = list(active_dict.keys())
            active_count = sum(len(tasks) for tasks in active_dict.values())
            scheduled_count = sum(len(tasks) for tasks in scheduled_dict.values())
            reserved_count = sum(len(tasks) for tasks in reserved_dict.values())
        except Exception as e:
            logger.warning(f"Error inspecting Celery workers: {e}")

    queue_depth = {}
    total_queued = 0
    if redis_ok:
        try:
            for q in (CELERY_QUEUE_EXTRACTION, CELERY_QUEUE_DUCKDB):
                count = int(r.llen(q))
                queue_depth[q] = count
                total_queued += count
        except Exception as e:
            logger.warning(f"Error reading Redis queue lengths: {e}")

    return QueueStatusResponse(
        status="ONLINE" if redis_ok else "DEGRADED",
        redis_connected=redis_ok,
        registered_workers=workers,
        active_tasks_count=active_count,
        queued_tasks_count=total_queued,
        queue_depth=queue_depth,
        scheduled_tasks_count=scheduled_count,
        reserved_tasks_count=reserved_count,
    )


@router.get(
    "/gold/summary",
    summary="Query Gold Financial Summary Analytical View",
)
def get_gold_summary(ticker: Optional[str] = Query(default=None, description="Optional issuer ticker filter (e.g. NCKL)")) -> list[dict[str, Any]]:
    """
    Query the DuckDB Gold analytical summary view (idxfin_gold.financial_summary) in read-only mode.
    """
    try:
        db = DatabaseStore(read_only=True)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not open DuckDB in read-only mode: {e}",
        )

    try:
        query = "SELECT * FROM idxfin_gold.financial_summary"
        if ticker:
            query += f" WHERE issuer_id = '{ticker.upper()}'"
        query += " ORDER BY issuer_id, period_end DESC"
        df = db.conn.execute(query).df()
        return df.to_dict(orient="records")
    finally:
        db.close()


@router.post(
    "/ingest/{run_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger Manual DuckDB Ingestion for Staged Run",
)
def manual_ingest(run_id: str) -> dict[str, Any]:
    """
    Manually dispatch staged Parquet ingestion into DuckDB.
    Dispatched to the single-writer queue (duckdb_writer).
    """
    task = ingest_staging_task.apply_async(args=[run_id], queue=CELERY_QUEUE_DUCKDB)
    return {
        "task_id": task.id,
        "run_id": run_id,
        "status": "QUEUED",
        "poll_url": f"/api/v1/tasks/{task.id}",
    }
