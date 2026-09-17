"""
Market Data Fetcher and Loader using yfinance for IDX Stocks and Commodity Benchmarks.
Provides 4-year daily price history ingestion into DuckDB idxfin_market schema.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from src.storage.database import DatabaseStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommodityDefinition:
    symbol: str
    name: str
    category: str
    unit: str
    currency: str = "USD"
    description: str = ""


# Default catalog of commodities and proxies
COMMODITY_CATALOG: Dict[str, CommodityDefinition] = {
    "gold": CommodityDefinition(
        symbol="GC=F",
        name="Gold Futures (COMEX)",
        category="PRECIOUS_METALS",
        unit="USD/troy oz",
        currency="USD",
        description="Benchmark gold futures contract traded on the COMEX division of CME.",
    ),
    "oil_wti": CommodityDefinition(
        symbol="CL=F",
        name="Crude Oil WTI Futures (NYMEX)",
        category="ENERGY",
        unit="USD/bbl",
        currency="USD",
        description="West Texas Intermediate light sweet crude oil futures traded on NYMEX.",
    ),
    "oil_brent": CommodityDefinition(
        symbol="BZ=F",
        name="Crude Oil Brent Futures (ICE)",
        category="ENERGY",
        unit="USD/bbl",
        currency="USD",
        description="Brent blend crude oil futures benchmark traded on the ICE exchange.",
    ),
    "ammonia_domestic": CommodityDefinition(
        symbol="ESSA.JK",
        name="PT ESSA Industries Indonesia Tbk (Domestic Ammonia Producer)",
        category="CHEMICALS_FERTILIZERS",
        unit="IDR/share",
        currency="IDR",
        description="Indonesia's leading private merchant ammonia and LPG producer, proxy for domestic ammonia margins.",
    ),
    "ammonia_proxy_cf": CommodityDefinition(
        symbol="CF",
        name="CF Industries Holdings (Ammonia Producer Proxy)",
        category="CHEMICALS_FERTILIZERS",
        unit="USD/share",
        currency="USD",
        description="World's largest clean ammonia and nitrogen fertilizer manufacturer, global ammonia equity proxy.",
    ),
    "ammonia_proxy_uan": CommodityDefinition(
        symbol="UAN",
        name="CVR Partners LP (Ammonia Fertilizer Proxy)",
        category="CHEMICALS_FERTILIZERS",
        unit="USD/share",
        currency="USD",
        description="Pure-play North American nitrogen fertilizer and ammonia producer.",
    ),
    "natural_gas_feed": CommodityDefinition(
        symbol="NG=F",
        name="Natural Gas Henry Hub (Ammonia Feedstock Benchmark)",
        category="ENERGY",
        unit="USD/MMBtu",
        currency="USD",
        description="Natural gas futures benchmark; feedgas accounts for 70-85% of Haber-Bosch ammonia cash production cost.",
    ),
    "ammonia_spot": CommodityDefinition(
        symbol="NH3=F",
        name="Anhydrous Ammonia Benchmark Spot (Global CFR/FOB)",
        category="CHEMICALS_FERTILIZERS",
        unit="USD/ton",
        currency="USD",
        description="Physical spot benchmark price for anhydrous ammonia in USD per metric ton.",
    ),
}


@dataclass(frozen=True)
class FXDefinition:
    pair: str
    symbol: str
    name: str
    base_currency: str = "USD"
    quote_currency: str = "IDR"
    description: str = ""


# Default catalog of foreign exchange rate pairs
FX_CATALOG: Dict[str, FXDefinition] = {
    "USD/IDR": FXDefinition(
        pair="USD/IDR",
        symbol="IDR=X",
        name="US Dollar to Indonesian Rupiah Exchange Rate",
        base_currency="USD",
        quote_currency="IDR",
        description="Daily exchange rate benchmark for USD to IDR from Yahoo Finance.",
    ),
}


class MarketDataFetcher:
    """Fetches daily OHLCV and volume for IDX stocks and commodities from yfinance."""

    @staticmethod
    def _normalize_ticker(ticker: str) -> tuple[str, str]:
        """Normalize ticker to (clean_ticker, yf_symbol).

        For Indonesian tickers (e.g. 'BBCA'), returns ('BBCA', 'BBCA.JK').
        If already containing a suffix like '.JK', returns ('BBCA', 'BBCA.JK').
        """
        raw = ticker.strip().upper()
        if raw.endswith(".JK"):
            return raw[:-3], raw
        if "=" in raw or "^" in raw:
            return raw, raw
        return raw, f"{raw}.JK"

    def fetch_stock(self, ticker: str, period: str = "4y") -> pd.DataFrame:
        """Fetch daily historical data for a single stock ticker over period (default: 4y)."""
        clean_ticker, yf_symbol = self._normalize_ticker(ticker)
        logger.info(f"Fetching stock data for {clean_ticker} ({yf_symbol}) [period={period}]...")

        try:
            t = yf.Ticker(yf_symbol)
            df = t.history(period=period, interval="1d", auto_adjust=False)
            if df is None or df.empty:
                logger.warning(f"No price data returned for {yf_symbol}")
                return pd.DataFrame()

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]

            df = df.reset_index()

            date_col = "Date" if "Date" in df.columns else df.columns[0]
            df["trade_date"] = pd.to_datetime(df[date_col]).dt.date

            col_map = {
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Adj Close": "adj_close",
                "Volume": "volume",
            }
            for orig, target in col_map.items():
                if orig in df.columns:
                    df[target] = pd.to_numeric(df[orig], errors="coerce")
                elif target not in df.columns:
                    df[target] = df["Close"] if orig == "Adj Close" else np.nan

            df["ticker"] = clean_ticker
            df["currency"] = "IDR" if yf_symbol.endswith(".JK") else "USD"
            df["source"] = "yfinance"

            keep_cols = [
                "ticker",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "adj_close",
                "volume",
                "currency",
                "source",
            ]
            result = df[keep_cols].dropna(subset=["trade_date", "close"])
            result = result[result["close"] > 0]
            logger.info(f"Retrieved {len(result)} daily bars for {clean_ticker} ({result['trade_date'].min()} to {result['trade_date'].max()})")
            return result
        except Exception as e:
            logger.error(f"Error fetching stock data for {ticker}: {e}")
            return pd.DataFrame()

    def fetch_stocks_batch(
        self, tickers: List[str], period: str = "4y", delay_s: float = 0.1
    ) -> pd.DataFrame:
        """Fetch daily history for multiple stock tickers sequentially."""
        dfs = []
        for idx, ticker in enumerate(tickers, start=1):
            df = self.fetch_stock(ticker, period=period)
            if not df.empty:
                dfs.append(df)
            if delay_s > 0 and idx < len(tickers):
                time.sleep(delay_s)

        if not dfs:
            return pd.DataFrame()
        return pd.concat(dfs, ignore_index=True)

    def fetch_commodity(
        self,
        symbol: str,
        name: str,
        category: str,
        unit: str,
        currency: str = "USD",
        period: str = "4y",
    ) -> pd.DataFrame:
        """Fetch daily historical commodity / benchmark data over period (default: 4y)."""
        if symbol == "NH3=F":
            return self.load_ammonia_benchmark()

        logger.info(f"Fetching commodity data for {symbol} ({name}) [period={period}]...")
        try:
            t = yf.Ticker(symbol)
            df = t.history(period=period, interval="1d", auto_adjust=False)
            if df is None or df.empty:
                logger.warning(f"No price data returned for commodity {symbol}")
                return pd.DataFrame()

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]

            df = df.reset_index()
            date_col = "Date" if "Date" in df.columns else df.columns[0]
            df["trade_date"] = pd.to_datetime(df[date_col]).dt.date

            col_map = {
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
            for orig, target in col_map.items():
                if orig in df.columns:
                    df[target] = pd.to_numeric(df[orig], errors="coerce")
                elif target not in df.columns:
                    df[target] = np.nan

            df["symbol"] = symbol
            df["commodity_name"] = name
            df["commodity_category"] = category
            df["unit"] = unit
            df["currency"] = currency
            df["source"] = "yfinance"

            keep_cols = [
                "symbol",
                "commodity_name",
                "commodity_category",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "unit",
                "currency",
                "source",
            ]
            result = df[keep_cols].dropna(subset=["trade_date", "close"])
            result = result[result["close"] > 0]
            logger.info(f"Retrieved {len(result)} commodity bars for {symbol} ({result['trade_date'].min()} to {result['trade_date'].max()})")
            return result
        except Exception as e:
            logger.error(f"Error fetching commodity data for {symbol}: {e}")
            return pd.DataFrame()

    def fetch_all_commodities(
        self,
        catalog: Optional[Dict[str, CommodityDefinition]] = None,
        period: str = "4y",
        delay_s: float = 0.1,
    ) -> pd.DataFrame:
        """Fetch all configured commodities in the catalog."""
        cat = catalog or COMMODITY_CATALOG
        dfs = []
        for idx, (key, item) in enumerate(cat.items(), start=1):
            df = self.fetch_commodity(
                symbol=item.symbol,
                name=item.name,
                category=item.category,
                unit=item.unit,
                currency=item.currency,
                period=period,
            )
            if not df.empty:
                dfs.append(df)
            if delay_s > 0 and idx < len(cat):
                time.sleep(delay_s)

        if not dfs:
            return pd.DataFrame()
        return pd.concat(dfs, ignore_index=True)

    def load_ammonia_benchmark(
        self,
        csv_path: Optional[str | Path] = None,
        forward_fill: bool = True,
    ) -> pd.DataFrame:
        """Load physical ammonia benchmark price series from CSV and format for commodity_daily."""
        path = Path(csv_path) if csv_path else Path(__file__).resolve().parent.parent.parent / "data" / "benchmarks" / "ammonia_daily_benchmark.csv"
        if not path.exists():
            logger.warning(f"Ammonia benchmark CSV not found at {path}")
            return pd.DataFrame()

        try:
            df = pd.read_csv(path)
            if df.empty or "Date" not in df.columns or "Price_USD_per_T" not in df.columns:
                return pd.DataFrame()

            df["Date"] = pd.to_datetime(df["Date"])
            df = df.sort_values("Date").dropna(subset=["Price_USD_per_T"])

            if forward_fill:
                # Forward-fill across calendar days so daily joins (e.g. fx_daily, balance sheet dates) succeed
                min_date = df["Date"].min()
                max_date = df["Date"].max()
                all_dates = pd.date_range(min_date, max_date, freq="D", name="Date")
                df = df.set_index("Date").reindex(all_dates)
                df["Price_USD_per_T"] = df["Price_USD_per_T"].ffill()
                df = df.reset_index()

            df["trade_date"] = df["Date"].dt.date
            df["symbol"] = "NH3=F"
            df["commodity_name"] = "Anhydrous Ammonia Benchmark Spot (Global CFR/FOB)"
            df["commodity_category"] = "CHEMICALS_FERTILIZERS"
            df["unit"] = "USD/ton"
            df["currency"] = "USD"
            df["open"] = df["Price_USD_per_T"].astype(float)
            df["high"] = df["Price_USD_per_T"].astype(float)
            df["low"] = df["Price_USD_per_T"].astype(float)
            df["close"] = df["Price_USD_per_T"].astype(float)
            df["volume"] = 0
            df["source"] = "benchmark_feed"

            keep_cols = [
                "symbol",
                "commodity_name",
                "commodity_category",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "unit",
                "currency",
                "source",
            ]
            result = df[keep_cols].dropna(subset=["trade_date", "close"])
            logger.info(f"Loaded {len(result)} daily ammonia benchmark bars (NH3=F: {result['trade_date'].min()} to {result['trade_date'].max()})")
            return result
        except Exception as e:
            logger.error(f"Error loading ammonia benchmark CSV: {e}")
            return pd.DataFrame()

    def fetch_fx(
        self,
        pair: str = "USD/IDR",
        symbol: str = "IDR=X",
        base_currency: str = "USD",
        quote_currency: str = "IDR",
        period: str = "4y",
    ) -> pd.DataFrame:
        """Fetch daily historical foreign exchange (FX) rate data over period (default: 4y)."""
        logger.info(f"Fetching FX data for {pair} ({symbol}) [period={period}]...")
        try:
            t = yf.Ticker(symbol)
            df = t.history(period=period, interval="1d", auto_adjust=False)
            if df is None or df.empty:
                logger.warning(f"No FX data returned for {symbol}")
                return pd.DataFrame()

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]

            df = df.reset_index()
            date_col = "Date" if "Date" in df.columns else df.columns[0]
            df["trade_date"] = pd.to_datetime(df[date_col]).dt.date

            col_map = {
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
            }
            for orig, target in col_map.items():
                if orig in df.columns:
                    df[target] = pd.to_numeric(df[orig], errors="coerce")
                elif target not in df.columns:
                    df[target] = np.nan

            df["pair"] = pair
            df["symbol"] = symbol
            df["rate"] = df["close"]
            df["base_currency"] = base_currency
            df["quote_currency"] = quote_currency
            df["source"] = "yfinance"

            keep_cols = [
                "pair",
                "symbol",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "rate",
                "base_currency",
                "quote_currency",
                "source",
            ]
            result = df[keep_cols].dropna(subset=["trade_date", "close"])
            result = result[result["close"] > 0]
            logger.info(f"Retrieved {len(result)} FX bars for {pair} ({result['trade_date'].min()} to {result['trade_date'].max()})")
            return result
        except Exception as e:
            logger.error(f"Error fetching FX data for {pair} ({symbol}): {e}")
            return pd.DataFrame()

    def fetch_all_fx(
        self,
        catalog: Optional[Dict[str, FXDefinition]] = None,
        period: str = "4y",
        delay_s: float = 0.1,
    ) -> pd.DataFrame:
        """Fetch all configured FX pairs in the catalog."""
        cat = catalog or FX_CATALOG
        dfs = []
        for idx, (key, item) in enumerate(cat.items(), start=1):
            df = self.fetch_fx(
                pair=item.pair,
                symbol=item.symbol,
                base_currency=item.base_currency,
                quote_currency=item.quote_currency,
                period=period,
            )
            if not df.empty:
                dfs.append(df)
            if delay_s > 0 and idx < len(cat):
                time.sleep(delay_s)

        if not dfs:
            return pd.DataFrame()
        return pd.concat(dfs, ignore_index=True)


class MarketDataLoader:
    """Orchestrates syncing market stock and commodity prices into DuckDB."""

    def __init__(self, db: Optional[DatabaseStore] = None, fetcher: Optional[MarketDataFetcher] = None):
        self.db = db or DatabaseStore()
        self.fetcher = fetcher or MarketDataFetcher()

    def sync_stocks(self, tickers: List[str], period: str = "4y") -> int:
        """Fetch and upsert stock price data for given tickers."""
        if not tickers:
            return 0
        df = self.fetcher.fetch_stocks_batch(tickers, period=period)
        if df.empty:
            logger.warning("No stock price records to save.")
            return 0
        return self.db.save_stock_prices(df)

    def sync_all_issuers(self, period: str = "4y") -> int:
        """Sync stock prices for all corporate issuers discovered in the database."""
        tickers = self.db.get_market_tickers()
        logger.info(f"Found {len(tickers)} distinct issuers in database: {tickers}")
        return self.sync_stocks(tickers, period=period)

    def sync_commodities(
        self,
        keys: Optional[List[str]] = None,
        period: str = "4y",
    ) -> int:
        """Fetch and upsert commodity price data for catalog commodities."""
        cat = COMMODITY_CATALOG
        if keys:
            cat = {k: v for k, v in cat.items() if k in keys or v.symbol in keys}
        df = self.fetcher.fetch_all_commodities(catalog=cat, period=period)
        if df.empty:
            logger.warning("No commodity price records to save.")
            return 0
        return self.db.save_commodity_prices(df)

    def sync_fx(
        self,
        pairs: Optional[List[str]] = None,
        period: str = "4y",
    ) -> int:
        """Fetch and upsert foreign exchange rate data for catalog currency pairs."""
        cat = FX_CATALOG
        if pairs:
            cat = {k: v for k, v in cat.items() if k in pairs or v.pair in pairs or v.symbol in pairs}
        df = self.fetcher.fetch_all_fx(catalog=cat, period=period)
        if df.empty:
            logger.warning("No FX rate records to save.")
            return 0
        return self.db.save_fx_rates(df)

    def sync_all(self, period: str = "4y") -> Dict[str, int]:
        """Sync all database issuer stocks, default commodities, and foreign exchange rates."""
        stocks_count = self.sync_all_issuers(period=period)
        commodities_count = self.sync_commodities(period=period)
        fx_count = self.sync_fx(period=period)
        return {
            "stocks_rows": stocks_count,
            "commodities_rows": commodities_count,
            "fx_rows": fx_count,
        }
