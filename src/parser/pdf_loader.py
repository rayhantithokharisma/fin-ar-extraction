"""
PDF Document Loader and Layout Intelligence using PyMuPDF (fitz).
Extracts text, bounding boxes, tables, and detects core filing metadata (currency, scale, locale, doc_type).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import fitz  # PyMuPDF


@dataclass
class TextBlock:
    bbox: list[float]  # [x0, y0, x1, y1]
    text: str
    block_no: int


@dataclass
class PDFPage:
    page_no: int  # 1-based
    text: str
    blocks: list[TextBlock] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0


class PDFDocument:
    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"PDF not found: {self.file_path}")

        with open(self.file_path, "rb") as f:
            content = f.read()
            self.doc_id = hashlib.sha256(content).hexdigest()

        self._fitz_doc = fitz.open(self.file_path)
        self.page_count = len(self._fitz_doc)
        self.metadata = self._fitz_doc.metadata or {}
        self.pages: list[PDFPage] = []
        self._load_pages()

        # Metadata detected from content
        self.doc_type: str = self._detect_doc_type()
        self.ticker: Optional[str] = None
        self.legal_name: Optional[str] = None
        self.fiscal_year: Optional[int] = None
        self.fiscal_period: str = "FY"
        self.period_end: Optional[str] = None
        self.currency: str = "IDR"
        self.scale: int = 1_000_000
        self.scale_raw: str = "Dalam jutaan Rupiah"
        self.number_locale: str = "id"
        self.presentation_format: str = "classified"
        self.accounting_regime: str = "general_corporate"
        self._detect_filing_metadata()

    def _load_pages(self) -> None:
        has_text = False
        for idx in range(self.page_count):
            page = self._fitz_doc[idx]
            page_text = page.get_text("text")
            if page_text.strip():
                has_text = True

            blocks: list[TextBlock] = []
            raw_blocks = page.get_text("blocks")
            for b_idx, b in enumerate(raw_blocks):
                # b is (x0, y0, x1, y1, text, block_no, block_type)
                if len(b) >= 5 and b[4].strip():
                    blocks.append(
                        TextBlock(
                            bbox=[float(b[0]), float(b[1]), float(b[2]), float(b[3])],
                            text=b[4].strip(),
                            block_no=b_idx,
                        )
                    )

            self.pages.append(
                PDFPage(
                    page_no=idx + 1,
                    text=page_text,
                    blocks=blocks,
                    width=page.rect.width,
                    height=page.rect.height,
                )
            )
        self.has_text_layer = has_text

    def get_page(self, page_no: int) -> PDFPage:
        """Get 1-based page."""
        if 1 <= page_no <= self.page_count:
            return self.pages[page_no - 1]
        raise IndexError(f"Page {page_no} out of range (1..{self.page_count})")

    def get_text_range(self, start_page: int, end_page: int) -> str:
        """Get combined text from start_page to end_page (inclusive, 1-based)."""
        texts = []
        for p in range(max(1, start_page), min(self.page_count, end_page) + 1):
            raw = self.pages[p - 1].text
            # Clean IDX PDF formatting artifact where empty parentheses prefix numbers: ( )123,456 -> (123,456)
            cleaned = re.sub(r"\(\s*\)\s*([0-9.,]+)", r"(\1)", raw)
            texts.append(f"--- [Page {p}] ---\n" + cleaned)
        return "\n\n".join(texts)

    def _detect_doc_type(self) -> str:
        filename_lower = self.file_path.name.lower()
        if "annualreport" in filename_lower or "annual_report" in filename_lower or "laporan_tahunan" in filename_lower:
            return "annual_report"
        if "financialstatement" in filename_lower or "laporankeuangan" in filename_lower or "lk" in filename_lower:
            if any(term in filename_lower for term in ["q1", "tw1", "triwulan1"]):
                return "fs_interim"
            if any(term in filename_lower for term in ["h1", "sm1", "semester1"]):
                return "fs_interim"
            if any(term in filename_lower for term in ["9m", "tw3", "triwulan3", "q3"]):
                return "fs_interim"
            return "fs_annual"

        # Content heuristics on first 5 pages
        first_pages_text = " ".join(p.text for p in self.pages[:min(5, len(self.pages))]).lower()
        if "laporan tahunan" in first_pages_text and "annual report" in first_pages_text and self.page_count > 150:
            return "annual_report"
        if "[1000000] general information" in first_pages_text or "penyampaian laporan keuangan" in first_pages_text:
            if "tahunan" in first_pages_text or "annual" in first_pages_text:
                return "fs_annual"
            return "fs_interim"
        return "fs_annual"

    def _detect_filing_metadata(self) -> None:
        """Extract ticker, entity name, dates, scale, and currency."""
        # Check parent folder name as fallback for ticker (e.g. examples/nckl/...)
        parent_name = self.file_path.parent.name.upper()
        if len(parent_name) == 4 and parent_name.isalpha():
            self.ticker = parent_name

        first_pages = " \n ".join(p.text for p in self.pages[:min(20, len(self.pages))])

        # Ticker detection
        m_ticker = re.search(r"Kode\s+Emiten\s*[:\n]\s*([A-Z0-9]{4})", first_pages, re.IGNORECASE)
        if m_ticker:
            self.ticker = m_ticker.group(1).upper()
        elif not self.ticker:
            m_ticker2 = re.search(r"\b([A-Z]{4})\b", self.file_path.stem)
            if m_ticker2:
                self.ticker = m_ticker2.group(1).upper()

        # Legal entity name
        m_name = re.search(r"Nama\s+Emiten\s*[:\n]\s*([^\n\r]+)", first_pages, re.IGNORECASE)
        if m_name:
            self.legal_name = m_name.group(1).strip()
        else:
            m_pt = re.search(r"(PT\s+[A-Za-z\s]+Tbk\.?)", first_pages)
            if m_pt:
                self.legal_name = m_pt.group(1).strip().replace("\n", " ")

        # Currency detection
        m_curr = re.search(r"Mata\s+uang\s+pelaporan\s*[:\n]?\s*([^\n\r]+)", first_pages, re.IGNORECASE)
        if m_curr:
            curr_str = m_curr.group(1).lower()
            if any(term in curr_str for term in ["dollar", "usd", "dolar"]):
                self.currency = "USD"
            elif any(term in curr_str for term in ["rupiah", "idr"]):
                self.currency = "IDR"
        elif any(term in first_pages.lower() for term in ["dollar amerika", "dolar amerika", "us dollar", "/ usd"]):
            self.currency = "USD"
        elif any(term in first_pages.lower() for term in ["rupiah / idr", "dalam mata uang rupiah", "/ idr"]):
            self.currency = "IDR"

        # Scale detection
        if any(term in first_pages.lower() for term in ["jutaan / in million", "dalam jutaan rupiah", "in million idr", "in million"]):
            self.scale = 1_000_000
            self.scale_raw = "Dalam jutaan Rupiah / In million IDR"
        elif any(term in first_pages.lower() for term in ["ribuan / in thousand", "dalam ribuan", "in thousand"]):
            self.scale = 1_000
            self.scale_raw = "Dalam ribuan / In thousand"
        elif any(term in first_pages.lower() for term in ["miliar / in billion", "dalam miliaran", "in billion"]):
            self.scale = 1_000_000_000
            self.scale_raw = "Dalam miliar / In billion"
        else:
            self.scale = 1
            self.scale_raw = "Satuan penuh / As reported"

        # Period / Date detection
        m_year = re.search(r"(?:31\s+Desember|December\s+31|berakhir\s+pada\s+31/12/)\s*(20\d\d)", first_pages, re.IGNORECASE)
        if m_year:
            self.fiscal_year = int(m_year.group(1))
            self.period_end = f"{self.fiscal_year}-12-31"
            self.fiscal_period = "FY"
        else:
            # Check for interim periods: 30 September (9M), 30 Juni (H1), 31 Maret (Q1)
            m_9m = re.search(r"30\s+(?:September|September)\s*(20\d\d)", first_pages, re.IGNORECASE)
            m_h1 = re.search(r"30\s+(?:Juni|June)\s*(20\d\d)", first_pages, re.IGNORECASE)
            m_q1 = re.search(r"31\s+(?:Maret|March)\s*(20\d\d)", first_pages, re.IGNORECASE)
            if m_9m:
                self.fiscal_year = int(m_9m.group(1))
                self.period_end = f"{self.fiscal_year}-09-30"
                self.fiscal_period = "9M"
            elif m_h1:
                self.fiscal_year = int(m_h1.group(1))
                self.period_end = f"{self.fiscal_year}-06-30"
                self.fiscal_period = "H1"
            elif m_q1:
                self.fiscal_year = int(m_q1.group(1))
                self.period_end = f"{self.fiscal_year}-03-31"
                self.fiscal_period = "Q1"

        # Number locale detection: check occurrences of 1,234.56 vs 1.234,56 in statement tables
        sample_text = " ".join(p.text for p in self.pages[min(3, len(self.pages)):min(25, len(self.pages))])
        # Look for patterns with commas vs periods as decimal separators
        # e.g., 14,159,490 (en) vs 14.159.490 (id)
        en_pattern_count = len(re.findall(r"\b\d{1,3},\d{3},\d{3}\b", sample_text))
        id_pattern_count = len(re.findall(r"\b\d{1,3}\.\d{3}\.\d{3}\b", sample_text))
        if en_pattern_count > id_pattern_count:
            self.number_locale = "en"
        else:
            self.number_locale = "id"

    def extract_metadata_with_llm(self, llm_client: Any) -> bool:
        """
        LLM-first metadata extraction from cover and general information pages.
        Extracts ticker, legal_name, doc_type, fiscal_year, period_end, currency, scale,
        number_locale, presentation_format, and accounting_regime.
        """
        if not llm_client:
            return False

        from src.llm.prompts import FILING_METADATA_PROMPT

        cover_text = "\n\n".join(f"--- PAGE {p+1} ---\n{self.pages[p].text}" for p in range(min(3, len(self.pages))))
        prompt = FILING_METADATA_PROMPT.format(page_text=cover_text[:10000])

        try:
            meta = llm_client.complete_json(prompt, system="You are an expert IDX filing indexer.")
            if meta.get("ticker"):
                self.ticker = str(meta["ticker"]).upper()
            if meta.get("legal_name"):
                self.legal_name = str(meta["legal_name"])
            if meta.get("doc_type"):
                self.doc_type = str(meta["doc_type"])
            if meta.get("fiscal_year"):
                self.fiscal_year = int(meta["fiscal_year"])
            if meta.get("period_end"):
                self.period_end = str(meta["period_end"])
            if meta.get("currency"):
                self.currency = str(meta["currency"]).upper()
            if meta.get("scale"):
                self.scale = int(meta["scale"])
            if meta.get("number_locale"):
                self.number_locale = str(meta["number_locale"])
            if meta.get("presentation_format"):
                self.presentation_format = str(meta["presentation_format"])
            if meta.get("accounting_regime"):
                self.accounting_regime = str(meta["accounting_regime"])
            return True
        except Exception:
            return False

    def close(self) -> None:
        self._fitz_doc.close()

    def __repr__(self) -> str:
        return (
            f"<PDFDocument ticker={self.ticker} type={self.doc_type} "
            f"period={self.fiscal_period}{self.fiscal_year} pages={self.page_count} "
            f"currency={self.currency} scale={self.scale} locale={self.number_locale}>"
        )
