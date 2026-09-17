"""
Unit and integration tests for Market Data and Commodity Ingestion module.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

try:
    import pytest
except ImportError:
    pytest = None

from src.market_data.yfinance_loader import (
    COMMODITY_CATALOG,
    MarketDataFetcher,
    MarketDataLoader,
)
from src.storage.database import DatabaseStore
from src.storage.gold_views import create_gold_views


def test_commodity_catalog():
    """Verify all requested commodities (Gold, Oil, Ammonia proxies) are properly registered."""
    catalog_symbols = {v.symbol for v in COMMODITY_CATALOG.values()}

    # Gold
    assert "GC=F" in catalog_symbols, "Gold COMEX futures missing from catalog"

    # Oil
    assert "CL=F" in catalog_symbols, "WTI Crude Oil missing from catalog"
    assert "BZ=F" in catalog_symbols, "Brent Crude Oil missing from catalog"

    # Ammonia proxies
    assert "ESSA.JK" in catalog_symbols, "PT ESSA Industries Indonesia (domestic ammonia) missing"
    assert "CF" in catalog_symbols, "CF Industries (global ammonia proxy) missing"
    assert "NG=F" in catalog_symbols, "Henry Hub Natural Gas (ammonia feedstock) missing"

    for key, item in COMMODITY_CATALOG.items():
        assert item.name, f"Missing name for {key}"
        assert item.unit, f"Missing unit for {key}"
        assert item.category in ("PRECIOUS_METALS", "ENERGY", "CHEMICALS_FERTILIZERS")
        assert item.currency in ("USD", "IDR")


def test_normalize_ticker():
    """Verify ticker normalization for IDX and futures symbols."""
    fetcher = MarketDataFetcher()
    clean, symbol = fetcher._normalize_ticker("bbca")
    assert clean == "BBCA"
    assert symbol == "BBCA.JK"

    clean, symbol = fetcher._normalize_ticker("ADRO.JK")
    assert clean == "ADRO"
    assert symbol == "ADRO.JK"

    clean, symbol = fetcher._normalize_ticker("GC=F")
    assert clean == "GC=F"
    assert symbol == "GC=F"


def test_database_market_schema_and_upsert(tmp_path: Path):
    """Test DuckDB schema creation and idempotency of upserts."""
    test_db_path = tmp_path / "test_market.duckdb"
    db = DatabaseStore(db_path=test_db_path)

    # 1. Verify schemas
    schemas = [s[0] for s in db.conn.execute("SELECT schema_name FROM information_schema.schemata;").fetchall()]
    assert "idxfin_market" in schemas
    assert "idxfin_gold" in schemas

    # 2. Test save_stock_prices
    stock_df = pd.DataFrame([
        {
            "ticker": "BBCA",
            "trade_date": datetime.date(2026, 9, 16),
            "open": 6350.0,
            "high": 6425.0,
            "low": 6325.0,
            "close": 6400.0,
            "adj_close": 6400.0,
            "volume": 50000000,
            "currency": "IDR",
            "source": "yfinance",
        },
        {
            "ticker": "BBCA",
            "trade_date": datetime.date(2026, 9, 17),
            "open": 6400.0,
            "high": 6450.0,
            "low": 6375.0,
            "close": 6425.0,
            "adj_close": 6425.0,
            "volume": 42000000,
            "currency": "IDR",
            "source": "yfinance",
        },
    ])
    saved = db.save_stock_prices(stock_df)
    assert saved == 2

    # Idempotent upsert
    saved_again = db.save_stock_prices(stock_df)
    assert saved_again == 2
    count = db.conn.execute("SELECT COUNT(*) FROM idxfin_market.stock_daily;").fetchone()[0]
    assert count == 2

    # 3. Test save_commodity_prices
    comm_df = pd.DataFrame([
        {
            "symbol": "GC=F",
            "commodity_name": "Gold Futures (COMEX)",
            "commodity_category": "PRECIOUS_METALS",
            "trade_date": datetime.date(2026, 9, 16),
            "open": 4350.0,
            "high": 4380.0,
            "low": 4340.0,
            "close": 4370.0,
            "volume": 120000,
            "unit": "USD/troy oz",
            "currency": "USD",
            "source": "yfinance",
        }
    ])
    comm_saved = db.save_commodity_prices(comm_df)
    assert comm_saved == 1
    comm_count = db.conn.execute("SELECT COUNT(*) FROM idxfin_market.commodity_daily;").fetchone()[0]
    assert comm_count == 1

    # 4. Materialize gold views and query
    create_gold_views(db)
    monthly_trend = db.conn.execute("SELECT * FROM idxfin_gold.commodity_trend_monthly;").fetchdf()
    assert len(monthly_trend) == 1
    assert monthly_trend.iloc[0]["symbol"] == "GC=F"
    assert monthly_trend.iloc[0]["avg_close"] == 4370.0

    db.close()


def test_live_yfinance_fetch_short_window():
    """Live smoke test fetching 5 days of data from yfinance for IDX stock and Gold."""
    fetcher = MarketDataFetcher()

    # IDX Stock
    bbca_df = fetcher.fetch_stock("BBCA", period="5d")
    assert not bbca_df.empty, "Failed to fetch 5d BBCA from yfinance"
    assert "trade_date" in bbca_df.columns
    assert "close" in bbca_df.columns
    assert (bbca_df["close"] > 0).all()

    # Commodity
    gold_df = fetcher.fetch_commodity("GC=F", "Gold Futures", "PRECIOUS_METALS", "USD/troy oz", period="5d")
    assert not gold_df.empty, "Failed to fetch 5d GC=F from yfinance"
    assert "close" in gold_df.columns
    assert gold_df.iloc[0]["symbol"] == "GC=F"


if __name__ == "__main__":
    import tempfile
    print("Running market data test suite...")
    test_commodity_catalog()
    print("  [PASS] test_commodity_catalog")
    test_normalize_ticker()
    print("  [PASS] test_normalize_ticker")
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_database_market_schema_and_upsert(Path(tmp_dir))
    print("  [PASS] test_database_market_schema_and_upsert")
    test_live_yfinance_fetch_short_window()
    print("  [PASS] test_live_yfinance_fetch_short_window")
    print("All market data tests PASSED successfully!")
