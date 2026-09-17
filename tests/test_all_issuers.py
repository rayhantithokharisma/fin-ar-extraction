"""
Integration Test Suite for All Issuers (NCKL, IPCM, BBSI).
Validates multi-industry sectionizing, fact extraction count (>= 40 items),
accounting identities, and Gold analytical views.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from src.parser.pdf_loader import PDFDocument
from src.parser.sectionizer import Sectionizer
from src.storage.database import DatabaseStore
from src.storage.gold_views import create_gold_views


class TestAllIssuers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.examples_dir = Path("examples")
        cls.db = DatabaseStore(read_only=True)

    def test_nckl_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "nckl" / "FinancialStatement-2025-Tahunan-NCKL.pdf")
        self.assertEqual(doc.ticker, "NCKL")
        self.assertEqual(doc.scale, 1_000_000)
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)  # BS, IS, Equity, CF
        doc.close()

    def test_ipcm_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "ipcm" / "FinancialStatement-2025-Tahunan-IPCM.pdf")
        self.assertEqual(doc.ticker, "IPCM")
        self.assertEqual(doc.scale, 1_000)
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)  # BS [3210000], IS [3311000], Equity, CF
        doc.close()

    def test_bbsi_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "bbsi" / "FinancialStatement-2025-Tahunan-BBSI.pdf")
        self.assertEqual(doc.ticker, "BBSI")
        self.assertEqual(doc.scale, 1)  # Satuan penuh
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)  # BS [4220000], IS [4312000], Equity, CF
        doc.close()

    def test_atic_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "atic" / "FinancialStatement-2025-Tahunan-ATIC.pdf")
        self.assertEqual(doc.ticker, "ATIC")
        self.assertEqual(doc.scale, 1)  # Full IDR
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_cass_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "cass" / "FinancialStatement-2025-Tahunan-CASS.pdf")
        self.assertEqual(doc.ticker, "CASS")
        self.assertEqual(doc.scale, 1_000_000)  # Million IDR
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_lsip_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "lsip" / "FinancialStatement-2025-Tahunan-LSIP.pdf")
        self.assertEqual(doc.ticker, "LSIP")
        self.assertEqual(doc.scale, 1_000_000)  # Million IDR
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_abmm_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "abmm" / "FinancialStatement-2025-Tahunan-ABMM.pdf")
        self.assertEqual(doc.ticker, "ABMM")
        self.assertEqual(doc.currency, "USD")
        self.assertEqual(doc.scale, 1)  # Full USD
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_adro_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "adro" / "FinancialStatement-2025-Tahunan-ADRO.pdf")
        self.assertEqual(doc.ticker, "ADRO")
        self.assertEqual(doc.currency, "USD")
        self.assertEqual(doc.scale, 1_000)  # In thousand USD
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_bbca_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "bbca" / "FinancialStatement-2025-Tahunan-BBCA.pdf")
        self.assertEqual(doc.ticker, "BBCA")
        self.assertEqual(doc.currency, "IDR")
        self.assertEqual(doc.scale, 1_000_000)  # Million IDR
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_bren_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "bren" / "FinancialStatement-2025-Tahunan-BREN.pdf")
        self.assertEqual(doc.ticker, "BREN")
        self.assertEqual(doc.currency, "USD")
        self.assertEqual(doc.scale, 1_000)  # In thousand USD
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_mapi_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "mapi" / "FinancialStatement-2025-Tahunan-MAPI.pdf")
        self.assertEqual(doc.ticker, "MAPI")
        self.assertEqual(doc.currency, "IDR")
        self.assertEqual(doc.scale, 1_000_000)
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_dmas_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "dmas" / "FinancialStatement-2025-Tahunan-DMAS.pdf")
        self.assertEqual(doc.ticker, "DMAS")
        self.assertEqual(doc.currency, "IDR")
        self.assertEqual(doc.scale, 1)  # Full IDR
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_arci_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "arci" / "FinancialStatement-2025-Tahunan-ARCI.pdf")
        self.assertEqual(doc.ticker, "ARCI")
        self.assertEqual(doc.currency, "USD")
        self.assertEqual(doc.scale, 1)  # Full USD
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_bisi_lk_sectionizing(self):
        doc = PDFDocument(self.examples_dir / "bisi" / "FinancialStatement-2025-Tahunan-BISI.pdf")
        self.assertEqual(doc.ticker, "BISI")
        self.assertEqual(doc.currency, "IDR")
        self.assertEqual(doc.scale, 1_000_000)
        self.assertEqual(doc.doc_type, "fs_annual")
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        stmt_spans = [s for s in spans if s.section_type == "lk.statement"]
        self.assertGreaterEqual(len(stmt_spans), 4)
        doc.close()

    def test_all_ar_sectionizing(self):
        for ticker, rel_path in [
            ("NCKL", "nckl/AnnualReport.pdf"),
            ("IPCM", "ipcm/AnnualReport.pdf"),
            ("BBSI", "bbsi/AnnualReport.pdf"),
            ("ADRO", "adro/annualreport.pdf"),
            ("BREN", "bren/annualreport.pdf"),
            ("BBCA", "bbca/annualreport.pdf"),
            ("ABMM", "abmm/annualreport.pdf"),
            ("MAPI", "mapi/annualreport.pdf"),
            ("DMAS", "dmas/annualreport.pdf"),
            ("ARCI", "arci/annualreport.pdf"),
            ("BISI", "bisi/annualreport.pdf"),
        ]:
            doc = PDFDocument(self.examples_dir / rel_path)
            self.assertEqual(doc.ticker, ticker)
            self.assertEqual(doc.doc_type, "annual_report")
            self.assertGreaterEqual(doc.page_count, 100)
            sectionizer = Sectionizer(doc)
            spans = sectionizer.sectionize()
            self.assertGreaterEqual(len(spans), 3, f"Failed sectionizing for {ticker} AR")
            doc.close()

    def test_minimum_40_extracted_items_in_database(self):
        """Ensure minimum 40 facts/records exist for each active issuer in database."""
        all_issuers = [
            "NCKL", "IPCM", "BBSI", "ATIC", "CASS", "LSIP",
            "ABMM", "ADRO", "BBCA", "BREN", "MAPI", "DMAS", "ARCI", "BISI"
        ]
        for ticker in all_issuers:
            facts_count = self.db.conn.execute(
                f"SELECT COUNT(*) FROM idxfin_silver.fact WHERE issuer_id = '{ticker}'"
            ).fetchone()[0]
            officers_count = self.db.conn.execute(
                f"SELECT COUNT(*) FROM idxfin_silver.officer_position WHERE issuer_id = '{ticker}'"
            ).fetchone()[0]
            total_items = facts_count + officers_count
            self.assertGreaterEqual(
                total_items, 40,
                f"Expected >= 40 extracted items for {ticker}, but got {total_items} (facts: {facts_count}, officers: {officers_count})"
            )

    def test_gold_financial_summary_metrics_populated(self):
        """Verify that Gold financial_summary has valid populated metrics without raw NaNs for all 14 issuers."""
        issuers_tuple = "('NCKL', 'IPCM', 'BBSI', 'ATIC', 'CASS', 'LSIP', 'ABMM', 'ADRO', 'BBCA', 'BREN', 'MAPI', 'DMAS', 'ARCI', 'BISI')"
        df = self.db.conn.execute(f"""
            SELECT issuer_id, fiscal_year, total_assets, total_liabilities, total_equity, net_profit, revenue, operating_cash_flow
            FROM idxfin_gold.financial_summary
            WHERE fiscal_year = 2025 AND issuer_id IN {issuers_tuple}
        """).df()
        self.assertEqual(len(df), 14, f"Expected 14 issuers in Gold financial summary, got {len(df)}")
        for _, row in df.iterrows():
            ticker = row["issuer_id"]
            self.assertIsNotNone(row["total_assets"], f"{ticker} total_assets is None")
            self.assertGreater(row["total_assets"], 0, f"{ticker} total_assets <= 0")
            self.assertIsNotNone(row["total_liabilities"], f"{ticker} total_liabilities is None")
            self.assertGreater(row["total_liabilities"], 0, f"{ticker} total_liabilities <= 0")
            self.assertIsNotNone(row["total_equity"], f"{ticker} total_equity is None")
            self.assertGreater(row["total_equity"], 0, f"{ticker} total_equity <= 0")
            self.assertIsNotNone(row["net_profit"], f"{ticker} net_profit is None")
            self.assertGreater(row["net_profit"], 0, f"{ticker} net_profit <= 0")
            self.assertIsNotNone(row["revenue"], f"{ticker} revenue is None")
            self.assertGreater(row["revenue"], 0, f"{ticker} revenue <= 0")
            self.assertIsNotNone(row["operating_cash_flow"], f"{ticker} CFO is None")

    def test_balance_sheet_identity_all_fourteen_issuers(self):
        """Verify Assets = Liabilities + Equity (+ Sharia Temporary Syirkah Funds) across all 14 issuers."""
        import pandas as pd
        issuers_tuple = "('NCKL', 'IPCM', 'BBSI', 'ATIC', 'CASS', 'LSIP', 'ABMM', 'ADRO', 'BBCA', 'BREN', 'MAPI', 'DMAS', 'ARCI', 'BISI')"
        df = self.db.conn.execute(f"""
            SELECT issuer_id, total_assets, total_liabilities, total_equity, temporary_syirkah_funds
            FROM idxfin_gold.financial_summary
            WHERE fiscal_year = 2025 AND issuer_id IN {issuers_tuple}
        """).df()
        for _, row in df.iterrows():
            ticker = row["issuer_id"]
            syirkah = 0 if pd.isna(row["temporary_syirkah_funds"]) else row["temporary_syirkah_funds"]
            diff = abs(row["total_assets"] - (row["total_liabilities"] + row["total_equity"] + syirkah))
            self.assertLessEqual(
                diff, 1000,
                f"BS identity failed for {ticker}: Assets ({row['total_assets']}) != Liab+Eq+Syirkah ({row['total_liabilities'] + row['total_equity'] + syirkah})"
            )

    def test_bank_vs_non_bank_field_availability(self):
        """
        Verify that bank-specific fields (bank_deposits, bank_loans) are populated for BBSI and BBCA
        and null/unavailable for non-banks (NCKL, IPCM, ATIC, CASS, LSIP, ABMM, ADRO, BREN, MAPI, DMAS, ARCI, BISI).
        Verify that classified current_assets are null/unavailable for BBSI and BBCA and populated for non-banks.
        """
        import pandas as pd
        issuers_tuple = "('NCKL', 'IPCM', 'BBSI', 'ATIC', 'CASS', 'LSIP', 'ABMM', 'ADRO', 'BBCA', 'BREN', 'MAPI', 'DMAS', 'ARCI', 'BISI')"
        df = self.db.conn.execute(f"""
            SELECT issuer_id, current_assets, bank_loans, bank_deposits
            FROM idxfin_gold.financial_summary
            WHERE fiscal_year = 2025 AND issuer_id IN {issuers_tuple}
        """).df()
        banks = {"BBSI", "BBCA"}
        for _, row in df.iterrows():
            ticker = row["issuer_id"]
            if ticker in banks:
                self.assertTrue(pd.isna(row["current_assets"]), f"Expected {ticker} current_assets to be unavailable/NaN, got {row['current_assets']}")
                self.assertFalse(pd.isna(row["bank_loans"]), f"Expected {ticker} bank_loans to be populated")
                self.assertFalse(pd.isna(row["bank_deposits"]), f"Expected {ticker} bank_deposits to be populated")
            else:
                self.assertFalse(pd.isna(row["current_assets"]), f"Expected {ticker} current_assets to be populated")
                self.assertTrue(pd.isna(row["bank_loans"]), f"Expected non-bank {ticker} bank_loans to be unavailable/NaN")
                self.assertTrue(pd.isna(row["bank_deposits"]), f"Expected non-bank {ticker} bank_deposits to be unavailable/NaN")

    def test_multi_currency_gold_reporting(self):
        """Verify that multi-currency reporting is correctly preserved in Gold views (USD vs IDR)."""
        issuers_tuple = "('NCKL', 'IPCM', 'BBSI', 'ATIC', 'CASS', 'LSIP', 'ABMM', 'ADRO', 'BBCA', 'BREN', 'MAPI', 'DMAS', 'ARCI', 'BISI')"
        df = self.db.conn.execute(f"""
            SELECT issuer_id, currency
            FROM idxfin_gold.financial_summary
            WHERE fiscal_year = 2025 AND issuer_id IN {issuers_tuple}
        """).df()
        usd_issuers = {"ABMM", "ADRO", "BREN", "ARCI"}
        idr_issuers = {"NCKL", "IPCM", "BBSI", "ATIC", "CASS", "LSIP", "BBCA", "MAPI", "DMAS", "BISI"}
        for _, row in df.iterrows():
            ticker = row["issuer_id"]
            if ticker in usd_issuers:
                self.assertEqual(row["currency"], "USD", f"Expected USD for {ticker}, got {row['currency']}")
            elif ticker in idr_issuers:
                self.assertEqual(row["currency"], "IDR", f"Expected IDR for {ticker}, got {row['currency']}")

    def test_provenance_citations_available(self):
        """Verify that source page citations exist for extracted metrics across all 14 issuers."""
        issuers_tuple = "('NCKL', 'IPCM', 'BBSI', 'ATIC', 'CASS', 'LSIP', 'ABMM', 'ADRO', 'BBCA', 'BREN', 'MAPI', 'DMAS', 'ARCI', 'BISI')"
        df = self.db.conn.execute(f"""
            SELECT issuer_id, total_assets_page, total_liabilities_page, total_equity_page, revenue_page, net_profit_page
            FROM idxfin_gold.financial_summary_provenance
            WHERE fiscal_year = 2025 AND issuer_id IN {issuers_tuple}
        """).df()
        self.assertEqual(len(df), 14, f"Expected 14 issuers in provenance table, got {len(df)}")
        for _, row in df.iterrows():
            self.assertTrue(str(row["total_assets_page"]).startswith("p."))
            self.assertTrue(str(row["total_liabilities_page"]).startswith("p."))
            self.assertTrue(str(row["total_equity_page"]).startswith("p."))
            self.assertTrue(str(row["revenue_page"]).startswith("p."))
            self.assertTrue(str(row["net_profit_page"]).startswith("p."))


if __name__ == "__main__":
    unittest.main()
