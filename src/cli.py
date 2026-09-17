"""
Command-Line Interface for IDX Extraction Pipeline.
Usage:
  python -m src.cli inspect <pdf_path>
  python -m src.cli extract <pdf_path> [--sections section1,section2] [--model model_name] [--async]
  python -m src.cli status <task_id>
  python -m src.cli gold [--ticker TICKER]
  python -m src.cli api [--host HOST] [--port PORT]
  python -m src.cli worker [--queue QUEUE] [--concurrency CONCURRENCY]
  python -m src.cli reindex-staging
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd

from src.config import CELERY_QUEUE_EXTRACTION, CELERY_WORKER_CONCURRENCY
from src.parser.pdf_loader import PDFDocument
from src.parser.sectionizer import Sectionizer
from src.pipeline import ExtractionPipeline
from src.storage.database import DatabaseStore
from src.storage.gold_views import create_gold_views
from src.storage.staging import ParquetStagingStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def cmd_inspect(args: argparse.Namespace) -> None:
    path = Path(args.pdf_path)
    if not path.exists():
        print(f"Error: File not found: {path}")
        sys.exit(1)

    print(f"\n=======================================================")
    print(f" INSPECTING: {path.name}")
    print(f"=======================================================\n")
    doc = PDFDocument(path)
    print(f"Ticker:               {doc.ticker}")
    print(f"Legal Name:           {doc.legal_name}")
    print(f"Doc Type:             {doc.doc_type}")
    print(f"Period:               {doc.fiscal_period} {doc.fiscal_year} (ended {doc.period_end})")
    print(f"Pages:                {doc.page_count}")
    print(f"Currency:             {doc.currency}")
    print(f"Scale:                {doc.scale} ({doc.scale_raw})")
    print(f"Number Locale:        {doc.number_locale}")
    print(f"Has Text Layer:       {doc.has_text_layer}")
    print(f"SHA-256 Hash:         {doc.doc_id[:16]}...")

    sectionizer = Sectionizer(doc)
    spans = sectionizer.sectionize()
    print(f"\nDetected Section Spans ({len(spans)}):")
    print(f"{'-'*75}")
    print(f"{'Section ID':<25} {'Type':<20} {'Pages':<12} {'Title'}")
    print(f"{'-'*75}")
    for s in spans:
        print(f"{s.section_id:<25} {s.section_type:<20} {s.start_page:>3} - {s.end_page:<5} {s.title[:35]}")
    print(f"{'-'*75}\n")
    doc.close()


def cmd_extract(args: argparse.Namespace) -> None:
    path = Path(args.pdf_path)
    if not path.exists():
        print(f"Error: File not found: {path}")
        sys.exit(1)

    sections = [s.strip() for s in args.sections.split(",")] if args.sections else None

    # Asynchronous queue mode via Celery and Redis
    if getattr(args, "async_mode", False):
        try:
            import redis
            from src.config import REDIS_URL
            r = redis.Redis.from_url(REDIS_URL, socket_timeout=1.5)
            r.ping()
        except Exception:
            print(f"\n[ERROR] Could not connect to Redis broker at {REDIS_URL}.")
            print("Please ensure Redis is running before enqueuing asynchronous tasks, e.g.:")
            print("  docker run -d -p 6379:6379 --name idxfin-redis redis:7-alpine")
            print("  # or: docker-compose up -d redis\n")
            sys.exit(1)

        try:
            from src.tasks import extract_document_task
            task = extract_document_task.apply_async(
                kwargs={
                    "file_path": str(path.resolve()),
                    "target_sections": sections,
                    "model": args.model,
                    "auto_ingest": True,
                },
                queue=CELERY_QUEUE_EXTRACTION,
            )
            print(f"\n=======================================================")
            print(f" TASK ENQUEUED TO CELERY (REDIS)")
            print(f"=======================================================\n")
            print(f"Task ID:          {task.id}")
            print(f"File:             {path.name}")
            print(f"Status:           QUEUED (Waiting for free worker in pool of {CELERY_WORKER_CONCURRENCY})")
            print(f"Queue:            {CELERY_QUEUE_EXTRACTION}")
            print(f"\nTo monitor progress, run:")
            print(f"  python3 -m src.cli status {task.id}\n")
            return
        except Exception as e:
            print(f"Error enqueuing Celery task: {e}")
            print("Make sure Redis is running (e.g. docker run -p 6379:6379 redis:7-alpine)")
            sys.exit(1)

    # Synchronous direct execution mode
    print(f"\nStarting synchronous extraction on {path.name}...")
    pipeline = ExtractionPipeline()
    result = pipeline.run(file_path=path, target_sections=sections, model=args.model)

    print(f"\n=======================================================")
    print(f" EXTRACTION RUN SUMMARY")
    print(f"=======================================================\n")
    for k, v in result["summary"].items():
        print(f"  {k:<25}: {v}")

    print(f"\nValidation Rule Results:")
    print(f"{'-'*75}")
    for r in result["validation_results"]:
        status = "PASSED" if r.passed else "FAILED"
        print(f"  [{status}] ({r.severity}) {r.rule_id}: {r.message}")
    print(f"{'-'*75}\n")


def cmd_status(args: argparse.Namespace) -> None:
    """Check task execution status in Celery."""
    from celery.result import AsyncResult
    from src.celery_app import app as celery_app

    res = AsyncResult(args.task_id, app=celery_app)
    state = res.state

    print(f"\n=======================================================")
    print(f" TASK STATUS: {args.task_id}")
    print(f"=======================================================\n")
    print(f"State:            {state}")

    if state == "PROGRESS":
        print(f"Progress Info:    {json.dumps(res.info, indent=2)}")
    elif state == "SUCCESS":
        result = res.result
        print(f"Run ID:           {result.get('run_id')}")
        print(f"Document:         {result.get('file_name')}")
        print(f"Staged Parquet:   {result.get('staged_dir')}")
        print(f"\nSummary:")
        for k, v in result.get("summary", {}).items():
            print(f"  {k:<25}: {v}")
    elif state == "FAILURE":
        print(f"Error:            {res.result}")
    elif state == "STARTED":
        print("Task is currently being processed by an active worker slot...")
    elif state == "PENDING":
        print("Task is queued in Redis (waiting for an available worker slot)...")
    print()


def cmd_gold(args: argparse.Namespace) -> None:
    try:
        db = DatabaseStore(read_only=False)
        create_gold_views(db)
    except Exception:
        db = DatabaseStore(read_only=True)
    query = "SELECT * FROM idxfin_gold.financial_summary"
    if args.ticker:
        query += f" WHERE issuer_id = '{args.ticker}'"
    query += " ORDER BY issuer_id, period_end DESC"
    df = db.conn.execute(query).df()
    print("\n=======================================================")
    print(" GOLD LAYER: FINANCIAL SUMMARY")
    print("=======================================================\n")
    if df.empty:
        print("No records found in Gold financial_summary view.")
    else:
        formatted_df = df.copy()
        numeric_cols = [c for c in df.columns if c not in ("issuer_id", "period_end", "fiscal_year", "fiscal_period", "currency")]
        for col in numeric_cols:
            formatted_df[col] = formatted_df[col].apply(
                lambda x: f"{x:,.0f}" if pd.notnull(x) else "unavailable"
            )
        print(formatted_df.to_string(index=False))

    prov_query = "SELECT * FROM idxfin_gold.financial_summary_provenance"
    if args.ticker:
        prov_query += f" WHERE issuer_id = '{args.ticker}'"
    prov_query += " ORDER BY issuer_id, period_end DESC"
    prov_df = db.conn.execute(prov_query).df()
    if not prov_df.empty:
        print("\n-------------------------------------------------------")
        print(" METRIC PROVENANCE (Source Citations)")
        print("-------------------------------------------------------")
        prov_formatted = prov_df.fillna("-")
        print(prov_formatted.to_string(index=False))
    print()


def cmd_market(args: argparse.Namespace) -> None:
    """Fetch and sync 4-year stock prices and commodity benchmarks via yfinance."""
    from src.market_data.yfinance_loader import MarketDataLoader, COMMODITY_CATALOG

    loader = MarketDataLoader()
    period = args.period or "4y"

    if args.summary:
        print("\n=======================================================")
        print(" IDXFIN MARKET & COMMODITY SUMMARY")
        print("=======================================================\n")
        db = DatabaseStore()
        try:
            stocks_df = db.conn.execute("""
                SELECT ticker, MIN(trade_date) as start_date, MAX(trade_date) as end_date,
                       COUNT(*) as bars, ROUND(AVG(close), 2) as avg_price,
                       ROUND(MAX(close), 2) as max_price, ROUND(MIN(close), 2) as min_price
                FROM idxfin_market.stock_daily
                GROUP BY ticker ORDER BY ticker;
            """).df()
            print(f"--- Tracked Stocks ({len(stocks_df)} issuers) ---")
            if not stocks_df.empty:
                print(stocks_df.to_string(index=False))

            comm_df = db.conn.execute("""
                SELECT symbol, commodity_name, unit, currency,
                       MIN(trade_date) as start_date, MAX(trade_date) as end_date,
                       COUNT(*) as bars, ROUND(AVG(close), 2) as avg_close
                FROM idxfin_market.commodity_daily
                GROUP BY symbol, commodity_name, unit, currency ORDER BY symbol;
            """).df()
            print(f"\n--- Tracked Commodities & Proxies ({len(comm_df)} series) ---")
            if not comm_df.empty:
                print(comm_df.to_string(index=False))

            fx_df = db.conn.execute("""
                SELECT pair, symbol, base_currency, quote_currency,
                       MIN(trade_date) as start_date, MAX(trade_date) as end_date,
                       COUNT(*) as bars, ROUND(AVG(close), 2) as avg_rate,
                       ROUND(MIN(close), 2) as min_rate, ROUND(MAX(close), 2) as max_rate
                FROM idxfin_market.fx_daily
                GROUP BY pair, symbol, base_currency, quote_currency ORDER BY pair;
            """).df()
            print(f"\n--- Tracked Foreign Exchange Rates ({len(fx_df)} pairs) ---")
            if not fx_df.empty:
                print(fx_df.to_string(index=False))

            val_df = db.conn.execute("""
                SELECT issuer_id, fiscal_year, fiscal_period, period_end,
                       period_close_price, latest_close_price, price_change_since_period_pct
                FROM idxfin_gold.market_valuation
                WHERE period_close_price IS NOT NULL
                ORDER BY issuer_id, period_end DESC
                LIMIT 15;
            """).df()
            print(f"\n--- Gold Market Valuation Snapshot (Sample) ---")
            if not val_df.empty:
                print(val_df.to_string(index=False))
        finally:
            db.close()
        print()
        return

    # Ingestion flow
    if args.all_tickers:
        print(f"Syncing daily stock history for all issuers in database [period={period}]...")
        saved = loader.sync_all_issuers(period=period)
        print(f"Saved {saved} stock bars into idxfin_market.stock_daily.")

    elif args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        print(f"Syncing daily stock history for {tickers} [period={period}]...")
        saved = loader.sync_stocks(tickers, period=period)
        print(f"Saved {saved} stock bars into idxfin_market.stock_daily.")

    if args.commodities:
        print(f"Syncing daily commodity benchmarks (Gold, Oil, Ammonia proxies) [period={period}]...")
        saved = loader.sync_commodities(period=period)
        print(f"Saved {saved} commodity bars into idxfin_market.commodity_daily.")

    if getattr(args, "fx", False):
        print(f"Syncing daily foreign exchange rates (USD/IDR) [period={period}]...")
        saved = loader.sync_fx(period=period)
        print(f"Saved {saved} FX bars into idxfin_market.fx_daily.")

    if not args.tickers and not args.all_tickers and not args.commodities and not getattr(args, "fx", False):
        print("Syncing all database issuers, default commodities, and FX rates [period=4y]...")
        res = loader.sync_all(period=period)
        print(f"Synchronization complete: {res['stocks_rows']} stock bars, {res['commodities_rows']} commodity bars, {res['fx_rows']} FX bars.")

    # Re-materialize gold views
    create_gold_views(loader.db)


def cmd_api(args: argparse.Namespace) -> None:
    """Launch the FastAPI Gateway."""
    import uvicorn
    print(f"Starting FastAPI Gateway on http://{args.host}:{args.port}...")
    uvicorn.run("src.api.app:app", host=args.host, port=args.port, reload=args.reload)


def cmd_worker(args: argparse.Namespace) -> None:
    """Launch Celery worker."""
    concurrency = args.concurrency or CELERY_WORKER_CONCURRENCY
    queue = args.queue or CELERY_QUEUE_EXTRACTION
    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "src.celery_app",
        "worker",
        "-Q",
        queue,
        "-c",
        str(concurrency),
        "--loglevel=info",
    ]
    print(f"Starting Celery worker: queue={queue}, concurrency={concurrency}...")
    subprocess.run(cmd)


def cmd_reindex_staging(args: argparse.Namespace) -> None:
    """Reindex all pending or completed staging Parquet files into DuckDB."""
    staging = ParquetStagingStore()
    pending = staging.list_pending_runs()
    completed = [p.name for p in staging.completed_dir.iterdir() if p.is_dir()] if staging.completed_dir.exists() else []

    all_runs = list(set(pending + completed))
    print(f"Found {len(all_runs)} runs in staging lake to index into DuckDB...")

    db = DatabaseStore()
    for run_id in all_runs:
        try:
            stats = db.ingest_from_staging(run_id, staging)
            print(f"  [OK] Ingested run {run_id}: {stats['tables_ingested']}")
        except Exception as e:
            print(f"  [ERROR] Ingesting run {run_id} failed: {e}")
    db.close()
    print("Reindexing complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="IDX Financial Statement and Annual Report Extraction CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect PDF metadata and detected section boundaries")
    inspect_parser.add_argument("pdf_path", help="Path to PDF file")
    inspect_parser.set_defaults(func=cmd_inspect)

    # Extract
    extract_parser = subparsers.add_parser("extract", help="Run extraction pipeline")
    extract_parser.add_argument("pdf_path", help="Path to PDF file")
    extract_parser.add_argument("--sections", help="Comma-separated target section types or IDs", default=None)
    extract_parser.add_argument("--model", help="OpenRouter model to use", default=None)
    extract_parser.add_argument("--async", dest="async_mode", action="store_true", help="Enqueue task asynchronously to Celery/Redis")
    extract_parser.set_defaults(func=cmd_extract)

    # Status
    status_parser = subparsers.add_parser("status", help="Check status of an asynchronous Celery task")
    status_parser.add_argument("task_id", help="Celery Task ID")
    status_parser.set_defaults(func=cmd_status)

    # Gold
    gold_parser = subparsers.add_parser("gold", help="Query Gold financial summary views")
    gold_parser.add_argument("--ticker", help="Filter by ticker symbol", default=None)
    gold_parser.set_defaults(func=cmd_gold)

    # API
    api_parser = subparsers.add_parser("api", help="Launch FastAPI server")
    api_parser.add_argument("--host", default="0.0.0.0", help="Host address (default: 0.0.0.0)")
    api_parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    api_parser.add_argument("--reload", action="store_true", help="Enable hot reload")
    api_parser.set_defaults(func=cmd_api)

    # Worker
    worker_parser = subparsers.add_parser("worker", help="Launch Celery worker")
    worker_parser.add_argument("-Q", "--queue", default=None, help="Target queue (default: extraction)")
    worker_parser.add_argument("-c", "--concurrency", type=int, default=None, help="Worker concurrency (default: 6)")
    worker_parser.set_defaults(func=cmd_worker)

    # Reindex Staging
    reindex_parser = subparsers.add_parser("reindex-staging", help="Re-ingest all Parquet staging files into DuckDB")
    reindex_parser.set_defaults(func=cmd_reindex_staging)

    # Market
    market_parser = subparsers.add_parser("market", help="Sync stock and commodity prices via yfinance into DuckDB")
    market_parser.add_argument("--tickers", help="Comma-separated ticker symbols (e.g. BBCA,ADRO,NCKL)", default=None)
    market_parser.add_argument("--all-tickers", action="store_true", help="Sync all issuers found in DuckDB")
    market_parser.add_argument("--commodities", action="store_true", help="Sync benchmark commodities (Gold, Oil, Ammonia proxies)")
    market_parser.add_argument("--fx", action="store_true", help="Sync foreign exchange rates (USD/IDR)")
    market_parser.add_argument("--period", default="4y", help="Historical period (default: 4y)")
    market_parser.add_argument("--summary", action="store_true", help="Print summary of market, FX, and valuation tables")
    market_parser.set_defaults(func=cmd_market)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
