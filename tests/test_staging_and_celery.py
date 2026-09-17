"""
Unit & Integration Tests for Distributed Architecture:
- Parquet Staging Store
- Single-Writer DuckDB Vectorized Ingestion
- Celery Task Configuration & Queue Routing
- FastAPI Gateway Endpoints
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.api.app import app as fastapi_app
from src.celery_app import app as celery_app
from src.config import (
    CELERY_QUEUE_DUCKDB,
    CELERY_QUEUE_EXTRACTION,
    CELERY_WORKER_CONCURRENCY,
)
from src.normalization.normalizer import SilverFact
from src.storage.database import DatabaseStore
from src.storage.staging import ParquetStagingStore
from src.validation.engine import ValidationRuleResult


class TestStagingAndCelery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.staging_dir = self.temp_dir / "staging"
        self.db_path = self.temp_dir / "test_idxfin.duckdb"
        self.staging = ParquetStagingStore(base_dir=self.staging_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dummy_fact(self, fact_id: str, concept_id: str, value: Decimal, doc_id: str = "test_doc_sha256") -> SilverFact:
        return SilverFact(
            fact_id=fact_id,
            issuer_id="NCKL",
            reporting_entity="PT Trimegah Bangun Persada Tbk.",
            doc_id=doc_id,
            concept_id=concept_id,
            label_raw="Jumlah aset",
            label_raw_alt="Total assets",
            period_type="instant",
            period_end="2025-12-31",
            fiscal_year=2025,
            fiscal_period="FY",
            value=value,
            raw_text=f"{value:,}",
            unit_id="IDR",
            currency="IDR",
            scale_as_reported=1_000_000,
            knowledge_ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ingested_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            lineage_page=4,
        )

    def test_parquet_staging_save_and_mark_completed(self):
        """Verify Parquet staging writes strongly-typed tables and manages lifecycle."""
        run_id = "test-run-001"
        mock_doc = MagicMock()
        mock_doc.doc_id = "test_doc_sha256"
        mock_doc.ticker = "NCKL"
        mock_doc.doc_type = "fs_annual"
        mock_doc.fiscal_year = 2025
        mock_doc.fiscal_period = "FY"
        mock_doc.period_end = "2025-12-31"
        mock_doc.file_path = "examples/nckl/FinancialStatement-2025-Tahunan-NCKL.pdf"
        mock_doc.currency = "IDR"
        mock_doc.number_locale = "en"
        mock_doc.scale = 1_000_000
        mock_doc.page_count = 85
        mock_doc.has_text_layer = True

        facts = [
            self._create_dummy_fact("f1", "fs.bs.total_assets", Decimal("61770103")),
            self._create_dummy_fact("f2", "fs.bs.total_liabilities", Decimal("15014238")),
            self._create_dummy_fact("f3", "fs.bs.total_equity", Decimal("46755865")),
        ]

        val_results = [
            ValidationRuleResult(
                rule_id="bs.identity.assets_eq_liab_plus_equity",
                severity="hard",
                passed=True,
                lhs=Decimal("61770103"),
                rhs=Decimal("61770103"),
                diff=Decimal("0"),
                tolerance=Decimal("3"),
                message="Total Assets = Liabilities + Equity passed",
            )
        ]

        run_dir = self.staging.save_run_staging(
            run_id=run_id,
            doc=mock_doc,
            facts=facts,
            validation_results=val_results,
            summary={"facts_extracted": 3, "hard_rules_pass_rate": "100.0%"},
        )

        self.assertTrue((run_dir / "document.parquet").exists())
        self.assertTrue((run_dir / "facts.parquet").exists())
        self.assertTrue((run_dir / "validation_results.parquet").exists())
        self.assertTrue((run_dir / "run_summary.json").exists())

        # Test pending list
        pending = self.staging.list_pending_runs()
        self.assertIn(run_id, pending)

        # Mark completed
        self.staging.mark_completed(run_id)
        self.assertFalse((self.staging.pending_dir / run_id).exists())
        self.assertTrue((self.staging.completed_dir / run_id).exists())

    def test_duckdb_vectorized_ingestion_from_staging(self):
        """Verify DuckDB loads staging Parquet files via native read_parquet into Silver & Gold."""
        run_id = "test-run-002"
        mock_doc = MagicMock()
        mock_doc.doc_id = "doc_nckl_2025"
        mock_doc.ticker = "NCKL"
        mock_doc.doc_type = "fs_annual"
        mock_doc.fiscal_year = 2025
        mock_doc.fiscal_period = "FY"
        mock_doc.period_end = "2025-12-31"
        mock_doc.file_path = "examples/nckl/FinancialStatement-2025-Tahunan-NCKL.pdf"
        mock_doc.currency = "IDR"
        mock_doc.number_locale = "en"
        mock_doc.scale = 1_000_000
        mock_doc.page_count = 85
        mock_doc.has_text_layer = True

        facts = [
            self._create_dummy_fact("fact_assets", "fs.bs.total_assets", Decimal("61770103000000"), doc_id="doc_nckl_2025"),
            self._create_dummy_fact("fact_liab", "fs.bs.total_liabilities", Decimal("15014238000000"), doc_id="doc_nckl_2025"),
            self._create_dummy_fact("fact_eq", "fs.bs.total_equity", Decimal("46755865000000"), doc_id="doc_nckl_2025"),
            self._create_dummy_fact("fact_rev", "fs.is.revenue", Decimal("29633000000000"), doc_id="doc_nckl_2025"),
            self._create_dummy_fact("fact_np", "fs.is.profit_for_the_period", Decimal("10970000000000"), doc_id="doc_nckl_2025"),
        ]

        self.staging.save_run_staging(
            run_id=run_id,
            doc=mock_doc,
            facts=facts,
            validation_results=[],
        )

        db = DatabaseStore(db_path=self.db_path)
        stats = db.ingest_from_staging(run_id, staging_store=self.staging)

        self.assertIn("idxfin_silver.fact", stats["tables_ingested"])
        self.assertEqual(stats["records_ingested"]["facts"], 5)

        # Query DuckDB fact table
        fact_count = db.conn.execute("SELECT COUNT(*) FROM idxfin_silver.fact WHERE doc_id = 'doc_nckl_2025'").fetchone()[0]
        self.assertEqual(fact_count, 5)

        # Query Gold financial summary view
        gold_row = db.conn.execute("""
            SELECT issuer_id, fiscal_year, total_assets, total_liabilities, total_equity, revenue, net_profit
            FROM idxfin_gold.financial_summary
            WHERE issuer_id = 'NCKL'
        """).fetchone()
        self.assertIsNotNone(gold_row)
        self.assertEqual(gold_row[0], "NCKL")
        self.assertEqual(gold_row[1], 2025)
        self.assertEqual(float(gold_row[2]), 61770103000000.0)

        db.close()

    def test_celery_task_routing_and_backpressure_config(self):
        """Verify Celery queue routing and prefetch multiplier settings."""
        # 1. Concurrency
        self.assertEqual(celery_app.conf.worker_concurrency, 6)
        # 2. Backpressure: worker_prefetch_multiplier must be 1 so tasks stay in Redis
        self.assertEqual(celery_app.conf.worker_prefetch_multiplier, 1)
        # 3. Crash recovery: task_acks_late must be True
        self.assertTrue(celery_app.conf.task_acks_late)

        # 4. Route verification
        routes = celery_app.conf.task_routes
        self.assertEqual(routes.get("src.tasks.extract_document_task")["queue"], CELERY_QUEUE_EXTRACTION)
        self.assertEqual(routes.get("src.tasks.ingest_staging_task")["queue"], CELERY_QUEUE_DUCKDB)

    def test_fastapi_endpoints(self):
        """Verify FastAPI extraction enqueuing, polling, and gold querying."""
        client = TestClient(fastapi_app)

        # 1. Health check
        res = client.get("/health")
        self.assertEqual(res.status_code, 200)
        self.assertIn("status", res.json())

        # 2. Root check
        root_res = client.get("/")
        self.assertEqual(root_res.status_code, 200)
        self.assertIn("endpoints", root_res.json())

        # 3. Enqueue Financial Statement (LK)
        with patch("src.tasks.extract_document_task.apply_async") as mock_apply:
            mock_task = MagicMock()
            mock_task.id = "mock-task-fs-1234"
            mock_apply.return_value = mock_task

            post_res = client.post("/api/v1/extract/fs", json={
                "file_path": "examples/nckl/FinancialStatement-2025-Tahunan-NCKL.pdf",
                "auto_ingest": True,
            })
            self.assertEqual(post_res.status_code, 202)
            data = post_res.json()
            self.assertEqual(data["task_id"], "mock-task-fs-1234")
            self.assertEqual(data["status"], "QUEUED")
            self.assertEqual(data["doc_type"], "fs_annual")
            self.assertIn("/api/v1/tasks/mock-task-fs-1234", data["poll_url"])

        # 4. Enqueue Annual Report (AR)
        with patch("src.tasks.extract_document_task.apply_async") as mock_apply:
            mock_task = MagicMock()
            mock_task.id = "mock-task-ar-5678"
            mock_apply.return_value = mock_task

            post_res = client.post("/api/v1/extract/ar", json={
                "file_path": "examples/nckl/AnnualReport.pdf",
                "auto_ingest": True,
            })
            self.assertEqual(post_res.status_code, 202)
            data = post_res.json()
            self.assertEqual(data["task_id"], "mock-task-ar-5678")
            self.assertEqual(data["status"], "QUEUED")
            self.assertEqual(data["doc_type"], "annual_report")

        # 5. Missing file error 404
        bad_res = client.post("/api/v1/extract/fs", json={
            "file_path": "examples/non_existent.pdf"
        })
        self.assertEqual(bad_res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
