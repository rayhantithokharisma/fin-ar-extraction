"""
Celery Distributed Task Queue Application.
Configured for Redis broker/backend with backpressure queueing and separate worker pools:
- Extraction pool: concurrency = 6 (processes PDF and LLM extractions to Parquet staging)
- DuckDB ingestion pool: concurrency = 1 (serializes ACID writes to DuckDB with FileLock)
"""
from __future__ import annotations

import logging
from celery import Celery
from kombu import Exchange, Queue

from src.config import (
    CELERY_QUEUE_DUCKDB,
    CELERY_QUEUE_EXTRACTION,
    CELERY_RESULT_BACKEND,
    CELERY_WORKER_CONCURRENCY,
    REDIS_URL,
)

logger = logging.getLogger(__name__)

# Initialize Celery app
app = Celery(
    "idxfin_extraction",
    broker=REDIS_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["src.tasks"],
)

# Queue definitions
default_exchange = Exchange("default", type="direct")

app.conf.task_queues = (
    Queue(CELERY_QUEUE_EXTRACTION, default_exchange, routing_key=CELERY_QUEUE_EXTRACTION),
    Queue(CELERY_QUEUE_DUCKDB, default_exchange, routing_key=CELERY_QUEUE_DUCKDB),
)

# Routing
app.conf.task_routes = {
    "src.tasks.extract_document_task": {"queue": CELERY_QUEUE_EXTRACTION},
    "src.tasks.ingest_staging_task": {"queue": CELERY_QUEUE_DUCKDB},
}

# Concurrency & Backpressure configuration
app.conf.update(
    # Worker concurrency defaults (overridable via -c CLI argument)
    worker_concurrency=CELERY_WORKER_CONCURRENCY,
    
    # CRITICAL: Do NOT prefetch tasks ahead of time.
    # Tasks remain queued in Redis until an active worker slot is available.
    worker_prefetch_multiplier=1,
    
    # Acknowledge task only after completion; re-queues task if worker crashes
    task_acks_late=True,
    
    # Track task start state
    task_track_started=True,

    # Connection retry limits (fail fast if Redis broker is offline)
    broker_connection_retry_on_startup=False,
    broker_connection_max_retries=2,
    result_backend_transport_options={"max_retries": 2, "interval_start": 0.5, "interval_step": 0.5},
    
    # Serialization format
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Jakarta",
    enable_utc=True,
    
    # Result expiry (store job status for 24 hours)
    result_expires=86400,
)

if __name__ == "__main__":
    app.start()

