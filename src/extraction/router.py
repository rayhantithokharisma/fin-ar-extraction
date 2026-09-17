"""
Extraction Router: Dispatches sections to specialized extractors based on ROUTES.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.extraction.assertions import AssertionsExtractor
from src.extraction.audit import AuditExtractor
from src.extraction.governance import GovernanceExtractor
from src.extraction.kpis import KPIExtractor
from src.extraction.statements import StatementExtractor
from src.parser.pdf_loader import PDFDocument
from src.parser.sectionizer import SectionSpan

logger = logging.getLogger(__name__)


class ExtractionRouter:
    def __init__(self):
        self.statement_extractor = StatementExtractor()
        self.kpi_extractor = KPIExtractor()
        self.governance_extractor = GovernanceExtractor()
        self.assertions_extractor = AssertionsExtractor()
        self.audit_extractor = AuditExtractor()

    def route_and_extract(
        self,
        doc: PDFDocument,
        spans: list[SectionSpan],
        model: Optional[str] = None,
        target_sections: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Execute extraction across target section spans.
        Returns a dict of extracted objects keyed by section_id.
        """
        results: dict[str, Any] = {
            "statements": [],
            "kpis": [],
            "officers": [],
            "guidance": [],
            "audit_reports": [],
        }

        for span in spans:
            if target_sections and not any(t in span.section_id or t in span.section_type for t in target_sections):
                continue

            logger.info(f"Routing section {span.section_id} (type: {span.section_type})...")
            try:
                if span.section_type in ("lk.statement", "lk.note.table", "lk.note.borrowings"):
                    stmt = self.statement_extractor.extract(doc, span, model=model)
                    results["statements"].append(stmt)
                elif span.section_type in ("ar.mdna.kpis", "sr.esg_metrics", "deck.kpis"):
                    kpis = self.kpi_extractor.extract(doc, span, model=model)
                    results["kpis"].extend(kpis)
                elif span.section_type == "ar.governance.officers":
                    officers = self.governance_extractor.extract_officers(doc, span, model=model)
                    results["officers"].extend(officers)
                elif span.section_type in ("ar.mdna.outlook", "deck.guidance"):
                    guidance = self.assertions_extractor.extract_guidance(doc, span, model=model)
                    results["guidance"].extend(guidance)
                elif span.section_type == "lk.auditor_report":
                    audit = self.audit_extractor.extract_audit_report(doc, span, model=model)
                    if audit:
                        results["audit_reports"].append(audit)
            except Exception as e:
                logger.error(f"Error extracting section {span.section_id}: {e}", exc_info=True)
                err_msg = str(e).lower()
                if "openrouter api failed" in err_msg or "402" in err_msg or "403" in err_msg or "rate limit" in err_msg:
                    raise RuntimeError(f"Extraction halted on {span.section_id} due to API limit/failure: {e}") from e

        return results
