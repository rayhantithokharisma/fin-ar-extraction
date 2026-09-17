"""
Intelligent Sectionizer for IDX Financial Statements and Annual Reports.
Identifies logical boundaries and maps them to extraction ROUTES.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.parser.pdf_loader import PDFDocument


@dataclass
class SectionSpan:
    section_id: str
    title: str
    start_page: int  # 1-based
    end_page: int    # 1-based inclusive
    section_type: str  # Keys into extraction_contracts.ROUTES
    form_code: Optional[str] = None
    note_number: Optional[str] = None
    topic: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"<SectionSpan {self.section_id} [{self.section_type}] "
            f"pages={self.start_page}-{self.end_page} title='{self.title[:35]}...'>"
        )


class Sectionizer:
    """Partitions LK or AR documents into targeted extractable sections."""

    @classmethod
    def resolve_idx_code(cls, code: str, title: str) -> tuple[str, str, str]:
        """
        Maps a 7-digit IDX taxonomy form code to (section_type, default_title, topic).
        The first digit indicates industry sector (1: General, 3: Infrastructure, 4: Banking).
        The last 6 digits identify the financial statement or disclosure.
        """
        if code == "1000000":
            return ("lk.auditor_report", "General information & Audit Report", "audit_report")

        tail6 = code[1:]

        # Face statements across all industries (IDX XBRL taxonomy second-digit semantics)
        if tail6.startswith("2"):
            return ("lk.statement", "Statement of financial position (Balance Sheet)", "balance_sheet")
        if tail6.startswith("3"):
            return ("lk.statement", "Statement of profit or loss and OCI", "income_statement")
        if tail6.startswith("4"):
            return ("lk.statement", "Statement of changes in equity", "changes_in_equity")
        if tail6.startswith("5"):
            return ("lk.statement", "Statement of cash flows", "cash_flow")

        # Core note disclosures
        if tail6.startswith("610000"):
            return ("lk.note.policies", "Significant Accounting Policies", "accounting_policies")
        if tail6.startswith("611100"):
            return ("lk.note.table", "Notes: Property, Plant and Equipment", "ppe_rollforward")
        if tail6.startswith("612100"):
            return ("lk.note.table", "Notes: Right of Use Assets", "rou_rollforward")
        if tail6.startswith("616100"):
            return ("lk.note.table", "Notes: Revenue Disaggregation", "revenue_disaggregation")
        if tail6.startswith("621000"):
            return ("lk.note.table", "Notes: Trade Receivables", "receivables_aging")
        if tail6.startswith("630000") or tail6.startswith("632000"):
            return ("lk.note.table", "Notes: Inventories", "inventory")
        if tail6.startswith("640100") or tail6.startswith("641000"):
            return ("lk.note.table", "Notes: Trade Payables", "cost_breakdown")
        if tail6.startswith("670000") or tail6.startswith("671000"):
            return ("lk.note.table", "Notes: Cost of Goods Sold / Revenue", "cost_breakdown")
        if tail6.startswith("691100"):
            return ("lk.note.borrowings", "Notes: Long-Term Borrowings", "borrowings")
        if tail6.startswith("693100"):
            return ("lk.note.borrowings", "Notes: Short-Term Borrowings", "borrowings")

        # Banking specific disclosures
        if tail6.startswith("631100") or tail6.startswith("631110"):
            return ("lk.note.table", "Notes: Bank Interest and Financing Income", "interest_income")
        if tail6.startswith("632100") or tail6.startswith("632110"):
            return ("lk.note.table", "Notes: Bank Interest and Financing Expense", "interest_expense")
        if tail6.startswith("614100") or tail6.startswith("614110"):
            return ("lk.note.table", "Notes: Bank Loans Gross & ECL", "bank_loans")
        if tail6.startswith("624100") or tail6.startswith("624110"):
            return ("lk.note.table", "Notes: Bank Customer Deposits (DPK)", "customer_deposits")

        return ("lk.note.table", title or f"Notes: Disclosure {code}", "other")

    def __init__(self, doc: PDFDocument, llm_client: Optional[Any] = None):
        self.doc = doc
        self.llm_client = llm_client

    def sectionize(self) -> list[SectionSpan]:
        if self.doc.doc_type in ("fs_annual", "fs_interim"):
            return self._sectionize_lk()
        if self.llm_client:
            llm_spans = self._sectionize_ar_with_llm()
            if llm_spans:
                return llm_spans
        return self._sectionize_ar()

    def _sectionize_ar_with_llm(self) -> list[SectionSpan]:
        """
        LLM-first sectionizer: Parses the Table of Contents (TOC) from the first 10 pages
        to identify chapter boundaries without scanning 800+ pages.
        """
        if not self.llm_client:
            return []

        from src.llm.prompts import TOC_EXTRACTION_PROMPT
        import json

        toc_pages = []
        for p in self.doc.pages[:min(12, len(self.doc.pages))]:
            txt_lower = p.text.lower()
            if any(term in txt_lower for term in ["daftar isi", "table of contents", "contents", "ikhtisar"]):
                toc_pages.append(f"--- PAGE {p.page_no} ---\n{p.text}")

        if not toc_pages:
            return []

        prompt = TOC_EXTRACTION_PROMPT.format(page_text="\n\n".join(toc_pages)[:12000])

        try:
            res = self.llm_client.complete_json(prompt, system="You are an expert indexer of Indonesian corporate filings.")
            sections = res.get("sections", [])
            spans: list[SectionSpan] = []
            for s in sections:
                sec_type = s.get("section_type")
                title = s.get("title", sec_type)
                sp = int(s.get("start_page", 1))
                ep = int(s.get("end_page", sp + 10))
                if 1 <= sp <= self.doc.page_count:
                    spans.append(
                        SectionSpan(
                            section_id=f"ar.{sec_type.split('.')[-1]}.p{sp}",
                            title=title,
                            start_page=sp,
                            end_page=min(self.doc.page_count, max(sp, ep)),
                            section_type=sec_type,
                        )
                    )
            return spans
        except Exception:
            return []

    def _sectionize_lk(self) -> list[SectionSpan]:
        """Detect standard IDX form codes in LK documents."""
        detected: list[tuple[int, str, str]] = []  # (page_no, code, raw_title)

        for p in self.doc.pages:
            matches = re.findall(r"\[(\d{7})\]\s*([^\n\r]+)", p.text)
            for code, raw_title in matches:
                clean_title = raw_title.strip()
                detected.append((p.page_no, code, clean_title))

        if not detected:
            # Fallback to text header matching if form codes missing
            return self._sectionize_lk_fallback()

        # Consolidate duplicate headers on the same page
        unique_detected: list[tuple[int, str, str]] = []
        seen = set()
        for page_no, code, title in detected:
            key = (page_no, code)
            if key not in seen:
                seen.add(key)
                unique_detected.append((page_no, code, title))

        spans: list[SectionSpan] = []
        for i, (page_no, code, title) in enumerate(unique_detected):
            next_page = unique_detected[i + 1][0] if i + 1 < len(unique_detected) else self.doc.page_count + 1
            end_page = max(page_no, next_page - 1 if next_page > page_no else page_no)

            section_type, default_title, topic = self.resolve_idx_code(code, title)

            span = SectionSpan(
                section_id=f"lk.{code}.p{page_no}",
                title=title or default_title,
                start_page=page_no,
                end_page=end_page,
                section_type=section_type,
                form_code=code,
                topic=topic,
            )
            spans.append(span)

        return spans

    def _sectionize_lk_fallback(self) -> list[SectionSpan]:
        """Fallback sectionizer using standard Indonesian financial statement titles."""
        headings = [
            (r"laporan posisi keuangan|statement of financial position", "lk.statement", "balance_sheet", "Balance Sheet"),
            (r"laporan laba rugi|statement of profit or loss", "lk.statement", "income_statement", "Income Statement"),
            (r"laporan perubahan ekuitas|statement of changes in equity", "lk.statement", "changes_in_equity", "Changes in Equity"),
            (r"laporan arus kas|statement of cash flows", "lk.statement", "cash_flow", "Cash Flows"),
            (r"catatan atas laporan keuangan|notes to the (?:consolidated )?financial statements", "lk.note.table", "notes", "Notes"),
        ]
        spans: list[SectionSpan] = []
        # Look for these titles across the document
        return spans

    def _sectionize_ar(self) -> list[SectionSpan]:
        """Partition Annual Report into key chapters and sections."""
        spans: list[SectionSpan] = []

        # Chapter 04 MD&A banner regex
        mda_banner_regex = re.compile(
            r"analisis\s+dan\s+pembahasan\s+manajemen|management[\x27\s]*s?\s+discussion|analisis\s+dan\s+diskusi\s+manajemen",
            re.IGNORECASE
        )

        # Patterns for deep MD&A operational chapter (Chapter 04 in OJK reporting)
        deep_kpi_regex = re.compile(
            r"tinjauan\s+kinerja\s+operasional|operational\s+performance\s+review|"
            r"tinjauan\s+operasi(?:on)?\s+per\s+segmen|operational\s+review\s+of\s+each\s+business\s+segment|"
            r"operating\s+performance(?:\s+by\s+business\s+segment)?|business\s+segment\s+performance|kinerja\s+segmen|"
            r"tinjauan\s+operasional|operational\s+overview|operational\s+review|"
            r"business\s+and\s+operations\s+review|review\s+of\s+operations|"
            r"segmen\s+operasional|segmen\s+operasi|operating\s+segment|kinerja\s+usaha|"
            r"kinerja\s+operasi(?:onal)?|kinerja\s+produksi\s+dan\s+penjualan|"
            r"target\s+dan\s+realisasi\s+kinerja\s+produksi|kinerja\s+komoditas|"
            r"mining\s+and\s+processing|penambangan\s+dan\s+pengolahan|penambangan\s+dan\s+pemrosesan|"
            r"trading\s+and\s+distribution|perdagangan\s+dan\s+distribusi|"
            r"pabrik\s+kelapa\s+sawit|perkebunan\s+kelapa\s+sawit|"
            r"kapasitas\s+pembangkitan|efisiensi\s+pembangkitan",
            re.IGNORECASE
        )

        patterns = [
            ("ar.highlights", "Financial Highlights", r"ikhtisar keuangan|financial highlights|ikhtisar kinerja|performance overview", "Ikhtisar Keuangan"),
            ("ar.governance.officers", "Board and Governance Profiles", r"profil direksi|profile of the board of directors|sambutan direksi|board of directors' statement|laporan dewan komisaris|profil dewan komisaris", "Profil Direksi"),
            ("ar.mdna.outlook", "MD&A Outlook and Plans", r"prospek dan rencana ke depan|outlook and future plans|rencana keberlanjutan|target dan realisasi", "Prospek dan Rencana Ke Depan"),
            ("ar.mdna.drivers", "MD&A Financial Overview", r"tinjauan keuangan|financial review|financial overview|kinerja keuangan", "Tinjauan Keuangan"),
            ("ar.shareholders", "Shareholders Information", r"informasi pemegang saham|shareholders information", "Informasi Pemegang Saham"),
            ("ar.risk", "Risk Management", r"manajemen risiko|risk management", "Manajemen Risiko"),
            ("sr.esg_metrics", "Sustainability & Responsible Mining", r"manajemen keberlanjutan|sustainability management|tata kelola keberlanjutan|kinerja keberlanjutan", "Manajemen Keberlanjutan"),
            ("lk.auditor_report", "Audited Financial Statements", r"laporan auditor independen|independent auditor['’]s report", "Laporan Auditor Independen"),
        ]

        found_pages: list[tuple[int, str, str, str]] = []
        deep_kpi_pages: list[tuple[int, str, str, str, int]] = []

        for idx, page in enumerate(self.doc.pages):
            p_no = idx + 1
            text_lower = page.text.lower()
            header_text = page.text[:1500]

            # Skip TOC pages in early report (first 35 pages)
            if p_no < 35 and any(term in text_lower for term in ["table of content", "daftar isi"]):
                continue
            if idx < 5:
                continue

            # Check for deep operational review
            has_mda = bool(mda_banner_regex.search(header_text))
            has_op = bool(deep_kpi_regex.search(header_text))

            # Exclude early board reports / management letters (pages < 50), FS notes, and appendix compliance checklists
            is_early_letter = p_no < 50
            is_appendix = bool(re.search(r"tabel\s+kesesuaian|pedoman\s+penyusunan|indeks\s+gri|indeks\s+ojk|checklist|acuan\s+pengungkapan", header_text, re.IGNORECASE)) or p_no > self.doc.page_count - 25
            is_fs_notes = bool(re.search(r"catatan\s+atas\s+laporan\s+keuangan|notes\s+to\s+(the\s+)?(consolidated\s+)?financial\s+statements", header_text, re.IGNORECASE))

            if has_op and not is_early_letter and not is_appendix and not is_fs_notes:
                score = 2 if has_mda else 1
                deep_kpi_pages.append((p_no, "ar.mdna.kpis", "MD&A Operational Review", "Tinjauan Kinerja Operasional", score))

            for sec_type, title, pattern, id_label in patterns:
                if re.search(pattern, header_text, re.IGNORECASE):
                    found_pages.append((p_no, sec_type, title, id_label))

        # Select the best deep operational review page (preferring MD&A chapter banner at p_no >= 50)
        chosen_kpi = None
        mda_kpis = [p for p in deep_kpi_pages if p[0] >= 50]
        if mda_kpis:
            # First preference: pages that are explicitly inside MD&A chapter (score == 2)
            golden = [p for p in mda_kpis if p[4] == 2]
            if golden:
                chosen_kpi = (golden[0][0], golden[0][1], golden[0][2], golden[0][3])
            else:
                chosen_kpi = (mda_kpis[0][0], mda_kpis[0][1], mda_kpis[0][2], mda_kpis[0][3])
        elif deep_kpi_pages:
            chosen_kpi = (deep_kpi_pages[0][0], deep_kpi_pages[0][1], deep_kpi_pages[0][2], deep_kpi_pages[0][3])


        # Filter unique sections, preferring the earliest occurrence for governance & highlights,
        # but preserving our chosen deep MD&A operational chapter
        clean_found: list[tuple[int, str, str, str]] = []
        used_sections = set()

        # Add chosen KPI first so it is never shadowed
        if chosen_kpi:
            clean_found.append(chosen_kpi)
            used_sections.add("ar.mdna.kpis")


        for p_no, sec_type, title, id_label in sorted(found_pages, key=lambda x: x[0]):
            if sec_type not in used_sections:
                used_sections.add(sec_type)
                clean_found.append((p_no, sec_type, title, id_label))

        clean_found.sort(key=lambda x: x[0])
        for i, (p_no, sec_type, title, id_label) in enumerate(clean_found):
            next_p = clean_found[i + 1][0] if i + 1 < len(clean_found) else min(self.doc.page_count, p_no + 25)

            if sec_type == "ar.mdna.kpis":
                # Give operational review up to 35 pages of breadth to capture all segment tables
                end_p = max(p_no, min(p_no + 35, next_p - 1))
            elif sec_type == "lk.auditor_report":
                end_p = self.doc.page_count  # Embedded LK continues to the end of the doc
            else:
                end_p = max(p_no, min(p_no + 15, next_p - 1))

            span = SectionSpan(
                section_id=f"ar.{sec_type.split('.')[-1]}.p{p_no}",
                title=title,
                start_page=p_no,
                end_page=end_p,
                section_type=sec_type,
            )
            spans.append(span)

        return spans
