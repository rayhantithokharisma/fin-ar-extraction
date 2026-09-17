"""
Parquet Staging Store for Intermediate Extraction Artifacts.
Decouples concurrent worker execution from embedded DuckDB single-writer file locking.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import schema_design.extraction_contracts as contracts
from src.config import STAGING_DIR
from src.normalization.normalizer import SilverFact
from src.parser.pdf_loader import PDFDocument
from src.validation.engine import ValidationRuleResult
from src.validation.repair import ReviewTask

logger = logging.getLogger(__name__)


class ParquetStagingStore:
    """Manages writing and reading intermediate Parquet datasets per extraction run."""

    def __init__(self, base_dir: Path | str = STAGING_DIR):
        self.base_dir = Path(base_dir)
        self.pending_dir = self.base_dir / "pending"
        self.completed_dir = self.base_dir / "completed"
        self.failed_dir = self.base_dir / "failed"

        self.pending_dir.mkdir(parents=True, exist_ok=True)
        self.completed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    def get_run_dir(self, run_id: str, status: str = "pending") -> Path:
        if status == "completed":
            return self.completed_dir / run_id
        elif status == "failed":
            return self.failed_dir / run_id
        return self.pending_dir / run_id

    def save_run_staging(
        self,
        run_id: str,
        doc: PDFDocument,
        facts: list[SilverFact],
        validation_results: list[ValidationRuleResult],
        review_tasks: Optional[list[ReviewTask]] = None,
        officers: Optional[list[contracts.OfficerPosition]] = None,
        guidance: Optional[list[contracts.ForwardGuidance]] = None,
        audit_reports: Optional[list[contracts.AuditReport]] = None,
        summary: Optional[dict[str, Any]] = None,
    ) -> Path:
        """Write all extracted Bronze and Silver entities to isolated Parquet files."""
        run_dir = self.get_run_dir(run_id, status="pending")
        run_dir.mkdir(parents=True, exist_ok=True)

        # 1. Document record (Bronze)
        doc_rows = [{
            "doc_id": doc.doc_id,
            "issuer_id": doc.ticker or "EMTN",
            "doc_type": doc.doc_type,
            "fiscal_year": int(doc.fiscal_year) if doc.fiscal_year else None,
            "fiscal_period": doc.fiscal_period,
            "period_end": str(doc.period_end) if doc.period_end else None,
            "storage_path": str(doc.file_path),
            "currency": doc.currency,
            "number_locale": doc.number_locale,
            "scale": int(doc.scale) if doc.scale else 1,
            "page_count": int(doc.page_count),
            "has_text_layer": bool(doc.has_text_layer),
            "ingested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }]
        pd.DataFrame(doc_rows).to_parquet(run_dir / "document.parquet", index=False)

        # 2. Facts (Silver)
        if facts:
            fact_rows = []
            for f in facts:
                val_str = str(f.value) if f.value is not None else None
                fact_rows.append({
                    "fact_id": f.fact_id,
                    "issuer_id": f.issuer_id,
                    "reporting_entity": f.reporting_entity,
                    "doc_id": f.doc_id,
                    "concept_id": f.concept_id,
                    "label_raw": f.label_raw,
                    "label_raw_alt": f.label_raw_alt,
                    "note_ref": f.note_ref,
                    "mapping_method": f.mapping_method,
                    "mapping_confidence": float(f.mapping_confidence),
                    "period_type": f.period_type,
                    "period_start": str(f.period_start) if f.period_start else None,
                    "period_end": str(f.period_end),
                    "fiscal_year": int(f.fiscal_year),
                    "fiscal_period": f.fiscal_period,
                    "duration_months": int(f.duration_months) if f.duration_months else None,
                    "is_cumulative_ytd": bool(f.is_cumulative_ytd),
                    "scenario": f.scenario,
                    "dim_key": f.dim_key or "",
                    "basis": f.basis,
                    "consolidation_scope": f.consolidation_scope,
                    "value": val_str,
                    "raw_text": f.raw_text,
                    "unit_id": f.unit_id,
                    "currency": f.currency,
                    "scale_as_reported": int(f.scale_as_reported),
                    "assurance": f.assurance,
                    "is_restated_column": bool(f.is_restated_column),
                    "is_comparative": bool(f.is_comparative),
                    "review_status": f.review_status,
                    "knowledge_ts": str(f.knowledge_ts),
                    "ingested_at": str(f.ingested_at),
                    "lineage_page": int(f.lineage_page),
                    "lineage_extractor": f.lineage_extractor,
                    "lineage_method": f.lineage_method,
                    "lineage_model": f.lineage_model or "",
                    "lineage_run_id": f.lineage_run_id or run_id,
                    "lineage_confidence": float(f.lineage_confidence),
                })
            pd.DataFrame(fact_rows).to_parquet(run_dir / "facts.parquet", index=False)

        # 3. Officers (Silver)
        if officers:
            officer_rows = []
            for o in officers:
                officer_rows.append({
                    "issuer_id": doc.ticker or "EMTN",
                    "doc_id": doc.doc_id,
                    "person_name": o.person_name_raw,
                    "role": str(o.role),
                    "is_independent": bool(o.is_independent),
                    "source_page": int(o.source.page),
                })
            pd.DataFrame(officer_rows).to_parquet(run_dir / "officers.parquet", index=False)

        # 4. Guidance (Silver)
        if guidance:
            guidance_rows = []
            for g in guidance:
                gid = f"guid.{doc.doc_id[:8]}.{abs(hash(g.metric_label_raw))}"
                guidance_rows.append({
                    "guidance_id": gid,
                    "issuer_id": doc.ticker or "EMTN",
                    "doc_id": doc.doc_id,
                    "metric_label_raw": g.metric_label_raw,
                    "scenario": str(g.scenario),
                    "point_raw": g.point_raw,
                    "low_raw": g.low_raw,
                    "high_raw": g.high_raw,
                    "unit_raw": g.unit_raw,
                    "target_period_raw": g.target_period_raw,
                    "target_fiscal_year": int(g.target_fiscal_year) if g.target_fiscal_year else None,
                    "source_page": int(g.source.page),
                })
            pd.DataFrame(guidance_rows).to_parquet(run_dir / "guidance.parquet", index=False)

        # 5. Audit Reports (Silver)
        if audit_reports:
            audit_rows = []
            for audit in audit_reports:
                kams = "; ".join(audit.key_audit_matters) if audit.key_audit_matters else ""
                audit_rows.append({
                    "doc_id": doc.doc_id,
                    "issuer_id": doc.ticker or "EMTN",
                    "fiscal_year": int(doc.fiscal_year) if doc.fiscal_year else None,
                    "audit_firm": audit.audit_firm_raw,
                    "network": audit.network_raw,
                    "signing_partner": audit.signing_partner_raw,
                    "report_date": str(audit.report_date) if audit.report_date else None,
                    "opinion": str(audit.opinion),
                    "emphasis_of_matter": bool(audit.emphasis_of_matter),
                    "material_uncertainty_going_concern": bool(audit.material_uncertainty_going_concern),
                    "key_audit_matters": kams,
                    "source_page": int(audit.source.page),
                })
            pd.DataFrame(audit_rows).to_parquet(run_dir / "audit_reports.parquet", index=False)

        # 6. Validation Results (Silver)
        if validation_results:
            val_rows = []
            for r in validation_results:
                val_rows.append({
                    "run_id": run_id,
                    "doc_id": doc.doc_id,
                    "rule_id": r.rule_id,
                    "passed": bool(r.passed),
                    "lhs": str(r.lhs) if r.lhs is not None else None,
                    "rhs": str(r.rhs) if r.rhs is not None else None,
                    "diff": str(r.diff) if r.diff is not None else None,
                    "tolerance": str(r.tolerance) if r.tolerance is not None else None,
                    "implicated_fact_ids": ",".join(r.implicated_fact_ids),
                    "message": r.message,
                    "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
            pd.DataFrame(val_rows).to_parquet(run_dir / "validation_results.parquet", index=False)

        # 7. Review Tasks (Silver)
        if review_tasks:
            task_rows = []
            for t in review_tasks:
                task_rows.append({
                    "task_id": t.task_id,
                    "doc_id": t.doc_id,
                    "target_ids": ",".join(t.target_ids),
                    "reason": t.reason,
                    "priority": float(t.priority),
                    "status": t.status,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
            pd.DataFrame(task_rows).to_parquet(run_dir / "review_tasks.parquet", index=False)

        # 8. Summary JSON metadata
        summary_payload = summary or {}
        summary_payload["run_id"] = run_id
        summary_payload["doc_id"] = doc.doc_id
        summary_payload["ticker"] = doc.ticker
        summary_payload["timestamp"] = datetime.now().isoformat()
        with open(run_dir / "run_summary.json", "w") as f:
            json.dump(summary_payload, f, indent=2)

        logger.info(f"Successfully staged extraction run {run_id} to Parquet at {run_dir}")
        return run_dir

    def mark_completed(self, run_id: str) -> None:
        """Move run folder from pending to completed."""
        src = self.pending_dir / run_id
        dest = self.completed_dir / run_id
        if src.exists():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(src), str(dest))
            logger.info(f"Marked run {run_id} as completed in staging.")

    def mark_failed(self, run_id: str) -> None:
        """Move run folder from pending to failed."""
        src = self.pending_dir / run_id
        dest = self.failed_dir / run_id
        if src.exists():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(src), str(dest))
            logger.warning(f"Marked run {run_id} as failed in staging.")

    def list_pending_runs(self) -> list[str]:
        """List all pending run IDs awaiting DuckDB ingestion."""
        if not self.pending_dir.exists():
            return []
        return [p.name for p in self.pending_dir.iterdir() if p.is_dir() and (p / "document.parquet").exists()]
