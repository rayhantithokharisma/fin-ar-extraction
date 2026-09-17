"""
End-to-End Extraction Pipeline Orchestrator.
Ingest -> Sectionize -> Extract -> Normalize -> Validate -> Parquet Staging -> (Optional) DuckDB Ingest.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from src.extraction.router import ExtractionRouter
from src.mapping.concept_mapper import ConceptMapper
from src.normalization.normalizer import Normalizer, SilverFact
from src.parser.pdf_loader import PDFDocument
from src.parser.sectionizer import SectionSpan, Sectionizer
from src.storage.database import DatabaseStore
from src.storage.staging import ParquetStagingStore
from src.validation.engine import ValidationEngine, ValidationRuleResult
from src.validation.repair import RepairLoop

logger = logging.getLogger(__name__)


class ExtractionPipeline:
    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        staging_only: bool = False,
        staging_dir: Optional[str | Path] = None,
    ):
        self.staging_only = staging_only
        self.mapper = ConceptMapper()
        self.router = ExtractionRouter()
        self.normalizer = Normalizer(mapper=self.mapper)
        self.validator = ValidationEngine()
        self.repair = RepairLoop()
        self.staging = ParquetStagingStore(base_dir=staging_dir) if staging_dir else ParquetStagingStore()

        if not self.staging_only:
            self.db = DatabaseStore(db_path) if db_path else DatabaseStore()
            self.db.seed_taxonomy(self.mapper)
        else:
            self.db = None

    def run(
        self,
        file_path: str | Path,
        target_sections: Optional[list[str]] = None,
        model: Optional[str] = None,
        progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        """Execute end-to-end extraction pipeline on a PDF filing."""
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        logger.info(f"Starting extraction run {run_id} on {file_path} (staging_only={self.staging_only})")

        # 1. Ingest PDF & Extract Text
        doc = PDFDocument(file_path)

        # 2. Sectionize
        sectionizer = Sectionizer(doc)
        spans = sectionizer.sectionize()
        logger.info(f"Detected {len(spans)} section spans in document.")

        if progress_callback:
            progress_callback({
                "status": "SECTIONIZED",
                "run_id": run_id,
                "sections_total": len(spans),
                "sections_completed": 0,
                "percent": 10,
            })

        # 3. Extract via Router
        raw_extractions = self.router.route_and_extract(
            doc=doc,
            spans=spans,
            model=model,
            target_sections=target_sections,
        )

        if progress_callback:
            progress_callback({
                "status": "EXTRACTED",
                "run_id": run_id,
                "percent": 70,
            })

        # 4. Normalize to Silver Facts
        all_facts: list[SilverFact] = []
        for stmt in raw_extractions.get("statements", []):
            facts = self.normalizer.normalize_statement(doc=doc, extraction=stmt, run_id=run_id, model=model)
            all_facts.extend(facts)

        kpis = raw_extractions.get("kpis", [])
        if kpis:
            kpi_facts = self.normalizer.normalize_kpis(doc=doc, kpis=kpis, run_id=run_id, model=model)
            all_facts.extend(kpi_facts)

        officers = raw_extractions.get("officers", [])
        guidance = raw_extractions.get("guidance", [])
        audit_reports = raw_extractions.get("audit_reports", [])

        # 5. Validate Facts & Repair Loop
        val_results = self.validator.validate_facts(facts=all_facts, doc=doc)
        all_facts, review_tasks = self.repair.repair_failed_rules(val_results, all_facts, doc)

        hard_rules_passed = sum(1 for r in val_results if r.severity == "hard" and r.passed)
        hard_rules_total = sum(1 for r in val_results if r.severity == "hard")
        pass_rate = (hard_rules_passed / hard_rules_total * 100.0) if hard_rules_total > 0 else 100.0

        summary = {
            "run_id": run_id,
            "doc_id": doc.doc_id,
            "ticker": doc.ticker,
            "doc_type": doc.doc_type,
            "period": f"{doc.fiscal_period}{doc.fiscal_year}",
            "period_end": str(doc.period_end) if doc.period_end else None,
            "pages": doc.page_count,
            "facts_extracted": len(all_facts),
            "kpis_extracted": len(kpis),
            "officers_extracted": len(officers),
            "guidance_extracted": len(guidance),
            "audit_reports_extracted": len(audit_reports),
            "validation_checks": len(val_results),
            "hard_rules_pass_rate": f"{pass_rate:.1f}%",
            "review_tasks_created": len(review_tasks),
        }

        # 6. Save intermediate artifacts to Parquet staging
        staged_dir = self.staging.save_run_staging(
            run_id=run_id,
            doc=doc,
            facts=all_facts,
            validation_results=val_results,
            review_tasks=review_tasks,
            officers=officers,
            guidance=guidance,
            audit_reports=audit_reports,
            summary=summary,
        )

        # 7. Ingest into DuckDB if not in staging-only mode
        if not self.staging_only and self.db:
            self.db.ingest_from_staging(run_id, self.staging)

        doc.close()
        logger.info(f"Pipeline run {run_id} completed: {summary}")

        if progress_callback:
            progress_callback({
                "status": "COMPLETED",
                "run_id": run_id,
                "percent": 100,
                "summary": summary,
            })

        return {
            "run_id": run_id,
            "staged_dir": str(staged_dir),
            "summary": summary,
            "facts": all_facts,
            "validation_results": val_results,
            "review_tasks": review_tasks,
        }
