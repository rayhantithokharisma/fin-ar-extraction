"""
Assertions Extractor: Extracts Forward Guidance, Outlook, and Driver Attributions.
"""
from __future__ import annotations

import logging
from typing import Optional
from pydantic import BaseModel

import schema_design.extraction_contracts as contracts
from src.llm.client import OpenRouterClient
from src.llm.prompts import FORWARD_GUIDANCE_PROMPT, SYSTEM_FINANCIAL_ANALYST
from src.parser.pdf_loader import PDFDocument
from src.parser.sectionizer import SectionSpan

logger = logging.getLogger(__name__)


class GuidanceExtractionResult(BaseModel):
    items: list[contracts.ForwardGuidance]


class AssertionsExtractor:
    def __init__(self, client: Optional[OpenRouterClient] = None):
        self.client = client or OpenRouterClient()

    def extract_guidance(self, doc: PDFDocument, span: SectionSpan, model: Optional[str] = None) -> list[contracts.ForwardGuidance]:
        logger.info(f"Extracting Forward Guidance from {span.section_id} (pages {span.start_page} to {span.end_page})...")
        page_text = doc.get_text_range(span.start_page, span.end_page)

        prompt = FORWARD_GUIDANCE_PROMPT.format(
            ticker=doc.ticker or "NCKL",
            entity_name=doc.legal_name or "PT Trimegah Bangun Persada Tbk",
            fiscal_year=doc.fiscal_year or 2025,
            start_page=span.start_page,
            end_page=span.end_page,
            page_text=page_text[:14000],
        )

        messages = [
            {"role": "system", "content": SYSTEM_FINANCIAL_ANALYST},
            {"role": "user", "content": prompt},
        ]

        result, usage = self.client.generate_structured(
            messages=messages,
            response_model=GuidanceExtractionResult,
            model=model,
        )
        logger.info(f"Extracted {len(result.items)} forward guidance items. Tokens: {usage.get('total_tokens', 0)}")
        return result.items
