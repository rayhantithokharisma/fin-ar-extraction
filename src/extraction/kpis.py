"""
Operational KPI Extractor: Extracts production volumes, ore grades, smelter output,
capacity, utilization, and unit costs from MD&A / Annual Reports into KPIFact contracts.
"""
from __future__ import annotations

import json
import logging
from typing import Optional
from pydantic import BaseModel

import schema_design.extraction_contracts as contracts
from src.llm.client import OpenRouterClient
from src.llm.prompts import KPI_EXTRACTION_PROMPT, SYSTEM_FINANCIAL_ANALYST
from src.mapping.concept_mapper import ConceptMapper
from src.parser.pdf_loader import PDFDocument
from src.parser.sectionizer import SectionSpan

logger = logging.getLogger(__name__)


class KPIExtractionResult(BaseModel):
    items: list[contracts.KPIFact]


class KPIExtractor:
    def __init__(self, client: Optional[OpenRouterClient] = None, mapper: Optional[ConceptMapper] = None):
        self.client = client or OpenRouterClient()
        self.mapper = mapper or ConceptMapper()

    def extract(self, doc: PDFDocument, span: SectionSpan, model: Optional[str] = None) -> list[contracts.KPIFact]:
        target_end = min(span.end_page, span.start_page + 8)
        logger.info(f"Extracting KPIs from {span.section_id} (pages {span.start_page} to {target_end})...")
        page_text = doc.get_text_range(span.start_page, target_end)

        # Multi-sector operational KPI candidates across all registered industry packs
        candidates = self.mapper.get_candidates_for_kpis(packs=None)
        candidates_json = json.dumps(candidates, separators=(',', ':'))

        ticker = doc.ticker or "IDX"
        entity_name = doc.legal_name or (f"PT {doc.ticker} Tbk" if doc.ticker else "IDX Corporate Issuer")
        fiscal_year = doc.fiscal_year or 2025
        period_end = doc.period_end or f"{fiscal_year}-12-31"

        prompt = KPI_EXTRACTION_PROMPT.format(
            ticker=ticker,
            entity_name=entity_name,
            fiscal_period=doc.fiscal_period or "FY",
            fiscal_year=fiscal_year,
            period_end=period_end,
            start_page=span.start_page,
            end_page=target_end,
            candidate_kpis_json=candidates_json,
            page_text=page_text[:25000],
        )

        messages = [
            {"role": "system", "content": SYSTEM_FINANCIAL_ANALYST},
            {"role": "user", "content": prompt},
        ]

        logger.info(f"Calling LLM for operational KPIs in {span.section_id}...")
        result, usage = self.client.generate_structured(
            messages=messages,
            response_model=KPIExtractionResult,
            model=model,
            max_tokens=32768,
        )
        logger.info(f"Extracted {len(result.items)} operational KPI facts. Tokens: {usage.get('total_tokens', 0)}")
        return result.items

