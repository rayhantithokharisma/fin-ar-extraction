"""
Celery Task Definitions for Asynchronous Document Extraction & Serialized DuckDB Ingestion.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from src.celery_app import app
from src.config import CELERY_QUEUE_DUCKDB
from src.pipeline import ExtractionPipeline
from src.storage.database import DatabaseStore

logger = logging.getLogger(__name__)


@app.task(bind=True, name="src.tasks.extract_document_task")
def extract_document_task(
    self,
    file_path: str,
    target_sections: Optional[list[str]] = None,
    model: Optional[str] = None,
    doc_type: Optional[str] = None,
    auto_ingest: bool = True,
) -> dict[str, Any]:
    """
    Celery task to run extraction on a PDF document.
    Executes in stateless worker mode: writes output to Parquet staging, then chains ingestion.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Target PDF filing not found: {file_path}")

    logger.info(f"[Task {self.request.id}] Starting extraction on {path.name} (type: {doc_type or 'auto'})")
    self.update_state(state="STARTED", meta={"file_name": path.name, "doc_type": doc_type, "percent": 0})

    def progress_callback(meta: dict[str, Any]) -> None:
        logger.info(f"[Task {self.request.id}] Progress: {meta}")
        self.update_state(state="PROGRESS", meta={"file_name": path.name, **meta})

    # Worker executes in staging-only mode: NO connection to DuckDB
    pipeline = ExtractionPipeline(staging_only=True)
    res = pipeline.run(
        file_path=path,
        target_sections=target_sections,
        model=model,
        progress_callback=progress_callback,
    )

    run_id = res["run_id"]
    result_payload = {
        "task_id": self.request.id,
        "run_id": run_id,
        "file_name": path.name,
        "doc_type": doc_type or res["summary"].get("doc_type"),
        "staged_dir": res["staged_dir"],
        "summary": res["summary"],
        "status": "EXTRACTED",
    }

    # Automatically dispatch DuckDB ingestion task to single-writer queue
    if auto_ingest:
        logger.info(f"[Task {self.request.id}] Enqueuing DuckDB ingestion task for run {run_id}...")
        ingest_staging_task.apply_async(args=[run_id], queue=CELERY_QUEUE_DUCKDB)
        result_payload["ingest_queued"] = True

    return result_payload


@app.task(name="src.tasks.ingest_staging_task")
def ingest_staging_task(run_id: str) -> dict[str, Any]:
    """
    Celery task to ingest staged Parquet files into DuckDB and materialize Gold views.
    Runs on queue 'duckdb_writer' where concurrency is strictly 1.
    """
    logger.info(f"Starting serialized DuckDB ingestion for run {run_id}...")
    db = DatabaseStore()
    try:
        stats = db.ingest_from_staging(run_id)
        logger.info(f"DuckDB ingestion completed for run {run_id}: {stats}")
        return stats
    finally:
        db.close()
