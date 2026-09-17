"""
Statement Extractor: Extracts Balance Sheet, Income Statement, Cash Flows, and Changes in Equity
into the StatementExtraction Pydantic v2 contract.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import schema_design.extraction_contracts as contracts
from src.llm.client import OpenRouterClient
from src.llm.prompts import STATEMENT_EXTRACTION_PROMPT, SYSTEM_FINANCIAL_ANALYST
from src.mapping.concept_mapper import ConceptMapper
from src.parser.pdf_loader import PDFDocument
from src.parser.sectionizer import SectionSpan

logger = logging.getLogger(__name__)


class StatementExtractor:
    def __init__(self, client: Optional[OpenRouterClient] = None, mapper: Optional[ConceptMapper] = None):
        self.client = client or OpenRouterClient()
        self.mapper = mapper or ConceptMapper()

    def extract(self, doc: PDFDocument, span: SectionSpan, model: Optional[str] = None) -> contracts.StatementExtraction:
        """Extract a financial statement from the given document and section span."""
        logger.info(f"Extracting {span.section_id} (pages {span.start_page} to {span.end_page})...")
        page_text = doc.get_text_range(span.start_page, span.end_page)

        # Get relevant concept candidates from taxonomy
        candidates = self.mapper.get_candidates_for_statement(span.topic or "balance_sheet", limit=100)
        candidates_json = json.dumps(candidates, indent=2)

        # Map topic to statement_type code
        stmt_type_code = "balance_sheet"
        if span.topic in ("income_statement", "comprehensive_income"):
            stmt_type_code = "income_statement"
        elif span.topic == "cash_flow":
            stmt_type_code = "cash_flow"
        elif span.topic == "changes_in_equity":
            stmt_type_code = "changes_in_equity"

        prompt = STATEMENT_EXTRACTION_PROMPT.format(
            ticker=doc.ticker or "EMTN",
            entity_name=doc.legal_name or "Issuer",
            statement_type=span.title,
            statement_type_code=stmt_type_code,
            fiscal_period=doc.fiscal_period,
            fiscal_year=doc.fiscal_year or 2025,
            period_end=doc.period_end or "2025-12-31",
            currency=doc.currency,
            scale_raw=doc.scale_raw,
            scale=doc.scale,
            number_locale=doc.number_locale,
            start_page=span.start_page,
            end_page=span.end_page,
            candidate_concepts_json=candidates_json,
            page_text=page_text[:80000],  # Fits full multi-page face statements
        )

        messages = [
            {"role": "system", "content": SYSTEM_FINANCIAL_ANALYST},
            {"role": "user", "content": prompt},
        ]

        logger.info(f"Calling LLM for {span.section_id} with schema StatementExtraction...")
        extraction, usage = self.client.generate_structured(
            messages=messages,
            response_model=contracts.StatementExtraction,
            model=model,
            max_tokens=32768,
        )
        logger.info(f"Successfully extracted {len(extraction.line_items)} line items. Tokens used: {usage.get('total_tokens', 0)}")
        return extraction
