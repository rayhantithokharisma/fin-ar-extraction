"""
Automated Semantic Contract Verification for Wren AI & DuckDB Gold Layer.
Verifies that all views, columns, and relationships mapped in MDL exist, have proper types,
and answer fundamental queries with zero null pointer or lock exceptions.
"""
import unittest
from pathlib import Path
import duckdb


class TestWrenSemanticContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_path = Path("data/idxfin.duckdb")
        cls.conn = duckdb.connect(str(db_path), read_only=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_financial_summary_schema_and_issuers(self):
        """Verify all 14 benchmark issuers + ESSA are present in financial_summary."""
        issuers = self.conn.execute("""
            SELECT DISTINCT issuer_id 
            FROM idxfin_gold.financial_summary 
            WHERE fiscal_year = 2025
        """).fetchall()
        issuer_set = {r[0] for r in issuers}
        expected = {"BBCA", "NCKL", "ADRO", "MAPI", "ABMM", "ATIC", "LSIP", 
                    "BREN", "ARCI", "CASS", "BBSI", "DMAS", "BISI", "IPCM", "ESSA"}
        self.assertTrue(expected.issubset(issuer_set), f"Missing issuers: {expected - issuer_set}")

    def test_sharia_balance_sheet_identity(self):
        """Verify BBCA sharia temporary syirkah funds balance sheet identity."""
        diff = self.conn.execute("""
            SELECT total_assets - (total_liabilities + total_equity + COALESCE(temporary_syirkah_funds, 0)) AS diff
            FROM idxfin_gold.financial_summary
            WHERE issuer_id = 'BBCA' AND fiscal_year = 2025
        """).fetchone()[0]
        self.assertEqual(diff, 0, "BBCA balance sheet discrepancy detected!")

    def test_market_valuation_linkage(self):
        """Verify market_valuation correctly links stock prices with fundamentals."""
        row = self.conn.execute("""
            SELECT period_close_price, latest_close_price, price_change_since_period_pct
            FROM idxfin_gold.market_valuation
            WHERE issuer_id = 'ADRO' AND fiscal_year = 2025
        """).fetchone()
        self.assertIsNotNone(row)
        self.assertGreater(row[0], 0)
        self.assertGreater(row[1], 0)
        self.assertIsNotNone(row[2])

    def test_essa_market_valuation_linkage(self):
        """Verify market_valuation correctly links stock prices with ESSA fundamentals."""
        row = self.conn.execute("""
            SELECT period_close_price, latest_close_price, price_change_since_period_pct
            FROM idxfin_gold.market_valuation
            WHERE issuer_id = 'ESSA' AND fiscal_year = 2025
        """).fetchone()
        self.assertIsNotNone(row)
        self.assertGreater(row[0], 0)
        self.assertGreater(row[1], 0)

    def test_commodity_trend_monthly_coverage(self):
        """Verify commodity benchmarks, physical ammonia benchmark, and proxies exist in monthly trend view."""
        symbols = self.conn.execute("""
            SELECT DISTINCT symbol 
            FROM idxfin_gold.commodity_trend_monthly
        """).fetchall()
        symbol_set = {r[0] for r in symbols}
        expected_commodities = {"GC=F", "CL=F", "BZ=F", "ESSA.JK", "CF", "NG=F", "NH3=F"}
        self.assertTrue(expected_commodities.issubset(symbol_set), f"Missing commodities: {expected_commodities - symbol_set}")

        # Check NH3=F specific stats
        nh3_stats = self.conn.execute("""
            SELECT COUNT(*), AVG(avg_close), MIN(min_close), MAX(max_close), MAX(unit)
            FROM idxfin_gold.commodity_trend_monthly
            WHERE symbol = 'NH3=F';
        """).fetchone()
        self.assertGreaterEqual(nh3_stats[0], 50)  # > 50 months from 2021 to 2026
        self.assertGreater(nh3_stats[1], 400.0)
        self.assertLess(nh3_stats[1], 900.0)
        self.assertEqual(nh3_stats[4], "USD/ton")

    def test_governance_and_audit_linkage(self):
        """Verify governance officers and audit reports exist for NCKL."""
        officer_count = self.conn.execute("""
            SELECT COUNT(*) FROM idxfin_silver.officer_position WHERE issuer_id = 'NCKL'
        """).fetchone()[0]
        self.assertGreaterEqual(officer_count, 1)

        audit_row = self.conn.execute("""
            SELECT audit_firm, opinion FROM idxfin_silver.audit_report WHERE issuer_id = 'NCKL' AND fiscal_year = 2025
        """).fetchone()
        self.assertIsNotNone(audit_row)

    def test_golden_queries_execution(self):
        """Execute all golden queries defined in golden_queries.yaml to verify validity."""
        # Query 1: Multi-Year Profitability
        res1 = self.conn.execute("""
            SELECT issuer_id, fiscal_year, currency, revenue, net_profit
            FROM idxfin_gold.financial_summary
            WHERE issuer_id = 'NCKL' AND fiscal_year IN (2024, 2025)
            ORDER BY fiscal_year ASC;
        """).fetchall()
        self.assertEqual(len(res1), 2)

        # Query 2: Bank Loans & Deposits
        res2 = self.conn.execute("""
            SELECT issuer_id, fiscal_year, currency, bank_loans, bank_deposits,
                   ROUND((bank_loans / NULLIF(bank_deposits, 0)) * 100, 2) AS ldr_pct
            FROM idxfin_gold.financial_summary
            WHERE issuer_id = 'BBCA' AND fiscal_year = 2025;
        """).fetchone()
        self.assertIsNotNone(res2)
        self.assertGreater(res2[3], 0)  # bank_loans
        self.assertGreater(res2[4], 0)  # bank_deposits

        # Query 3: Provenance Join
        res3 = self.conn.execute("""
            SELECT s.issuer_id, s.fiscal_year, s.total_assets, s.currency, p.total_assets_page
            FROM idxfin_gold.financial_summary s
            JOIN idxfin_gold.financial_summary_provenance p
              ON s.issuer_id = p.issuer_id AND s.period_end = p.period_end
            WHERE s.issuer_id = 'MAPI' AND s.fiscal_year = 2025;
        """).fetchone()
        self.assertIsNotNone(res3)
        self.assertTrue(res3[4].startswith("p."))

    def test_wren_toolkit_mdl_and_query_execution(self):
        """Verify WrenToolkit from wren-langchain executes semantic queries with calculated fields."""
        from wren_langchain import WrenToolkit

        toolkit = WrenToolkit.from_project("wren/projects/idxfin")
        table = toolkit.query(
            "SELECT issuer_id, fiscal_year, return_on_equity_pct, net_profit_margin_pct "
            "FROM financial_summary WHERE issuer_id = 'ESSA' ORDER BY fiscal_year DESC"
        )
        df = table.to_pandas()
        self.assertEqual(len(df), 3)
        self.assertIn("return_on_equity_pct", df.columns)
        self.assertGreater(float(df.iloc[0]["return_on_equity_pct"]), 0)

    def test_wren_toolkit_join_execution(self):
        """Verify WrenToolkit handles cross-model semantic joins."""
        from wren_langchain import WrenToolkit

        toolkit = WrenToolkit.from_project("wren/projects/idxfin")
        table = toolkit.query(
            "SELECT f.issuer_id, f.fiscal_year, f.net_profit, m.period_close_price, m.latest_close_price "
            "FROM financial_summary f "
            "JOIN market_valuation m ON f.issuer_id = m.issuer_id AND f.period_end = m.period_end "
            "WHERE f.issuer_id = 'ESSA' AND f.fiscal_year = 2025"
        )
        df = table.to_pandas()
        self.assertEqual(len(df), 1)
        self.assertEqual(float(df.iloc[0]["period_close_price"]), 605.0)
        self.assertEqual(float(df.iloc[0]["latest_close_price"]), 610.0)

    def test_fx_rate_daily_coverage(self):
        """Verify USD/IDR exchange rates exist in idxfin_gold.fx_rate_daily with valid ranges and averages."""
        row = self.conn.execute("""
            SELECT 
                COUNT(*) as bars,
                MIN(rate_close) as min_rate,
                MAX(rate_close) as max_rate,
                AVG(rate_close) as avg_rate
            FROM idxfin_gold.fx_rate_daily
            WHERE pair = 'USD/IDR';
        """).fetchone()
        self.assertIsNotNone(row)
        self.assertGreaterEqual(row[0], 1000)
        self.assertGreater(row[1], 14000)
        self.assertLess(row[2], 19000)

        # Check year-end 2025 and 2024 rates
        ye_rates = self.conn.execute("""
            SELECT trade_date, rate_close, yearly_avg_rate, monthly_avg_rate
            FROM idxfin_gold.fx_rate_daily
            WHERE pair = 'USD/IDR' AND trade_date IN ('2024-12-31', '2025-12-31')
            ORDER BY trade_date ASC;
        """).fetchall()
        self.assertEqual(len(ye_rates), 2)
        # 2024-12-31 rate
        self.assertAlmostEqual(ye_rates[0][1], 16085.6, delta=5.0)
        self.assertGreater(ye_rates[0][2], 15000)
        # 2025-12-31 rate
        self.assertAlmostEqual(ye_rates[1][1], 16709.0, delta=5.0)
        self.assertGreater(ye_rates[1][2], 16000)

    def test_wren_toolkit_fx_rate_query(self):
        """Verify WrenToolkit can execute semantic queries against fx_rate model."""
        from wren_langchain import WrenToolkit

        toolkit = WrenToolkit.from_project("wren/projects/idxfin")
        table = toolkit.query(
            "SELECT pair, trade_date, rate_close, yearly_avg_rate "
            "FROM fx_rate "
            "WHERE pair = 'USD/IDR' AND trade_date = '2025-12-31'"
        )
        df = table.to_pandas()
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["pair"], "USD/IDR")
        self.assertAlmostEqual(float(df.iloc[0]["rate_close"]), 16709.0, delta=5.0)


if __name__ == "__main__":
    unittest.main()


