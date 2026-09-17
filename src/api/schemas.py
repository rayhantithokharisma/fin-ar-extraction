"""
Pydantic API Request and Response Schemas.
"""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class ExtractionRequest(BaseModel):
    file_path: str = Field(..., description="Path to PDF filing (e.g. examples/nckl/FinancialStatement-2025-Tahunan-NCKL.pdf)")
    sections: Optional[list[str]] = Field(default=None, description="Optional target section IDs or types to extract")
    model: Optional[str] = Field(default=None, description="OpenRouter model override (e.g. google/gemini-2.5-flash)")
    auto_ingest: bool = Field(default=True, description="Whether to automatically ingest into DuckDB upon extraction completion")


class ExtractionResponse(BaseModel):
    task_id: str
    status: str
    doc_type: Optional[str] = None
    file_path: str
    file_name: str
    poll_url: str


class TaskStatusResponse(BaseModel):
    task_id: str
    state: str  # PENDING, STARTED, PROGRESS, SUCCESS, FAILURE, RETRY
    progress: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class QueueStatusResponse(BaseModel):
    status: str
    redis_connected: bool
    registered_workers: list[str]
    active_tasks_count: int
    queued_tasks_count: int = 0
    queue_depth: dict[str, int] = {}
    scheduled_tasks_count: int = 0
    reserved_tasks_count: int = 0


class IngestionRequest(BaseModel):
    run_id: str = Field(..., description="Staging run ID to ingest into DuckDB")


class HealthResponse(BaseModel):
    status: str
    redis: bool
    duckdb: bool
    version: str = "1.0.0"
