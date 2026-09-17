"""
Fact Normalizer: Transforms extraction contracts into normalized Silver facts (idxfin_silver.fact).
Computes deterministic fact_id, applies scale, standardizes dimensions, and preserves lineage.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from pydantic import BaseModel

import schema_design.extraction_contracts as contracts
from src.mapping.concept_mapper import ConceptMapper
from src.normalization.number_parser import apply_scale, parse_reported_number
from src.parser.pdf_loader import PDFDocument


class SilverFact(BaseModel):
    fact_id: str
    issuer_id: str
    reporting_entity: Optional[str] = None
    doc_id: str
    # What
    concept_id: Optional[str] = None
    label_raw: str
    label_raw_alt: Optional[str] = None
    note_ref: Optional[str] = None
    mapping_method: str = "taxonomy"
    mapping_confidence: float = 1.0
    # When
    period_type: str  # instant / duration
    period_start: Optional[str] = None
    period_end: str
    fiscal_year: int
    fiscal_period: str
    duration_months: Optional[int] = None
    is_cumulative_ytd: bool = True
    # Context
    scenario: str = "actual"
    dims: dict[str, str] = {}
    dim_key: str = ""
    basis: Optional[str] = None
    consolidation_scope: str = "consolidated"
    # Value
    value: Optional[Decimal] = None
    value_text: Optional[str] = None
    raw_text: str
    unit_id: str = "IDR"
    currency: Optional[str] = None
    scale_as_reported: int = 1_000_000
    # Trust
    assurance: str = "audited"
    is_restated_column: bool = False
    is_comparative: bool = False
    review_status: str = "auto"
    knowledge_ts: str
    ingested_at: str
    # Lineage
    lineage_page: int
    lineage_bbox: Optional[list[float]] = None
    lineage_extractor: str = "fs.statement"
    lineage_method: str = "llm"
    lineage_model: Optional[str] = None
    lineage_run_id: str = ""
    lineage_confidence: float = 1.0


def build_dim_key(dims: dict[str, str]) -> str:
    """Build canonical sorted dim_key: 'axis=member|axis=member'."""
    if not dims:
        return ""
    sorted_pairs = sorted(f"{k}={v}" for k, v in dims.items())
    return "|".join(sorted_pairs)


def make_fact_id(doc_id: str, concept_or_label: str, period_end: str, dim_key: str, scenario: str, raw_text: str) -> str:
    """Deterministic hash for silver fact_id."""
    raw = f"{doc_id}|{concept_or_label}|{period_end}|{dim_key}|{scenario}|{raw_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class Normalizer:
    def __init__(
        self,
        mapper: Optional[ConceptMapper] = None,
        llm_client: Optional[Any] = None,
        db_conn: Optional[Any] = None,
    ):
        self.mapper = mapper or ConceptMapper()
        self.llm_client = llm_client
        self.db_conn = db_conn
        if self.db_conn:
            self.mapper.load_persisted_mappings(self.db_conn)

    def normalize_statement(
        self,
        doc: PDFDocument,
        extraction: contracts.StatementExtraction,
        run_id: str,
        model: Optional[str] = None,
    ) -> list[SilverFact]:
        """Normalize a StatementExtraction into SilverFact objects."""
        facts: list[SilverFact] = []
        now_ts = datetime.utcnow().isoformat()
        doc_period_end = doc.period_end or "2025-12-31"

        for li in extraction.line_items:
            # Determine canonical concept ID: Dictionary first, then LLM candidate, then extension
            concept_id = None
            mapping_method = "taxonomy"
            mapping_conf = 1.0

            # 1. Exact canonical label mapping
            alt_mapped = self.mapper.map_label(li.label_raw_alt_lang) if getattr(li, "label_raw_alt_lang", None) else None
            mapped = self.mapper.map_label(li.label_raw) or alt_mapped
            if mapped:
                concept_id = mapped
                mapping_method = "label_mapping"
                mapping_conf = 1.0
            elif li.concept_candidates:
                concept_id = li.concept_candidates[0].concept_id
                mapping_conf = li.concept_candidates[0].score
                mapping_method = "llm_candidate"
            else:
                # LLM-first concept resolution with few-shot demonstrations and DuckDB persistence
                resolved_cid, conf, method = self.mapper.resolve_unmapped_with_llm(
                    label_raw=li.label_raw,
                    label_raw_alt=li.label_raw_alt_lang,
                    statement_type=str(extraction.statement_type),
                    context=f"{doc.ticker} {extraction.statement_type} filing",
                    llm_client=self.llm_client,
                    db_conn=self.db_conn,
                )
                concept_id = resolved_cid
                mapping_conf = conf
                mapping_method = method

            for cell in li.cells:
                if cell.period_index >= len(extraction.periods):
                    continue
                # Skip empty cell text (header rows)
                raw_cell = cell.raw_text.strip() if cell.raw_text else ""
                if not raw_cell:
                    continue

                period_ref = extraction.periods[cell.period_index]

                # Parse number
                parsed_num = parse_reported_number(cell.raw_text, doc.number_locale)
                scaled_val = apply_scale(parsed_num, extraction.context.scale)

                # Merge row and col dims
                dims_dict: dict[str, str] = {}
                for d in li.row_dims:
                    dims_dict[d.axis] = d.member or d.member_raw
                for d in cell.col_dims:
                    dims_dict[d.axis] = d.member or d.member_raw
                d_key = build_dim_key(dims_dict)

                period_end_str = str(period_ref.end_date)
                is_comp = period_end_str < doc_period_end

                fid = make_fact_id(
                    doc_id=doc.doc_id,
                    concept_or_label=concept_id or li.label_raw,
                    period_end=period_end_str,
                    dim_key=d_key,
                    scenario="actual",
                    raw_text=cell.raw_text,
                )

                fact = SilverFact(
                    fact_id=fid,
                    issuer_id=doc.ticker or "EMTN",
                    doc_id=doc.doc_id,
                    concept_id=concept_id,
                    label_raw=li.label_raw,
                    label_raw_alt=li.label_raw_alt_lang,
                    note_ref=li.note_ref,
                    mapping_method=mapping_method,
                    mapping_confidence=mapping_conf,
                    period_type=str(period_ref.period_type),
                    period_start=str(period_ref.start_date) if period_ref.start_date else None,
                    period_end=period_end_str,
                    fiscal_year=period_ref.fiscal_year,
                    fiscal_period=str(period_ref.fiscal_period),
                    duration_months=12 if period_ref.fiscal_period == contracts.FiscalPeriod.FY else 3,
                    is_cumulative_ytd=period_ref.is_cumulative_ytd,
                    scenario="actual",
                    dims=dims_dict,
                    dim_key=d_key,
                    consolidation_scope=extraction.context.consolidation_scope,
                    value=scaled_val,
                    raw_text=cell.raw_text,
                    unit_id=extraction.context.currency,
                    currency=extraction.context.currency,
                    scale_as_reported=extraction.context.scale,
                    assurance=str(period_ref.assurance) if period_ref.assurance else "audited",
                    is_restated_column=period_ref.is_restated,
                    is_comparative=is_comp,
                    review_status="auto",
                    knowledge_ts=now_ts,
                    ingested_at=now_ts,
                    lineage_page=cell.source.page,
                    lineage_bbox=cell.source.bbox,
                    lineage_extractor="fs.statement",
                    lineage_method="llm",
                    lineage_model=model,
                    lineage_run_id=run_id,
                    lineage_confidence=1.0,
                )
                facts.append(fact)

        return facts

    def normalize_kpis(
        self,
        doc: PDFDocument,
        kpis: list[contracts.KPIFact],
        run_id: str,
        model: Optional[str] = None,
    ) -> list[SilverFact]:
        """Normalize operational KPIFacts into SilverFact objects."""
        facts: list[SilverFact] = []
        now_ts = datetime.now(timezone.utc).isoformat()

        for k in kpis:
            concept_id = None
            mapping_method = "taxonomy"
            mapping_conf = 1.0

            if k.kpi_candidates:
                concept_id = k.kpi_candidates[0].concept_id
                mapping_conf = k.kpi_candidates[0].score
                mapping_method = "llm_candidate"
            else:
                mapped = self.mapper.map_label(k.kpi_label_raw)
                kpi_slug = re.sub(r"[^\w]", "_", k.kpi_label_raw.lower())[:25]
                concept_id = mapped or f"op.ext.{doc.ticker or 'EMTN'}.{kpi_slug}"

            parsed_num = parse_reported_number(k.raw_text, doc.number_locale)

            dims_dict = {}
            for d in k.dims:
                dims_dict[d.axis] = d.member or d.member_raw
            d_key = build_dim_key(dims_dict)

            period_end_str = str(k.period.end_date)
            fid = make_fact_id(
                doc_id=doc.doc_id,
                concept_or_label=concept_id or k.kpi_label_raw,
                period_end=period_end_str,
                dim_key=d_key,
                scenario=str(k.scenario),
                raw_text=k.raw_text,
            )

            fact = SilverFact(
                fact_id=fid,
                issuer_id=doc.ticker or "EMTN",
                doc_id=doc.doc_id,
                concept_id=concept_id,
                label_raw=k.kpi_label_raw,
                mapping_method=mapping_method,
                mapping_confidence=mapping_conf,
                period_type=str(k.period.period_type),
                period_start=str(k.period.start_date) if k.period.start_date else None,
                period_end=period_end_str,
                fiscal_year=k.period.fiscal_year,
                fiscal_period=str(k.period.fiscal_period),
                duration_months=12 if k.period.fiscal_period == contracts.FiscalPeriod.FY else 3,
                is_cumulative_ytd=k.period.is_cumulative_ytd,
                scenario=str(k.scenario),
                dims=dims_dict,
                dim_key=d_key,
                basis=k.basis,
                value=parsed_num,
                raw_text=k.raw_text,
                unit_id=k.unit_raw,
                currency=doc.currency if "idr" in k.unit_raw.lower() or "usd" in k.unit_raw.lower() else None,
                scale_as_reported=1,  # KPIs typically reported in full units (e.g. WMT, tonnes)
                assurance="management_narrative",
                review_status="auto",
                knowledge_ts=now_ts,
                ingested_at=now_ts,
                lineage_page=k.source.page,
                lineage_bbox=k.source.bbox,
                lineage_extractor="ar.mdna.kpis",
                lineage_method="llm",
                lineage_model=model,
                lineage_run_id=run_id,
                lineage_confidence=1.0,
            )
            facts.append(fact)

        return facts
