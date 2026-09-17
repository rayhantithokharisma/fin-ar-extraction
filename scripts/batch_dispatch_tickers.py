#!/usr/bin/env python3
"""
Batch Dispatcher Script for Financial Statements (FS) and Annual Reports (AR).
Scans issuer PDF documents from the `complete-ticker/` folder and enqueues extraction tasks
via the FastAPI distributed queue gateway (`POST /api/v1/extract/fs` and `/api/v1/extract/ar`).

NOTE: Automatically detects and skips filings that are already queued in Redis,
staged in Parquet, or completed in DuckDB, preventing duplicate tasks.

Usage:
  # 1. Dry run: Preview remaining files that will be submitted (skips already processed/queued)
  python3 scripts/batch_dispatch_tickers.py --dry-run

  # 2. Extract remaining filings across all 175 tickers (skips already queued/processed)
  python3 scripts/batch_dispatch_tickers.py --type all

  # 3. Force re-enqueue all filings (ignores previous status)
  python3 scripts/batch_dispatch_tickers.py --force

  # 4. Check progress of previously enqueued tasks
  python3 scripts/batch_dispatch_tickers.py --status
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import duckdb
import redis
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "complete-ticker"
DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
TASKS_TRACKING_FILE = PROJECT_ROOT / "batch_tasks.json"


def parse_filename(filename: str) -> tuple[Optional[str], Optional[int]]:
    """
    Extract filing type and fiscal year from filename.
    Examples:
      'FinancialStatement2025.pdf' -> ('fs', 2025)
      'AnnualReport2024.pdf'       -> ('ar', 2024)
      'FinancialStatement-2025-Tahunan-NCKL.pdf' -> ('fs', 2025)
    """
    lower = filename.lower()
    doc_type = None
    if "financialstatement" in lower or "laporankeuangan" in lower or "fs" in lower:
        doc_type = "fs"
    elif "annualreport" in lower or "laporantahunan" in lower or "ar" in lower:
        doc_type = "ar"

    year_match = re.search(r"(201\d|202\d)", filename)
    year = int(year_match.group(1)) if year_match else None

    return doc_type, year


def get_already_processed_or_queued_keys(
    redis_url: str = DEFAULT_REDIS_URL,
    duckdb_path: Path = PROJECT_ROOT / "data" / "idxfin.duckdb",
    tracking_file: Path = TASKS_TRACKING_FILE,
) -> tuple[set[str], dict[str, int]]:
    """
    Collect normalized keys (e.g. 'nckl/annualreport2025.pdf') of all filings
    that are already processed or queued across 4 system tiers:
      Tier 1: Completed in DuckDB (idxfin_bronze.document)
      Tier 2: In Staging Lake (data/staging/completed or pending)
      Tier 3: In Redis extraction queue
      Tier 4: In batch_tasks.json tracking file
    """
    seen_keys: set[str] = set()
    breakdown = {"duckdb": 0, "staging": 0, "redis_queue": 0, "tracking_file": 0}

    def _normalize_key(raw_path: str) -> str:
        p = Path(raw_path)
        return f"{p.parent.name}/{p.name}".lower()

    # 1. Check DuckDB Ingested Documents
    if duckdb_path.exists():
        try:
            conn = duckdb.connect(str(duckdb_path), read_only=True)
            rows = conn.execute("SELECT storage_path FROM idxfin_bronze.document").fetchall()
            for (sp,) in rows:
                if sp:
                    key = _normalize_key(sp)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        breakdown["duckdb"] += 1
            conn.close()
        except Exception:
            pass

    # 2. Check Staging Lake (completed & pending)
    staging_base = PROJECT_ROOT / "data" / "staging"
    for subdir in ("completed", "pending"):
        stage_dir = staging_base / subdir
        if stage_dir.exists():
            for run_p in stage_dir.iterdir():
                if not run_p.is_dir():
                    continue
                doc_f = run_p / "document.parquet"
                if doc_f.exists():
                    try:
                        import pandas as pd
                        df = pd.read_parquet(doc_f, columns=["storage_path"])
                        for sp in df["storage_path"]:
                            if sp:
                                key = _normalize_key(sp)
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    breakdown["staging"] += 1
                    except Exception:
                        pass

    # 3. Check Redis Extraction Queue
    try:
        r = redis.Redis.from_url(redis_url, socket_timeout=2)
        for item in r.lrange("extraction", 0, -1):
            try:
                data = json.loads(item)
                body = json.loads(base64.b64decode(data["body"]).decode("utf-8"))
                args, kwargs, *_ = body
                fp = kwargs.get("file_path")
                if fp:
                    key = _normalize_key(fp)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        breakdown["redis_queue"] += 1
            except Exception:
                pass
    except Exception:
        pass

    # 4. Check Tracking File (batch_tasks.json)
    if tracking_file.exists():
        try:
            with open(tracking_file, "r") as f:
                tasks = json.load(f)
            for t in tasks:
                fp = t.get("file_path") or f"{t.get('ticker')}/{t.get('file_name')}"
                if fp:
                    key = _normalize_key(fp)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        breakdown["tracking_file"] += 1
        except Exception:
            pass

    return seen_keys, breakdown


def discover_files(
    source_dir: Path,
    target_tickers: Optional[set[str]] = None,
    target_years: Optional[set[int]] = None,
    target_type: str = "all",
    skip_existing: bool = True,
    already_seen: Optional[set[str]] = None,
) -> tuple[list[dict[str, Any]], int]:
    """
    Scan complete-ticker folder and collect matching PDF files.
    Automatically filters out files already processed or queued.
    """
    if not source_dir.exists():
        print(f"Error: Source directory not found: {source_dir}")
        sys.exit(1)

    items = []
    skipped_count = 0
    seen_set = already_seen or set()

    # Loop over ticker subdirectories
    for ticker_dir in sorted(source_dir.iterdir()):
        if not ticker_dir.is_dir() or ticker_dir.name.startswith("."):
            continue

        ticker = ticker_dir.name.upper()
        if target_tickers and ticker not in target_tickers:
            continue

        for pdf_path in sorted(ticker_dir.glob("*.pdf")):
            doc_type, year = parse_filename(pdf_path.name)
            if not doc_type:
                continue

            if target_type != "all" and doc_type != target_type:
                continue

            if target_years and year and year not in target_years:
                continue

            # Check if file has already been processed or queued
            file_key = f"{ticker_dir.name}/{pdf_path.name}".lower()
            if skip_existing and file_key in seen_set:
                skipped_count += 1
                continue

            try:
                rel_path = str(pdf_path.relative_to(PROJECT_ROOT))
            except ValueError:
                rel_path = str(pdf_path.resolve())

            items.append({
                "ticker": ticker,
                "file_name": pdf_path.name,
                "file_path": rel_path,
                "file_key": file_key,
                "doc_type": doc_type,
                "year": year,
                "size_mb": round(pdf_path.stat().st_size / (1024 * 1024), 2),
            })

    return items, skipped_count


def dispatch_batch(
    files: list[dict[str, Any]],
    api_url: str,
    delay: float = 0.2,
    output_file: Path = TASKS_TRACKING_FILE,
) -> None:
    """Send extraction requests to FastAPI endpoints and record task IDs continuously."""
    print(f"\n=======================================================")
    print(f" DISPATCHING {len(files)} FILINGS TO EXTRACTOR QUEUE")
    print(f" Target Gateway: {api_url}")
    print(f"=======================================================\n")

    # Check API health first
    try:
        health_resp = requests.get(f"{api_url}/health", timeout=3.0)
        health_data = health_resp.json()
        print(f"[Health Check] Gateway Status: {health_data.get('status')}")
        print(f"  Redis:  {'CONNECTED' if health_data.get('redis') else 'DISCONNECTED'}")
        print(f"  DuckDB: {'CONNECTED' if health_data.get('duckdb') else 'DISCONNECTED'}\n")
        if not health_data.get("redis"):
            print("ERROR: Redis is not connected to the API gateway. Aborting dispatch.")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to connect to API Gateway at {api_url}: {e}")
        print("Please ensure the FastAPI service is running (e.g. docker-compose up -d or ./scripts/start_api.sh)")
        sys.exit(1)

    # Load existing tracking state so we update incrementally
    existing_tasks: list[dict[str, Any]] = []
    if output_file.exists():
        try:
            with open(output_file, "r") as f:
                existing_tasks = json.load(f)
        except Exception:
            existing_tasks = []

    enqueued_tasks: list[dict[str, Any]] = []
    success_count = 0
    fail_count = 0

    def _flush_tracking():
        """Persist current progress to batch_tasks.json immediately."""
        combined = existing_tasks + enqueued_tasks
        # Deduplicate by task_id
        seen_ids = set()
        deduped = []
        for item in combined:
            tid = item.get("task_id")
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                deduped.append(item)
            elif not tid:
                deduped.append(item)
        with open(output_file, "w") as f:
            json.dump(deduped, f, indent=2)

    try:
        for idx, item in enumerate(files, 1):
            endpoint = "/api/v1/extract/fs" if item["doc_type"] == "fs" else "/api/v1/extract/ar"
            url = f"{api_url}{endpoint}"
            payload = {
                "file_path": item["file_path"],
                "auto_ingest": True,
            }

            try:
                resp = requests.post(url, json=payload, timeout=10.0)
                if resp.status_code == 202:
                    data = resp.json()
                    task_id = data.get("task_id")
                    print(f"[{idx:>4}/{len(files)}] Enqueued {item['ticker']:<5} {item['doc_type'].upper():<2} {item['file_name']:<35} -> Task ID: {task_id}")
                    enqueued_tasks.append({
                        "task_id": task_id,
                        "ticker": item["ticker"],
                        "doc_type": item["doc_type"],
                        "year": item["year"],
                        "file_name": item["file_name"],
                        "file_path": item["file_path"],
                        "enqueued_at": datetime.now().isoformat(),
                        "status_url": f"{api_url}/api/v1/tasks/{task_id}",
                        "last_known_state": "QUEUED",
                    })
                    success_count += 1
                    
                    # Flush to batch_tasks.json every 5 items
                    if len(enqueued_tasks) % 5 == 0:
                        _flush_tracking()
                else:
                    print(f"[{idx:>4}/{len(files)}] FAILED {item['ticker']:<5} {item['file_name']}: HTTP {resp.status_code} - {resp.text}")
                    fail_count += 1
            except Exception as e:
                print(f"[{idx:>4}/{len(files)}] ERROR {item['ticker']:<5} {item['file_name']}: {e}")
                fail_count += 1

            if delay > 0:
                time.sleep(delay)

    finally:
        # Guarantee state is saved even if interrupted with Ctrl+C
        _flush_tracking()

    print(f"\n-------------------------------------------------------")
    print(f" Dispatch Summary:")
    print(f"   Successfully Enqueued: {success_count}")
    print(f"   Failed Submissions:    {fail_count}")
    print(f"   Tracking file saved:   {output_file}")
    print(f"\nTo monitor task statuses, run:")
    print(f"   python3 scripts/batch_dispatch_tickers.py --status\n")


def check_task_status(output_file: Path = TASKS_TRACKING_FILE) -> None:
    """Read batch_tasks.json and query current status for all tasks."""
    if not output_file.exists():
        print(f"No tracking file found at {output_file}. Run a dispatch first.")
        return

    with open(output_file, "r") as f:
        tasks = json.load(f)

    print(f"\n=======================================================")
    print(f" CHECKING STATUS FOR {len(tasks)} ENQUEUED TASKS")
    print(f"=======================================================\n")
    print(f"{'Ticker':<7} {'Type':<4} {'Year':<6} {'Task ID':<38} {'State':<12} {'Progress / Info'}")
    print(f"{'-'*95}")

    counts = {"PENDING": 0, "STARTED": 0, "PROGRESS": 0, "SUCCESS": 0, "FAILURE": 0, "UNKNOWN": 0}

    for t in tasks:
        url = t.get("status_url")
        state = "UNKNOWN"
        info = "-"
        try:
            resp = requests.get(url, timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                state = data.get("state", "UNKNOWN")
                if state == "PROGRESS":
                    prog = data.get("progress", {})
                    pct = prog.get("percent", 0)
                    sec = prog.get("current_section", prog.get("status", ""))
                    info = f"{pct}% ({sec})"
                elif state == "SUCCESS":
                    summary = data.get("result", {}).get("summary", {})
                    facts = summary.get("facts_extracted", 0)
                    info = f"{facts} facts extracted"
                elif state == "FAILURE":
                    info = str(data.get("error", ""))[:30]
        except Exception as e:
            state = "OFFLINE"
            info = str(e)[:25]

        counts[state] = counts.get(state, 0) + 1
        year_str = str(t.get("year", "-"))
        print(f"{t['ticker']:<7} {t['doc_type'].upper():<4} {year_str:<6} {t['task_id']:<38} {state:<12} {info}")

    print(f"{'-'*95}")
    print("Aggregate Status Breakdown:")
    for k, v in counts.items():
        if v > 0:
            print(f"  {k:<10}: {v}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Batch Dispatcher for Financial Statement (LK) & Annual Report (AR) Extractions",
    )
    parser.add_argument(
        "--source-dir",
        default=str(DEFAULT_SOURCE_DIR),
        help=f"Path to complete-ticker folder (default: {DEFAULT_SOURCE_DIR})",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"Base URL for FastAPI Gateway (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated ticker filter (e.g. NCKL,ADRO,BBCA). Default: all tickers.",
    )
    parser.add_argument(
        "--years",
        default=None,
        help="Comma-separated fiscal year filter (e.g. 2024,2025). Default: all years.",
    )
    parser.add_argument(
        "--type",
        choices=["all", "fs", "ar"],
        default="all",
        help="Filing type to process: 'fs' (Financial Statement), 'ar' (Annual Report), or 'all' (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-enqueue even if the filing is already completed, staged, or waiting in the Redis queue.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and preview files to dispatch without sending HTTP requests.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="Delay in seconds between request submissions (default: 0.1s)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check the live execution status of previously enqueued tasks in batch_tasks.json",
    )

    args = parser.parse_args()

    if args.status:
        check_task_status()
        return

    source_path = Path(args.source_dir)
    target_tickers = set(t.strip().upper() for t in args.tickers.split(",")) if args.tickers else None
    target_years = set(int(y.strip()) for y in args.years.split(",")) if args.years else None

    # Retrieve already queued or processed files across all 4 system tiers
    skip_existing = not args.force
    already_seen: set[str] = set()
    if skip_existing:
        already_seen, breakdown = get_already_processed_or_queued_keys()
        print(f"\n[Deduplication Index] Found {len(already_seen)} filings already queued or processed:")
        print(f"  - In DuckDB Warehouse:     {breakdown['duckdb']}")
        print(f"  - In Parquet Staging Lake: {breakdown['staging']}")
        print(f"  - In Redis Queue:          {breakdown['redis_queue']}")
        print(f"  - In batch_tasks.json:     {breakdown['tracking_file']}")

    files, skipped_count = discover_files(
        source_dir=source_path,
        target_tickers=target_tickers,
        target_years=target_years,
        target_type=args.type,
        skip_existing=skip_existing,
        already_seen=already_seen,
    )

    total_matching = len(files) + skipped_count
    print(f"\nDiscovered {total_matching} total matching filing PDFs across {source_path.name}/.")
    if skip_existing and skipped_count > 0:
        print(f"  -> Skipped {skipped_count} filings (already queued or processed).")
    print(f"  -> Ready to dispatch: {len(files)} new filings.")

    if len(files) == 0:
        print("\nAll matching filings have already been queued or processed! Nothing to do.")
        print("To force re-enqueueing, run with the --force flag:\n  python3 scripts/batch_dispatch_tickers.py --force\n")
        return

    if args.dry_run:
        print("\n[DRY RUN PREVIEW - NO REQUESTS SENT]")
        print(f"{'-'*75}")
        print(f"{'Ticker':<8} {'Type':<6} {'Year':<6} {'Size (MB)':<12} {'File Name'}")
        print(f"{'-'*75}")
        for item in files[:30]:
            print(f"{item['ticker']:<8} {item['doc_type'].upper():<6} {str(item['year']):<6} {item['size_mb']:<12} {item['file_name']}")
        if len(files) > 30:
            print(f"... and {len(files) - 30} more new files.")
        print(f"{'-'*75}\n")
        print("To enqueue these files, run without --dry-run:")
        print(f"  python3 scripts/batch_dispatch_tickers.py --type {args.type}\n")
        return

    dispatch_batch(files=files, api_url=args.api_url, delay=args.delay)


if __name__ == "__main__":
    main()
