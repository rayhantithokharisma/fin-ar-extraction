"""
Gold Views Engine: Creates analytical views for fact_latest, discrete quarters, TTM, and derived metrics.
Directly implements logic from storage_ddl.sql on DuckDB.
"""
from __future__ import annotations

import logging
from src.storage.database import DatabaseStore

logger = logging.getLogger(__name__)


def create_gold_views(db: DatabaseStore) -> None:
    """Create or replace all Gold views in the DuckDB database."""
    if getattr(db, "read_only", False):
        return
    conn = db.conn

    # 1. fact_latest: best known value per concept, period, and dimensions
    conn.execute("""
        CREATE OR REPLACE VIEW idxfin_gold.fact_latest AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT f.*, ROW_NUMBER() OVER (
                PARTITION BY issuer_id, concept_id, period_type, period_start, period_end, dim_key, scenario, consolidation_scope
                ORDER BY CASE assurance WHEN 'audited' THEN 0 WHEN 'reviewed' THEN 1 WHEN 'unaudited' THEN 2 ELSE 3 END,
                         knowledge_ts DESC, lineage_confidence DESC
            ) AS rn
            FROM idxfin_silver.fact f
            WHERE concept_id IS NOT NULL AND review_status <> 'rejected'
        ) WHERE rn = 1;
    """)

    # 2. fact_first_reported: what the market saw on day one
    conn.execute("""
        CREATE OR REPLACE VIEW idxfin_gold.fact_first_reported AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT f.*, ROW_NUMBER() OVER (
                PARTITION BY issuer_id, concept_id, period_type, period_start, period_end, dim_key, scenario, consolidation_scope
                ORDER BY knowledge_ts ASC,
                         CASE assurance WHEN 'audited' THEN 0 WHEN 'reviewed' THEN 1 WHEN 'unaudited' THEN 2 ELSE 3 END
            ) AS rn
            FROM idxfin_silver.fact f
            WHERE concept_id IS NOT NULL AND review_status <> 'rejected'
        ) WHERE rn = 1;
    """)

    # 3. fact_discrete_quarter: derive discrete quarters from cumulative YTD filings
    conn.execute("""
        CREATE OR REPLACE VIEW idxfin_gold.fact_discrete_quarter AS
        WITH base AS (
            SELECT f.issuer_id, f.concept_id, c.domain, f.dim_key, f.scenario, f.consolidation_scope, f.unit_id,
                   f.fiscal_year, f.period_end, f.duration_months, f.is_cumulative_ytd, f.value
            FROM idxfin_gold.fact_latest f
            LEFT JOIN idxfin_ref.concept c ON c.concept_id = f.concept_id
            WHERE f.period_type = 'duration' AND COALESCE(c.period_aggregation, 'additive') = 'additive'
        ),
        ytd AS (
            SELECT * FROM base WHERE is_cumulative_ytd AND duration_months IN (3, 6, 9, 12)
        ),
        derived AS (
            SELECT cur.issuer_id, cur.concept_id, cur.domain, cur.dim_key, cur.scenario, cur.consolidation_scope, cur.unit_id,
                   cur.fiscal_year, cur.period_end, CAST(cur.duration_months / 3 AS INT) AS quarter_no,
                   cur.value - COALESCE(prev.value, 0) AS value,
                   'derived_from_ytd' AS derivation,
                   cur.duration_months = 12 AS absorbs_year_end_adjustments
            FROM ytd cur
            LEFT JOIN ytd prev
              ON  prev.issuer_id = cur.issuer_id AND prev.concept_id = cur.concept_id AND prev.dim_key = cur.dim_key
              AND prev.scenario = cur.scenario AND prev.fiscal_year = cur.fiscal_year
              AND prev.duration_months = cur.duration_months - 3
            WHERE cur.duration_months = 3 OR prev.value IS NOT NULL
        )
        SELECT * FROM derived;
    """)

    # 4. Key Financial Summary View
    conn.execute("""
        CREATE OR REPLACE VIEW idxfin_gold.financial_summary AS
        SELECT
            issuer_id,
            period_end,
            fiscal_year,
            fiscal_period,
            MAX(currency) AS currency,
            MAX(CASE WHEN concept_id = 'fs.bs.total_assets' THEN value END) AS total_assets,
            MAX(CASE WHEN concept_id = 'fs.bs.total_current_assets' THEN value END) AS current_assets,
            MAX(CASE WHEN concept_id = 'fs.bs.total_liabilities' THEN value END) AS total_liabilities,
            MAX(CASE WHEN concept_id = 'fs.bs.total_current_liabilities' THEN value END) AS current_liabilities,
            MAX(CASE WHEN concept_id = 'fs.bs.total_equity' THEN value END) AS total_equity,
            MAX(CASE WHEN concept_id = 'fs.bs.sharia.temporary_syirkah_funds' THEN value END) AS temporary_syirkah_funds,
            -- Top-line revenue: Coalesced across corporate, banking, insurance, and operating revenue
            COALESCE(
                MAX(CASE WHEN concept_id = 'fs.is.revenue' THEN value END),
                MAX(CASE WHEN concept_id = 'fs.is.interest_income' THEN value END),
                MAX(CASE WHEN concept_id = 'fs.is.insurance.premium_income' THEN value END),
                MAX(CASE WHEN concept_id = 'fs.is.brokerage_income' THEN value END),
                MAX(CASE WHEN concept_id = 'fs.is.operating_income' THEN value END)
            ) AS revenue,
            -- Cost of revenue: Corporate COGS or banking interest expense (cost of funds)
            COALESCE(
                MAX(CASE WHEN concept_id = 'fs.is.cost_of_revenue' THEN value END),
                MAX(CASE WHEN concept_id = 'fs.is.interest_expense' THEN value END)
            ) AS cost_of_revenue,
            -- Gross profit: Standard gross profit or banking Net Interest Income (Interest Income - Interest Expense)
            COALESCE(
                MAX(CASE WHEN concept_id = 'fs.is.gross_profit' THEN value END),
                MAX(CASE WHEN concept_id = 'fs.is.interest_income' THEN value END) - 
                COALESCE(MAX(CASE WHEN concept_id = 'fs.is.interest_expense' THEN value END), 0)
            ) AS gross_profit,
            MAX(CASE WHEN concept_id = 'fs.is.profit_for_the_period' THEN value END) AS net_profit,
            MAX(CASE WHEN concept_id = 'fs.cf.net_cash_operating' THEN value END) AS operating_cash_flow,
            MAX(CASE WHEN concept_id = 'fs.cf.net_cash_investing' THEN value END) AS investing_cash_flow,
            MAX(CASE WHEN concept_id = 'fs.cf.net_cash_financing' THEN value END) AS financing_cash_flow,
            COALESCE(
                MAX(CASE WHEN concept_id = 'fs.cf.cash_ending' THEN value END),
                MAX(CASE WHEN concept_id = 'fs.bs.cash_and_equivalents' THEN value END)
            ) AS cash_and_equivalents,
            -- Capex: Acquisition of PPE, bearer plants, exploration assets, or intangibles from Cash Flow
            COALESCE(
                ABS(MAX(CASE WHEN concept_id = 'fs.cf.acquisition_ppe' AND value <> 0 THEN value END)),
                ABS(MAX(CASE WHEN concept_id = 'fs.cf.acquisition_bearer_plants' AND value <> 0 THEN value END)),
                ABS(MAX(CASE WHEN concept_id = 'fs.cf.acquisition_intangibles' AND value <> 0 THEN value END))
            ) AS capex,
            -- Interest / Finance Income: Banking interest income OR Corporate finance income
            COALESCE(
                MAX(CASE WHEN concept_id = 'fs.is.interest_income' THEN value END),
                MAX(CASE WHEN concept_id = 'fs.is.finance_income' THEN value END)
            ) AS interest_income,
            COALESCE(
                MAX(CASE WHEN concept_id = 'fs.is.net_interest_income' THEN value END),
                MAX(CASE WHEN concept_id = 'fs.is.interest_income' THEN value END) - 
                COALESCE(MAX(CASE WHEN concept_id = 'fs.is.interest_expense' THEN value END), 0)
            ) AS net_interest_income,
            COALESCE(
                MAX(CASE WHEN concept_id IN ('fs.bs.bank.loans_gross', 'fs.bs.bank.loans') THEN value END),
                SUM(CASE WHEN LOWER(label_raw) LIKE '%kredit yang diberikan%'
                          OR LOWER(label_raw) LIKE '%pinjaman yang diberikan%' THEN value END)
            ) AS bank_loans,
            -- Customer deposits: Standard concept OR sum of banking demand/savings/time deposits
            COALESCE(
                MAX(CASE WHEN concept_id = 'fs.bs.bank.customer_deposits' THEN value END),
                SUM(CASE WHEN concept_id LIKE 'fs.bs.%' AND (
                    concept_id IN (
                        'fs.bs.bank.demand_deposits', 'fs.bs.bank.savings_deposits', 'fs.bs.bank.time_deposits',
                        'fs.bs.bank.demand_deposits_related', 'fs.bs.bank.savings_deposits_related', 'fs.bs.bank.time_deposits_related',
                        'fs.bs.sharia.demand_deposits_mudharabah', 'fs.bs.sharia.savings_deposits_mudharabah', 'fs.bs.sharia.time_deposits_mudharabah'
                    ) OR concept_id LIKE 'fs.bs.bank.%deposit%'
                      OR LOWER(label_raw) LIKE '%simpanan nasabah%'
                      OR LOWER(label_raw) LIKE '%simpanan dari nasabah%'
                      OR LOWER(label_raw) LIKE '%dana pihak ketiga%'
                ) THEN value END)
            ) AS bank_deposits
        FROM idxfin_gold.fact_latest
        WHERE dim_key = '' AND scenario = 'actual'
        GROUP BY issuer_id, period_end, fiscal_year, fiscal_period;
    """)

    # 5. Provenance & Grounding View
    conn.execute("""
        CREATE OR REPLACE VIEW idxfin_gold.financial_summary_provenance AS
        SELECT
            issuer_id,
            period_end,
            fiscal_year,
            fiscal_period,
            MAX(currency) AS currency,
            MAX(CASE WHEN concept_id = 'fs.bs.total_assets' THEN 'p.' || CAST(lineage_page AS VARCHAR) END) AS total_assets_page,
            MAX(CASE WHEN concept_id = 'fs.bs.total_liabilities' THEN 'p.' || CAST(lineage_page AS VARCHAR) END) AS total_liabilities_page,
            MAX(CASE WHEN concept_id = 'fs.bs.total_equity' THEN 'p.' || CAST(lineage_page AS VARCHAR) END) AS total_equity_page,
            MAX(CASE WHEN concept_id = 'fs.is.revenue' THEN 'p.' || CAST(lineage_page AS VARCHAR) 
                     WHEN concept_id = 'fs.is.interest_income' THEN 'p.' || CAST(lineage_page AS VARCHAR) END) AS revenue_page,
            MAX(CASE WHEN concept_id = 'fs.is.profit_for_the_period' THEN 'p.' || CAST(lineage_page AS VARCHAR) END) AS net_profit_page,
            MAX(CASE WHEN concept_id = 'fs.cf.net_cash_operating' THEN 'p.' || CAST(lineage_page AS VARCHAR) END) AS operating_cash_flow_page,
            MAX(CASE WHEN concept_id = 'fs.cf.acquisition_ppe' AND value <> 0 THEN 'p.' || CAST(lineage_page AS VARCHAR) END) AS capex_page,
            MAX(CASE WHEN concept_id = 'fs.is.interest_income' THEN 'p.' || CAST(lineage_page AS VARCHAR)
                     WHEN concept_id = 'fs.is.finance_income' THEN 'p.' || CAST(lineage_page AS VARCHAR) END) AS interest_income_page
        FROM idxfin_gold.fact_latest
        WHERE dim_key = '' AND scenario = 'actual'
        GROUP BY issuer_id, period_end, fiscal_year, fiscal_period;
    """)

    # 6. Market Valuation View (combining fundamental statements with stock prices)
    conn.execute("""
        CREATE OR REPLACE VIEW idxfin_gold.market_valuation AS
        WITH price_at_period AS (
            -- Find the closing price on the trading day closest to (on or after) period_end
            SELECT
                s.ticker,
                f.period_end,
                s.close AS period_close_price,
                s.trade_date AS period_price_date,
                ROW_NUMBER() OVER (
                    PARTITION BY s.ticker, f.period_end
                    ORDER BY ABS(DATE_DIFF('day', s.trade_date, f.period_end)) ASC, s.trade_date ASC
                ) AS rn
            FROM (SELECT DISTINCT issuer_id, period_end FROM idxfin_gold.financial_summary) f
            JOIN idxfin_market.stock_daily s
              ON s.ticker = f.issuer_id
             AND s.trade_date BETWEEN f.period_end - INTERVAL 5 DAY AND f.period_end + INTERVAL 5 DAY
        ),
        latest_price AS (
            -- Find the latest available market price
            SELECT
                ticker,
                close AS latest_close_price,
                trade_date AS latest_trade_date,
                ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date DESC) AS rn
            FROM idxfin_market.stock_daily
        )
        SELECT
            fs.issuer_id,
            fs.period_end,
            fs.fiscal_year,
            fs.fiscal_period,
            fs.currency,
            fs.revenue,
            fs.gross_profit,
            fs.net_profit,
            fs.total_assets,
            fs.total_liabilities,
            fs.total_equity,
            fs.operating_cash_flow,
            pap.period_close_price,
            pap.period_price_date,
            lp.latest_close_price,
            lp.latest_trade_date,
            ROUND(CASE WHEN pap.period_close_price IS NOT NULL AND pap.period_close_price > 0
                       THEN ((lp.latest_close_price - pap.period_close_price) / pap.period_close_price) * 100
                       ELSE NULL END, 2) AS price_change_since_period_pct
        FROM idxfin_gold.financial_summary fs
        LEFT JOIN price_at_period pap
          ON pap.ticker = fs.issuer_id AND pap.period_end = fs.period_end AND pap.rn = 1
        LEFT JOIN latest_price lp
          ON lp.ticker = fs.issuer_id AND lp.rn = 1;
    """)

    # 7. Commodity Trend Monthly View (monthly aggregations and returns)
    conn.execute("""
        CREATE OR REPLACE VIEW idxfin_gold.commodity_trend_monthly AS
        WITH monthly_ranked AS (
            SELECT
                symbol,
                commodity_name,
                commodity_category,
                unit,
                currency,
                DATE_TRUNC('month', trade_date) AS month_date,
                trade_date,
                close,
                volume,
                ROW_NUMBER() OVER (PARTITION BY symbol, DATE_TRUNC('month', trade_date) ORDER BY trade_date ASC) AS rn_first,
                ROW_NUMBER() OVER (PARTITION BY symbol, DATE_TRUNC('month', trade_date) ORDER BY trade_date DESC) AS rn_last
            FROM idxfin_market.commodity_daily
        ),
        monthly_stats AS (
            SELECT
                symbol,
                commodity_name,
                commodity_category,
                unit,
                currency,
                DATE_TRUNC('month', trade_date) AS month_date,
                ROUND(AVG(close), 4) AS avg_close,
                ROUND(MIN(close), 4) AS min_close,
                ROUND(MAX(close), 4) AS max_close,
                SUM(COALESCE(volume, 0)) AS total_volume,
                COUNT(*) AS trading_days
            FROM idxfin_market.commodity_daily
            GROUP BY symbol, commodity_name, commodity_category, unit, currency, DATE_TRUNC('month', trade_date)
        ),
        month_boundary AS (
            SELECT
                s.symbol,
                s.month_date,
                f.close AS open_month_close,
                l.close AS end_month_close
            FROM (SELECT DISTINCT symbol, month_date FROM monthly_stats) s
            JOIN monthly_ranked f ON f.symbol = s.symbol AND f.month_date = s.month_date AND f.rn_first = 1
            JOIN monthly_ranked l ON l.symbol = s.symbol AND l.month_date = s.month_date AND l.rn_last = 1
        )
        SELECT
            ms.symbol,
            ms.commodity_name,
            ms.commodity_category,
            ms.unit,
            ms.currency,
            CAST(ms.month_date AS DATE) AS month_date,
            ms.avg_close,
            ms.min_close,
            ms.max_close,
            mb.open_month_close,
            mb.end_month_close,
            ROUND(((mb.end_month_close - mb.open_month_close) / mb.open_month_close) * 100, 2) AS monthly_return_pct,
            ms.total_volume,
            ms.trading_days
        FROM monthly_stats ms
        JOIN month_boundary mb ON mb.symbol = ms.symbol AND mb.month_date = ms.month_date
        ORDER BY ms.symbol, ms.month_date DESC;
    """)

    # 8. operational_kpi: filter fact_latest to operational KPIs (production, sales, capacity)
    conn.execute("""
        CREATE OR REPLACE VIEW idxfin_gold.operational_kpi AS
        SELECT * FROM idxfin_gold.fact_latest
        WHERE concept_id LIKE 'op.%' OR concept_id LIKE 'reg.%';
    """)

    # 9. fx_rate_daily: analytical daily FX rates with monthly and yearly averages
    conn.execute("""
        CREATE OR REPLACE VIEW idxfin_gold.fx_rate_daily AS
        SELECT
            pair,
            symbol,
            trade_date,
            open,
            high,
            low,
            close AS rate_close,
            rate,
            base_currency,
            quote_currency,
            CAST(DATE_TRUNC('month', trade_date) AS DATE) AS month_date,
            YEAR(trade_date) AS fiscal_year,
            ROUND(AVG(close) OVER (PARTITION BY pair, DATE_TRUNC('month', trade_date)), 2) AS monthly_avg_rate,
            ROUND(AVG(close) OVER (PARTITION BY pair, YEAR(trade_date)), 2) AS yearly_avg_rate
        FROM idxfin_market.fx_daily
        ORDER BY pair, trade_date DESC;
    """)

    logger.info("Successfully created Gold views: fact_latest, fact_first_reported, fact_discrete_quarter, financial_summary, financial_summary_provenance, market_valuation, commodity_trend_monthly, operational_kpi, fx_rate_daily.")

