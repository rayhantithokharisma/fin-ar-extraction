"""
Integration tests for NCKL filing ingestion, sectionizing, database storage, and Gold views.
"""
import unittest
from pathlib import Path
from decimal import Decimal

from src.parser.pdf_loader import PDFDocument
from src.parser.sectionizer import Sectionizer
from src.storage.database import DatabaseStore
from src.storage.gold_views import create_gold_views
from src.normalization.normalizer import SilverFact


class TestNCKLIntegration(unittest.TestCase):
    def setUp(self):
        self.fs_path = Path("examples/nckl/FinancialStatement-2025-Tahunan-NCKL.pdf")
        self.ar_path = Path("examples/nckl/AnnualReport.pdf")
        self.db = DatabaseStore(":memory:")  # In-memory test db

    def tearDown(self):
        self.db.close()

    def test_fs_metadata_and_sections(self):
        doc = PDFDocument(self.fs_path)
        self.assertEqual(doc.ticker, "NCKL")
        self.assertEqual(doc.currency, "IDR")
        self.assertEqual(doc.scale, 1_000_000)
        self.assertEqual(doc.fiscal_period, "FY")
        self.assertEqual(doc.period_end, "2025-12-31")

        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        self.assertGreaterEqual(len(spans), 15)

        # Ensure Balance Sheet, Income Statement, Cash Flows are identified
        codes = [s.form_code for s in spans if s.form_code]
        self.assertIn("1210000", codes)  # BS
        self.assertIn("1311000", codes)  # IS
        self.assertIn("1510000", codes)  # CF
        doc.close()

    def test_database_medallion_and_gold_views(self):
        doc = PDFDocument(self.fs_path)
        self.db.save_document(doc)

        # Insert sample silver facts
        facts = [
            SilverFact(
                fact_id="f1",
                issuer_id="NCKL",
                doc_id=doc.doc_id,
                concept_id="fs.is.revenue",
                label_raw="Penjualan dan pendapatan usaha",
                period_type="duration",
                period_end="2025-12-31",
                fiscal_year=2025,
                fiscal_period="FY",
                scenario="actual",
                value=Decimal("29632799000000"),
                raw_text="29,632,799",
                scale_as_reported=1_000_000,
                knowledge_ts="2026-05-26T00:00:00",
                ingested_at="2026-05-26T00:00:00",
                lineage_page=18,
            ),
            SilverFact(
                fact_id="f2",
                issuer_id="NCKL",
                doc_id=doc.doc_id,
                concept_id="fs.is.cost_of_revenue",
                label_raw="Beban pokok penjualan",
                period_type="duration",
                period_end="2025-12-31",
                fiscal_year=2025,
                fiscal_period="FY",
                scenario="actual",
                value=Decimal("19941401000000"),
                raw_text="(19,941,401)",
                scale_as_reported=1_000_000,
                knowledge_ts="2026-05-26T00:00:00",
                ingested_at="2026-05-26T00:00:00",
                lineage_page=18,
            ),
            SilverFact(
                fact_id="f3",
                issuer_id="NCKL",
                doc_id=doc.doc_id,
                concept_id="fs.is.gross_profit",
                label_raw="Jumlah laba bruto",
                period_type="duration",
                period_end="2025-12-31",
                fiscal_year=2025,
                fiscal_period="FY",
                scenario="actual",
                value=Decimal("9691398000000"),
                raw_text="9,691,398",
                scale_as_reported=1_000_000,
                knowledge_ts="2026-05-26T00:00:00",
                ingested_at="2026-05-26T00:00:00",
                lineage_page=18,
            ),
        ]
        self.db.save_facts(facts)

        # Create gold views
        create_gold_views(self.db)

        # Query fact_latest
        df_latest = self.db.conn.execute("SELECT * FROM idxfin_gold.fact_latest").df()
        self.assertEqual(len(df_latest), 3)

        # Query financial_summary
        df_summary = self.db.conn.execute("SELECT * FROM idxfin_gold.financial_summary").df()
        self.assertEqual(len(df_summary), 1)
        self.assertEqual(df_summary["revenue"].iloc[0], 29632799000000.0)
        self.assertEqual(df_summary["gross_profit"].iloc[0], 9691398000000.0)
        doc.close()


if __name__ == "__main__":
    unittest.main()
