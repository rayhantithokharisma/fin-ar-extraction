"""
Configuration settings for the IDX Extraction Pipeline.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

OPEN_ROUTER_API_KEY = os.getenv("OPEN_ROUTER_API_KEY", "")
AGENT_ROUTER_API_KEY = os.getenv("AGENT_ROUTER_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Determine LLM Provider: "openai", "gemini", "openrouter", or "agentrouter"
_explicit_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
if _explicit_provider in ("openai", "gemini", "openrouter", "agentrouter"):
    LLM_PROVIDER = _explicit_provider
elif OPENAI_API_KEY:
    LLM_PROVIDER = "openai"
elif GEMINI_API_KEY:
    LLM_PROVIDER = "gemini"
elif OPEN_ROUTER_API_KEY:
    LLM_PROVIDER = "openrouter"
else:
    LLM_PROVIDER = "gemini"

if LLM_PROVIDER == "openai":
    _env_model = os.getenv("DEFAULT_MODEL", "").strip()
    DEFAULT_MODEL = _env_model if _env_model and ("gpt" in _env_model.lower() or "o1" in _env_model.lower() or "o3" in _env_model.lower()) else "gpt-4o-mini"
    _env_fallback = os.getenv("FALLBACK_MODEL", "").strip()
    FALLBACK_MODEL = _env_fallback if _env_fallback and ("gpt" in _env_fallback.lower() or "o1" in _env_fallback.lower() or "o3" in _env_fallback.lower()) else "gpt-4o"
elif LLM_PROVIDER == "gemini":
    _env_model = os.getenv("DEFAULT_MODEL", "").strip()
    DEFAULT_MODEL = _env_model if _env_model and "nemotron" not in _env_model.lower() and "gpt" not in _env_model.lower() else "gemini-2.5-flash"
    _env_fallback = os.getenv("FALLBACK_MODEL", "").strip()
    FALLBACK_MODEL = _env_fallback if _env_fallback and "nemotron" not in _env_fallback.lower() and "gpt" not in _env_fallback.lower() else "gemini-2.5-pro"
else:
    _env_model = os.getenv("DEFAULT_MODEL", "").strip()
    DEFAULT_MODEL = _env_model if _env_model and not _env_model.startswith("gemini") and "gpt" not in _env_model.lower() else "nvidia/nemotron-3-super-120b-a12b:free"
    _env_fallback = os.getenv("FALLBACK_MODEL", "").strip()
    FALLBACK_MODEL = _env_fallback if _env_fallback and not _env_fallback.startswith("gemini") and "gpt" not in _env_fallback.lower() else "nvidia/nemotron-3-ultra-550b-a55b:free"

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True, parents=True)

DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "idxfin.duckdb")))
TAXONOMY_PATH = PROJECT_ROOT / "schema_design" / "taxonomy_seed.yaml"

# Staging lake & lock paths for safe single-writer DuckDB architecture
STAGING_DIR = Path(os.getenv("STAGING_DIR", str(DATA_DIR / "staging")))
STAGING_DIR.mkdir(exist_ok=True, parents=True)
DB_LOCK_PATH = Path(os.getenv("DB_LOCK_PATH", str(DATA_DIR / "idxfin.duckdb.lock")))

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "180.0"))

# Redis & Celery settings
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", "6"))
CELERY_QUEUE_EXTRACTION = "extraction"
CELERY_QUEUE_DUCKDB = "duckdb_writer"
