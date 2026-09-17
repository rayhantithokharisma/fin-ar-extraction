#!/usr/bin/env python3
"""
Sync Market Data Script: Download 4-year daily price history for IDX stocks and commodities into DuckDB.
Usage:
  python scripts/sync_market_data.py --tickers BBCA,ADRO,NCKL --period 4y
  python scripts/sync_market_data.py --all-tickers --commodities
  python scripts/sync_market_data.py --summary
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage.database import DatabaseStore
from src.storage.gold_views import create_gold_views
from src.market_data.yfinance_loader import MarketDataLoader, COMMODITY_CATALOG


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize 4-year daily stock and commodity prices via yfinance into DuckDB.")
    parser.add_argument("--tickers", help="Comma-separated ticker symbols (e.g. BBCA,ADRO,NCKL)")
    parser.add_argument("--all-tickers", action="store_true", help="Synchronize all issuers discovered in DuckDB")
    parser.add_argument("--commodities", action="store_true", help="Synchronize commodities catalog (Gold, Oil, Ammonia proxies)")
    parser.add_argument("--fx", action="store_true", help="Synchronize foreign exchange rate catalog (USD/IDR)")
    parser.add_argument("--period", default="4y", help="Timeframe to fetch (default: 4y)")
    parser.add_argument("--summary", action="store_true", help="Display summary stats of stored market, FX, and valuation tables")
    args = parser.parse_args()

    db = DatabaseStore()
    loader = MarketDataLoader(db=db)

    if args.summary:
        print("\n" + "=" * 65)
        print(" IDXFIN DUCKDB MARKET, COMMODITY & FX SUMMARY")
        print("=" * 65 + "\n")
        try:
            stocks = db.conn.execute("""
                SELECT ticker, MIN(trade_date) as start_date, MAX(trade_date) as end_date,
                       COUNT(*) as bars, ROUND(AVG(close), 2) as avg_price
                FROM idxfin_market.stock_daily
                GROUP BY ticker ORDER BY ticker;
            """).df()
            print(f"--- Tracked Stocks ({len(stocks)} Issuers) ---")
            print(stocks.to_string(index=False) if not stocks.empty else "No stock records found.")

            comms = db.conn.execute("""
                SELECT symbol, commodity_name, unit, currency,
                       MIN(trade_date) as start_date, MAX(trade_date) as end_date,
                       COUNT(*) as bars, ROUND(AVG(close), 2) as avg_close
                FROM idxfin_market.commodity_daily
                GROUP BY symbol, commodity_name, unit, currency ORDER BY symbol;
            """).df()
            print(f"\n--- Tracked Commodities ({len(comms)} Series) ---")
            print(comms.to_string(index=False) if not comms.empty else "No commodity records found.")

            fx = db.conn.execute("""
                SELECT pair, symbol, base_currency, quote_currency,
                       MIN(trade_date) as start_date, MAX(trade_date) as end_date,
                       COUNT(*) as bars, ROUND(AVG(close), 2) as avg_rate,
                       ROUND(MIN(close), 2) as min_rate, ROUND(MAX(close), 2) as max_rate
                FROM idxfin_market.fx_daily
                GROUP BY pair, symbol, base_currency, quote_currency ORDER BY pair;
            """).df()
            print(f"\n--- Tracked Foreign Exchange Rates ({len(fx)} Pairs) ---")
            print(fx.to_string(index=False) if not fx.empty else "No FX records found.")

            vals = db.conn.execute("""
                SELECT issuer_id, fiscal_year, fiscal_period, period_end,
                       period_close_price, latest_close_price, price_change_since_period_pct
                FROM idxfin_gold.market_valuation
                WHERE period_close_price IS NOT NULL
                ORDER BY issuer_id, period_end DESC
                LIMIT 15;
            """).df()
            print(f"\n--- Market Valuation Snapshot (Sample) ---")
            print(vals.to_string(index=False) if not vals.empty else "No valuation records found.")
        finally:
            db.close()
        print("\n" + "=" * 65 + "\n")
        return

    # Ingestion actions
    if args.all_tickers:
        print(f"Syncing daily stock bars for all database issuers [period={args.period}]...")
        saved = loader.sync_all_issuers(period=args.period)
        print(f"[OK] Ingested {saved} stock bars into idxfin_market.stock_daily.")

    elif args.tickers:
        t_list = [t.strip() for t in args.tickers.split(",") if t.strip()]
        print(f"Syncing daily stock bars for {t_list} [period={args.period}]...")
        saved = loader.sync_stocks(t_list, period=args.period)
        print(f"[OK] Ingested {saved} stock bars into idxfin_market.stock_daily.")

    if args.commodities:
        print(f"Syncing benchmark commodities (Gold, Oil, Ammonia proxies) [period={args.period}]...")
        saved = loader.sync_commodities(period=args.period)
        print(f"[OK] Ingested {saved} commodity bars into idxfin_market.commodity_daily.")

    if args.fx:
        print(f"Syncing foreign exchange rates (USD/IDR) [period={args.period}]...")
        saved = loader.sync_fx(period=args.period)
        print(f"[OK] Ingested {saved} FX bars into idxfin_market.fx_daily.")

    if not args.tickers and not args.all_tickers and not args.commodities and not args.fx:
        print(f"Default run: Syncing all issuers, commodities, and FX rates [period={args.period}]...")
        res = loader.sync_all(period=args.period)
        print(f"[OK] Synced {res['stocks_rows']} stock bars, {res['commodities_rows']} commodity bars, and {res['fx_rows']} FX bars.")

    create_gold_views(db)
    db.close()
    print("Market data synchronization completed successfully.")


if __name__ == "__main__":
    main()
