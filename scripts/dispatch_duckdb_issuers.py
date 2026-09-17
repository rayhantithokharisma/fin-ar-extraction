#!/usr/bin/env python3
"""
Dispatcher and Progress Tracker for the 24 Issuers already present in DuckDB.
Ensures all 154 filings (FS & AR) for these issuers are dispatched to the Celery extraction queue
with high priority, processed using the updated extraction pipeline, and ingested into DuckDB.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import duckdb
import redis
import requests

from src.celery_app import app as celery_app
from src.config import CELERY_QUEUE_EXTRACTION, DEFAULT_MODEL, REDIS_URL
from src.tasks import extract_document_task
TRACKING_FILE = PROJECT_ROOT / "duckdb_issuers_batch.json"

DB_ISSUERS = [
    "AALI", "ABMM", "ADES", "ADMG", "ADRO", "AGII", "AKRA", "ALDO",
    "AMMN", "AMMS", "ANDI", "ANJT", "ARCI", "ATIC", "BBCA", "BBSI",
    "BISI", "BREN", "CASS", "DMAS", "IPCM", "LSIP", "MAPI", "NCKL"
]


def discover_duckdb_filings(
    target_tickers: Optional[list[str]] = None,
    target_type: str = "all",
) -> list[dict[str, Any]]:
    """Collect all filing PDFs for the targeted DuckDB issuers from complete-ticker and examples."""
    tickers = [t.upper() for t in (target_tickers or DB_ISSUERS)]
    complete_dir = PROJECT_ROOT / "complete-ticker"
    examples_dir = PROJECT_ROOT / "examples"

    filings = []

    for ticker in sorted(tickers):
        found = []
        # Check complete-ticker
        tdir = complete_dir / ticker.lower()
        if tdir.exists():
            for p in sorted(tdir.glob("*.pdf")):
                found.append(p)

        # Check examples
        edir = examples_dir / ticker.lower()
        if edir.exists():
            for p in sorted(edir.glob("*.pdf")):
                if not any(p.name.lower() == f.name.lower() for f in found):
                    found.append(p)

        for p in found:
            fname = p.name.lower()
            if any(w in fname for w in ["financialstatement", "laporankeuangan", "fs"]):
                dtype = "fs"
            else:
                dtype = "ar"

            if target_type != "all" and dtype != target_type:
                continue

            try:
                rel_path = str(p.relative_to(PROJECT_ROOT))
            except ValueError:
                rel_path = str(p.resolve())

            filings.append({
                "ticker": ticker,
                "file_name": p.name,
                "file_path": rel_path,
                "doc_type": dtype,
                "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
            })

    return filings


def dispatch_high_priority(
    filings: list[dict[str, Any]],
    redis_url: str = REDIS_URL,
    tracking_path: Path = TRACKING_FILE,
) -> list[dict[str, Any]]:
    """
    Enqueue tasks directly to Redis queue at the FRONT of the queue (RPUSH)
    so active Celery workers pop them immediately (jumping the queue).
    """
    r = redis.Redis.from_url(redis_url)
    enqueued = []

    print(f"\n=======================================================")
    print(f" DISPATCHING {len(filings)} FILINGS WITH HIGH QUEUE PRIORITY")
    print(f"=======================================================\n")

    for idx, item in enumerate(filings, 1):
        # Create Celery signature
        doc_type_code = "fs_annual" if item["doc_type"] == "fs" else "annual_report"
        task_sig = extract_document_task.s(
            file_path=item["file_path"],
            doc_type=doc_type_code,
            auto_ingest=True,
        )
        task_id = task_sig.freeze().id

        # Format Celery Kombu message payload
        body_args = [(), {
            "file_path": item["file_path"],
            "target_sections": None,
            "model": None,
            "doc_type": doc_type_code,
            "auto_ingest": True,
        }, {"callbacks": None, "errbacks": None, "chain": None, "chord": None}]

        body_b64 = base64.b64encode(json.dumps(body_args).encode("utf-8")).decode("utf-8")
        msg = {
            "body": body_b64,
            "content-encoding": "utf-8",
            "content-type": "application/json",
            "headers": {
                "lang": "py",
                "task": "src.tasks.extract_document_task",
                "id": task_id,
                "shadow": None,
                "eta": None,
                "expires": None,
                "group": None,
                "group_index": None,
                "retries": 0,
                "timelimit": [None, None],
                "root_id": task_id,
                "parent_id": None,
                "argsrepr": "()",
                "kwargsrepr": f"{{'file_path': '{item['file_path']}', 'doc_type': '{doc_type_code}', 'auto_ingest': True}}",
                "origin": "gen@host",
            },
            "properties": {
                "correlation_id": task_id,
                "reply_to": "b9687e60-8457-3f8d-a419-7b065f42289c",
                "delivery_mode": 2,
                "delivery_info": {"exchange": "", "routing_key": CELERY_QUEUE_EXTRACTION},
                "priority": 0,
                "body_encoding": "base64",
                "delivery_tag": task_id,
            },
        }

        # RPUSH places message at the TAIL of Redis list, which BRPOP pops FIRST!
        r.rpush(CELERY_QUEUE_EXTRACTION, json.dumps(msg))

        entry = {
            "task_id": task_id,
            "ticker": item["ticker"],
            "doc_type": item["doc_type"],
            "file_name": item["file_name"],
            "file_path": item["file_path"],
            "dispatched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        enqueued.append(entry)
        print(f"[{idx:>3}/{len(filings)}] Prioritized {item['ticker']:<5} {item['doc_type'].upper():<2} {item['file_name']:<35} -> Task {task_id[:8]}...")

    with open(tracking_path, "w") as f:
        json.dump(enqueued, f, indent=2)

    print(f"\nSuccessfully prioritized and enqueued {len(enqueued)} tasks into Redis queue '{CELERY_QUEUE_EXTRACTION}'.")
    print(f"Tracking state written to {tracking_path.name}")
    return enqueued


def print_status_table(tracking_path: Path = TRACKING_FILE) -> None:
    """Print a comprehensive status dashboard with per-issuer fact counts from DuckDB."""
    from celery.result import AsyncResult
    import duckdb

    batch_tasks = []
    if tracking_path.exists():
        with open(tracking_path, "r") as f:
            batch_tasks = json.load(f)

    # 1. Celery task states
    states = {"PENDING": 0, "STARTED": 0, "PROGRESS": 0, "SUCCESS": 0, "FAILURE": 0}
    for t in batch_tasks:
        s = AsyncResult(t["task_id"], app=celery_app).state
        states[s] = states.get(s, 0) + 1

    # 2. Target filing counts per ticker
    targets_by_ticker: dict[str, int] = {}
    for t in batch_tasks:
        tkr = t["ticker"]
        targets_by_ticker[tkr] = targets_by_ticker.get(tkr, 0) + 1

    # 3. Active in-flight tickers from Celery inspect
    active_tickers = set()
    try:
        insp = celery_app.control.inspect(timeout=0.8)
        active_dict = insp.active() or {}
        for worker, w_tasks in active_dict.items():
            for task in w_tasks:
                kwargs = task.get("kwargs", {})
                fp = kwargs.get("file_path", "")
                for tkr in DB_ISSUERS:
                    if f"/{tkr.lower()}/" in fp.lower() or f"-{tkr.upper()}." in fp.upper():
                        active_tickers.add(tkr)
    except Exception:
        pass

    # 4. DuckDB facts & documents per issuer
    duckdb_path = PROJECT_ROOT / "data" / "idxfin.duckdb"
    db_stats = {}
    total_facts = 0
    total_op_kpis = 0
    total_stmts = 0

    if duckdb_path.exists():
        try:
            conn = duckdb.connect(str(duckdb_path), read_only=True)
            df = conn.execute("""
                SELECT 
                    issuer_id,
                    COUNT(*) as total_facts,
                    COUNT(CASE WHEN lineage_extractor = 'ar.mdna.kpis' THEN 1 END) as op_kpi_facts,
                    COUNT(CASE WHEN lineage_extractor = 'fs.statement' THEN 1 END) as stmt_facts,
                    COUNT(DISTINCT doc_id) as doc_count
                FROM idxfin_silver.fact
                GROUP BY issuer_id
            """).df()
            conn.close()
            for _, row in df.iterrows():
                tkr = row["issuer_id"]
                db_stats[tkr] = {
                    "total_facts": int(row["total_facts"]),
                    "op_kpis": int(row["op_kpi_facts"]),
                    "statements": int(row["stmt_facts"]),
                    "docs": int(row["doc_count"]),
                }
                total_facts += int(row["total_facts"])
                total_op_kpis += int(row["op_kpi_facts"])
                total_stmts += int(row["stmt_facts"])
        except Exception:
            pass

    pct = (states["SUCCESS"] / len(batch_tasks) * 100) if batch_tasks else 0.0

    print("=" * 86)
    print(f"                   IDX STATEMENTS EXTRACTION BATCH DASHBOARD [{time.strftime('%H:%M:%S')}]")
    print("=" * 86)
    print(f"Batch Progress : {states['SUCCESS']}/{len(batch_tasks)} ({pct:.1f}%) SUCCESS | {states['PROGRESS'] + states['STARTED']} IN-FLIGHT | {states['PENDING']} PENDING | {states['FAILURE']} FAILED")
    print(f"DuckDB Total   : {total_facts:,} Facts ({total_op_kpis:,} Operational KPIs, {total_stmts:,} Statements)")
    active_str = ", ".join(sorted(active_tickers)) if active_tickers else "Waiting for worker tasks..."
    print(f"Active Tickers : {active_str}")
    print("-" * 86)
    print(f" {'Issuer':<7} | {'Total Facts':>11} | {'Op KPIs':>9} | {'Statements':>10} | {'Filings':>7} | {'Target':>6} | {'Status'}")
    print("-" * 86)

    sum_docs = 0
    sum_targets = 0

    for tkr in DB_ISSUERS:
        st = db_stats.get(tkr, {"total_facts": 0, "op_kpis": 0, "statements": 0, "docs": 0})
        target = targets_by_ticker.get(tkr, st["docs"])
        sum_docs += st["docs"]
        sum_targets += target

        if tkr in active_tickers:
            status = "⚡ In-Flight"
        elif target > 0 and st["docs"] >= target:
            status = "🟢 Complete"
        elif st["docs"] > 0:
            status = "⏳ In Progress"
        else:
            status = "🕒 Queued"

        print(
            f" {tkr:<7} | "
            f"{st['total_facts']:>11,d} | "
            f"{st['op_kpis']:>9,d} | "
            f"{st['statements']:>10,d} | "
            f"{st['docs']:>7d} | "
            f"{target:>6d} | "
            f"{status}"
        )

    print("-" * 86)
    print(
        f" {'TOTAL':<7} | "
        f"{total_facts:>11,d} | "
        f"{total_op_kpis:>9,d} | "
        f"{total_stmts:>10,d} | "
        f"{sum_docs:>7d} | "
        f"{sum_targets:>6d} |"
    )
    print("=" * 86)


def monitor_progress(tracking_path: Path = TRACKING_FILE, poll_interval: int = 10, watch: bool = False) -> None:
    """Monitor live Celery task execution status and DuckDB facts count."""
    if not tracking_path.exists():
        print(f"Tracking file {tracking_path} not found.")
        return

    if not watch:
        print_status_table(tracking_path=tracking_path)
        return

    print("\nStarting live progress monitor (Press Ctrl+C to stop monitor; tasks continue in background)...")
    try:
        while True:
            # Clear terminal screen for clean live update
            print("\033[2J\033[H", end="")
            print_status_table(tracking_path=tracking_path)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nMonitoring stopped. Tasks continue processing in Docker worker background.")


def main():
    parser = argparse.ArgumentParser(description="Dispatch and prioritize extraction for DuckDB issuers.")
    parser.add_argument("--dry-run", action="store_true", help="Preview target filings without dispatching.")
    parser.add_argument("--type", choices=["all", "fs", "ar"], default="all", help="Target filing type (default: all).")
    parser.add_argument("--tickers", type=str, default="", help="Comma-separated tickers (default: all 24 DuckDB issuers).")
    parser.add_argument("--status", action="store_true", help="Display detailed extraction status and facts per issuer.")
    parser.add_argument("--watch", action="store_true", help="Keep refreshing status dashboard every polling interval.")
    parser.add_argument("--poll", type=int, default=10, help="Polling interval in seconds for --watch (default: 10s).")

    args = parser.parse_args()

    if args.status or args.watch:
        monitor_progress(poll_interval=args.poll, watch=args.watch)
        return

    target_tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else DB_ISSUERS
    filings = discover_duckdb_filings(target_tickers=target_tickers, target_type=args.type)

    print(f"\nDiscovered {len(filings)} filings across {len(target_tickers)} target tickers.")

    if args.dry_run:
        print("\n[DRY RUN PREVIEW]")
        print("-" * 75)
        for idx, f in enumerate(filings, 1):
            print(f"[{idx:>3}] {f['ticker']:<6} {f['doc_type'].upper():<3} {f['file_name']}")
        return

    dispatch_high_priority(filings)


if __name__ == "__main__":
    main()
