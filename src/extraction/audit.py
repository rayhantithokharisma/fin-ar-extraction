"""
Audit Report Extractor: Extracts KAP, signing partner, opinion, and KAMs from auditor report.
"""
from __future__ import annotations

import logging
from typing import Optional

import schema_design.extraction_contracts as contracts
from src.llm.client import OpenRouterClient
from src.llm.prompts import AUDIT_REPORT_PROMPT, SYSTEM_FINANCIAL_ANALYST
from src.parser.pdf_loader import PDFDocument
from src.parser.sectionizer import SectionSpan

logger = logging.getLogger(__name__)


class AuditExtractor:
    def __init__(self, client: Optional[OpenRouterClient] = None):
        self.client = client or OpenRouterClient()

    def extract_audit_report(self, doc: PDFDocument, span: SectionSpan, model: Optional[str] = None) -> Optional[contracts.AuditReport]:
        logger.info(f"Extracting Audit Report from {span.section_id} (pages {span.start_page} to {span.end_page})...")
        page_text = doc.get_text_range(span.start_page, min(span.start_page + 3, span.end_page))

        prompt = AUDIT_REPORT_PROMPT.format(
            ticker=doc.ticker or "EMTN",
            entity_name=doc.legal_name or "Issuer",
            fiscal_year=doc.fiscal_year or 2025,
            start_page=span.start_page,
            end_page=span.end_page,
            page_text=page_text[:30000],
        )

        messages = [
            {"role": "system", "content": SYSTEM_FINANCIAL_ANALYST},
            {"role": "user", "content": prompt},
        ]

        logger.info(f"Calling LLM for {span.section_id} with schema AuditReport...")
        report, usage = self.client.generate_structured(
            messages=messages,
            response_model=contracts.AuditReport,
            model=model,
            max_tokens=32768,
        )
        logger.info(f"Successfully extracted Audit Report for {report.audit_firm_raw}. Opinion: {report.opinion}")
        return report
